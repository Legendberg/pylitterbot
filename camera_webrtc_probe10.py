"""Probe WebRTC signaling - Round 10.

BREAKTHROUGH from Round 9: `accessToken` query param causes 500!
The server RECOGNIZES it and tries to process the session JWT, but crashes.

This round:
1. Systematically vary `accessToken` with other params to find the right combo
2. Try proper CORS preflight (OPTIONS with required headers)
3. Try `householdId` from Cognito token
4. Try WebSocket on `/api/signaling` with combined auth
5. Try generating session, immediately connecting WS, then hitting REST trigger
6. Explore if `/api/signaling/connect` (504 in R9) accepts WebSocket upgrade
"""

import asyncio
import base64
import configparser
import json
import logging
import sys
import time
from pathlib import Path

import aiohttp

from pylitterbot import Account

WATFORD_API = "https://watford.ienso-dev.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent / "camera_webrtc_probe10_results.log", mode="w"
        ),
    ],
)
_LOGGER = logging.getLogger(__name__)


def load_credentials():
    c = configparser.ConfigParser()
    c.read(Path.home() / ".config" / "whisker" / "credentials")
    return c.get("whisker", "email"), c.get("whisker", "password")


async def generate_session(websession, headers, device_id, auto_start=True):
    url = f"{WATFORD_API}/api/device-manager/client/generate-session/{device_id}"
    if auto_start:
        url += "?autoStart=true"
    async with websession.get(url, headers=headers) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"generate-session: {resp.status} {body}")
        return await resp.json()


async def try_req(session, method, url, headers=None, body=None, ct=None, label=""):
    req_headers = dict(headers) if headers else {}
    if ct:
        req_headers["Content-Type"] = ct

    try:
        kwargs = {"headers": req_headers}
        if body:
            kwargs["data"] = body

        async with session.request(method, url, **kwargs) as resp:
            resp_body = await resp.text()
            resp_hdrs = dict(resp.headers)
            status = resp.status

            is_interesting = status not in (400, 404)
            marker = ">>>" if is_interesting else "   "
            _LOGGER.info(f"  {marker} [{label}] {method} → {status}: {resp_body[:300]}")
            if is_interesting:
                for k, v in resp_hdrs.items():
                    if k.lower() in ("allow", "access-control-allow-methods",
                                     "access-control-allow-headers", "content-type",
                                     "x-amzn-errortype", "location", "www-authenticate"):
                        _LOGGER.info(f"      {k}: {v}")
            return status, resp_body, resp_hdrs
    except Exception as e:
        _LOGGER.info(f"  !!! [{label}] {method} ERROR: {e}")
        return 0, str(e), {}


async def recv(ws, timeout=5, label=""):
    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
        if msg.type == aiohttp.WSMsgType.TEXT:
            data = msg.data
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                pass
            _LOGGER.info(f"  [{label}] GOT: {json.dumps(data, default=str)[:300]}")
            return data
        elif msg.type == aiohttp.WSMsgType.BINARY:
            _LOGGER.info(f"  [{label}] BINARY: {msg.data[:100].hex()}")
            return msg.data
        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
            _LOGGER.info(f"  [{label}] CLOSE: code={getattr(ws, 'close_code', '?')}")
            return None
    except asyncio.TimeoutError:
        _LOGGER.info(f"  [{label}] (timeout {timeout}s)")
        return None


async def main():
    _LOGGER.info("=" * 60)
    _LOGGER.info("WebRTC Signaling Probe - Round 10 (Exploiting 500 Error)")
    _LOGGER.info("=" * 60)

    username, password = load_credentials()
    account = Account()

    try:
        await account.connect(username=username, password=password, load_robots=True, load_pets=False)

        lr5_pro = None
        for robot in account.robots:
            if hasattr(robot, "camera_metadata") and robot.camera_metadata:
                lr5_pro = robot
                break
        if not lr5_pro:
            _LOGGER.error("No LR5 Pro found")
            return

        device_id = lr5_pro.camera_metadata["deviceId"]
        space_id = lr5_pro.camera_metadata.get("spaceId", "")
        _LOGGER.info(f"Device: {device_id}, Space: {space_id}")

        # Get Cognito identity details
        id_token = await account.session.async_get_id_token()
        parts = id_token.split(".")
        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
        cognito_claims = json.loads(base64.b64decode(payload))
        cognito_sub = cognito_claims.get("sub", "")
        household_id = cognito_claims.get("householdId", "")
        mid = cognito_claims.get("mid", "")
        _LOGGER.info(f"Cognito sub: {cognito_sub}")
        _LOGGER.info(f"Household: {household_id}")
        _LOGGER.info(f"MID: {mid}")

        bearer = {"Authorization": f"Bearer {id_token}"}
        ws_session = aiohttp.ClientSession()

        try:
            base_sig = f"{WATFORD_API}/api/device-manager/client/signaling"
            base_ses = f"{WATFORD_API}/api/device-manager/client/sessions"
            base_dev = f"{WATFORD_API}/api/device-manager/client/devices"

            # ─── TEST 1: CORS Preflight with proper headers ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 1: Proper CORS preflight ───")
            cors_headers = {
                "Origin": "https://app.whisker.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization,Content-Type",
            }
            for url, label in [
                (base_sig, "sig"), (base_ses, "ses"), (base_dev, "dev"),
            ]:
                s, b, h = await try_req(ws_session, "OPTIONS", url, headers=cors_headers, label=f"cors-{label}")
                if s not in (400, 404):
                    _LOGGER.info(f"  Full CORS response headers:")
                    for k, v in h.items():
                        _LOGGER.info(f"    {k}: {v}")
                await asyncio.sleep(0.3)

            # Also try POST preflight
            cors_post = {
                "Origin": "https://app.whisker.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Content-Type",
            }
            await try_req(ws_session, "OPTIONS", base_sig, headers=cors_post, label="cors-sig-post")
            await asyncio.sleep(0.3)

            # ─── TEST 2: Fresh session + immediate accessToken test ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 2: Fresh session + immediate accessToken variations ───")
            id_token = await account.session.async_get_id_token()
            bearer = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, bearer, device_id)
            sid = session_data["sessionId"]
            stok = session_data["sessionToken"]
            _LOGGER.info(f"Fresh session: {sid}")

            # Systematic accessToken combinations
            at_combos = [
                # Base: just accessToken (we know this gives 500)
                (f"accessToken={stok}", "just-at"),
                # With deviceId
                (f"accessToken={stok}&deviceId={device_id}", "at+did"),
                # With sessionId
                (f"accessToken={stok}&sessionId={sid}", "at+sid"),
                # With both
                (f"accessToken={stok}&sessionId={sid}&deviceId={device_id}", "at+sid+did"),
                # With spaceId
                (f"accessToken={stok}&spaceId={space_id}", "at+space"),
                # With householdId
                (f"accessToken={stok}&householdId={household_id}", "at+hh"),
                # With type
                (f"accessToken={stok}&type=webrtc", "at+type"),
                (f"accessToken={stok}&type=signaling", "at+sig"),
                # With action
                (f"accessToken={stok}&action=connect", "at+connect"),
                (f"accessToken={stok}&action=start", "at+start"),
                (f"accessToken={stok}&action=join", "at+join"),
                # With role
                (f"accessToken={stok}&role=client", "at+client"),
                (f"accessToken={stok}&role=viewer", "at+viewer"),
                (f"accessToken={stok}&role=master", "at+master"),
                # With autoStart
                (f"accessToken={stok}&autoStart=true", "at+auto"),
            ]

            for params, label in at_combos:
                await try_req(ws_session, "GET", f"{base_sig}?{params}",
                              headers=bearer, label=label)
                await asyncio.sleep(0.2)

            # ─── TEST 3: Try the 3 endpoints with householdId ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 3: householdId parameter ───")
            hh_combos = [
                (f"householdId={household_id}", "just-hh"),
                (f"householdId={household_id}&deviceId={device_id}", "hh+did"),
                (f"householdId={household_id}&spaceId={space_id}", "hh+space"),
                (f"householdId={household_id}&sessionId={sid}", "hh+sid"),
            ]
            for params, label in hh_combos:
                for base, ep in [(base_sig, "sig"), (base_ses, "ses"), (base_dev, "dev")]:
                    await try_req(ws_session, "GET", f"{base}?{params}",
                                  headers=bearer, label=f"{ep}-{label}")
                    await asyncio.sleep(0.1)

            # ─── TEST 4: Try WebSocket upgrade on /api/signaling sub-paths ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 4: WebSocket on /api/signaling sub-paths ───")
            ws_sub_paths = [
                f"wss://watford.ienso-dev.com/api/signaling/connect?accessToken={stok}",
                f"wss://watford.ienso-dev.com/api/signaling/{sid}?accessToken={stok}",
                f"wss://watford.ienso-dev.com/api/signaling/{device_id}?accessToken={stok}",
                f"wss://watford.ienso-dev.com/api/signaling/offer?accessToken={stok}",
            ]
            for url in ws_sub_paths:
                short = url.split("?")[0].split("/api/")[-1]
                _LOGGER.info(f"  Trying WS: {short}")
                try:
                    ws = await ws_session.ws_connect(url)
                    _LOGGER.info(f"  >>> WS CONNECTED to {short}!")
                    # Listen for any messages
                    await recv(ws, 5, short)
                    try:
                        await ws.close()
                    except Exception:
                        pass
                except aiohttp.WSServerHandshakeError as e:
                    _LOGGER.info(f"  WS rejected: {e.status} {e.message}")
                except Exception as e:
                    _LOGGER.info(f"  WS error: {e}")
                await asyncio.sleep(0.5)

            # ─── TEST 5: Generate session + connect WS + then call REST ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 5: WS connected THEN call REST endpoints ───")
            _LOGGER.info("Hypothesis: REST endpoint triggers camera when WS is active")
            id_token = await account.session.async_get_id_token()
            bearer = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, bearer, device_id)
            sid = session_data["sessionId"]
            stok = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={stok}"

            # Connect to signaling WS first
            ws = await ws_session.ws_connect(ws_url)
            _LOGGER.info(f"WS connected. Session: {sid}")

            # Now call REST endpoints that might trigger the camera
            _LOGGER.info("Calling REST endpoints while WS connected...")

            # These gave 500 before - maybe they trigger the camera?
            s1, _, _ = await try_req(ws_session, "GET",
                                     f"{base_sig}?accessToken={stok}&sessionId={sid}&deviceId={device_id}",
                                     headers=bearer, label="trigger-sig")
            s2, _, _ = await try_req(ws_session, "GET",
                                     f"{base_ses}?accessToken={stok}&sessionId={sid}",
                                     headers=bearer, label="trigger-ses")
            s3, _, _ = await try_req(ws_session, "GET",
                                     f"{base_dev}?accessToken={stok}&deviceId={device_id}",
                                     headers=bearer, label="trigger-dev")

            # Now listen on WS for camera connection
            _LOGGER.info("Listening for camera on WS after REST triggers (20s)...")
            deadline = time.monotonic() + 20
            msg_count = 0
            while time.monotonic() < deadline:
                r = await recv(ws, min(deadline - time.monotonic(), 5), f"post-trigger-{msg_count}")
                if r is None:
                    continue
                msg_count += 1
                if isinstance(r, dict) and r.get("type"):
                    _LOGGER.info(f">>> GOT MESSAGE TYPE: {r['type']} <<<")
            _LOGGER.info(f"After trigger: {msg_count} messages")

            try:
                await ws.close()
            except Exception:
                pass

            # ─── TEST 6: Try dual-auth on WS (both Bearer + session token) ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 6: WS with extra auth headers ───")
            id_token = await account.session.async_get_id_token()
            bearer = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, bearer, device_id)
            sid = session_data["sessionId"]
            stok = session_data["sessionToken"]

            ws_auth_urls = [
                # Normal WS URL with Bearer in headers
                (f"wss://watford.ienso-dev.com/api/signaling?accessToken={stok}",
                 {"Authorization": f"Bearer {id_token}"},
                 "ws+bearer"),
                # WS URL with both tokens
                (f"wss://watford.ienso-dev.com/api/signaling?accessToken={stok}&Authorization=Bearer%20{id_token}",
                 None,
                 "ws+auth-param"),
                # WS with deviceId in URL
                (f"wss://watford.ienso-dev.com/api/signaling?accessToken={stok}&deviceId={device_id}",
                 None,
                 "ws+deviceId"),
                # WS with sessionId in URL
                (f"wss://watford.ienso-dev.com/api/signaling?accessToken={stok}&sessionId={sid}",
                 None,
                 "ws+sessionId"),
                # WS with role
                (f"wss://watford.ienso-dev.com/api/signaling?accessToken={stok}&role=client",
                 None,
                 "ws+role-client"),
                (f"wss://watford.ienso-dev.com/api/signaling?accessToken={stok}&role=viewer",
                 None,
                 "ws+role-viewer"),
                (f"wss://watford.ienso-dev.com/api/signaling?accessToken={stok}&X-Amz-Security-Token=dummy",
                 None,
                 "ws+amz-token"),
            ]

            for url, hdrs, label in ws_auth_urls:
                _LOGGER.info(f"  Testing: {label}")
                try:
                    if hdrs:
                        ws_test = await ws_session.ws_connect(url, headers=hdrs)
                    else:
                        ws_test = await ws_session.ws_connect(url)
                    _LOGGER.info(f"  >>> {label}: CONNECTED")
                    # Send a test message and listen
                    await ws_test.send_str(json.dumps({"type": "offer", "test": True}))
                    r = await recv(ws_test, 5, label)
                    try:
                        await ws_test.close()
                    except Exception:
                        pass
                except aiohttp.WSServerHandshakeError as e:
                    _LOGGER.info(f"  {label}: Rejected {e.status}")
                except Exception as e:
                    _LOGGER.info(f"  {label}: Error {e}")
                await asyncio.sleep(0.5)

            # ─── TEST 7: Try generate-session on the camera API domains ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 7: generate-session on camera API domains ───")
            cam_apis = [
                "https://rrntg65uwf.execute-api.us-east-1.amazonaws.com",
                "https://7mnuil943l.execute-api.us-east-1.amazonaws.com",
            ]
            id_token = await account.session.async_get_id_token()
            bearer = {"Authorization": f"Bearer {id_token}"}

            for api in cam_apis:
                api_label = api.split("//")[1].split(".")[0]
                paths = [
                    f"/prod/v1/cameras/{device_id}/signaling",
                    f"/prod/v1/cameras/{device_id}/session",
                    f"/prod/v1/cameras/{device_id}/webrtc",
                    f"/prod/v1/cameras/{device_id}/stream",
                    f"/prod/v1/cameras/{device_id}/connect",
                    f"/prod/v1/cameras/{device_id}/live",
                    f"/prod/v1/cameras/{device_id}/call",
                    f"/prod/v1/signaling/generate-session",
                    f"/prod/v1/signaling/{device_id}",
                    f"/prod/v1/session/generate/{device_id}",
                ]
                for path in paths:
                    s, b, h = await try_req(ws_session, "GET", f"{api}{path}",
                                            headers=bearer, label=f"{api_label}{path.split('/')[-1]}")
                    # If GET gives 403 or interesting, try POST
                    if s in (403, 405):
                        await try_req(ws_session, "POST", f"{api}{path}",
                                      headers=bearer,
                                      body=json.dumps({"deviceId": device_id, "autoStart": True}),
                                      ct="application/json",
                                      label=f"POST-{api_label}{path.split('/')[-1]}")
                    await asyncio.sleep(0.15)

            # ─── TEST 8: Try AWS IoT endpoints ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 8: Check for AWS IoT / MQTT WebSocket endpoints ───")
            iot_urls = [
                f"wss://watford.ienso-dev.com/mqtt?accessToken={stok}",
                f"wss://watford.ienso-dev.com/api/mqtt?accessToken={stok}",
                f"wss://watford.ienso-dev.com/api/iot?accessToken={stok}",
            ]
            for url in iot_urls:
                short = url.split("?")[0].split("//")[1]
                try:
                    ws_iot = await ws_session.ws_connect(url)
                    _LOGGER.info(f"  >>> CONNECTED to {short}!")
                    r = await recv(ws_iot, 3, short)
                    try:
                        await ws_iot.close()
                    except Exception:
                        pass
                except aiohttp.WSServerHandshakeError as e:
                    _LOGGER.info(f"  {short}: Rejected {e.status}")
                except Exception as e:
                    _LOGGER.info(f"  {short}: Error {e}")
                await asyncio.sleep(0.3)

            # ─── TEST 9: Send SDP offer directly on WS + listen longer ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 9: Send real offer on WS, listen 30s for camera ───")
            from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer

            id_token = await account.session.async_get_id_token()
            bearer = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, bearer, device_id)
            sid = session_data["sessionId"]
            stok = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={stok}"

            turn = session_data["turnServer"]
            ice_servers = []
            if turn.get("stunUrl"):
                ice_servers.append(RTCIceServer(urls=[turn["stunUrl"]]))
            turn_urls = turn.get("turnUrl", [])
            if isinstance(turn_urls, str):
                turn_urls = [turn_urls]
            if turn_urls:
                ice_servers.append(RTCIceServer(
                    urls=turn_urls, username=turn["username"], credential=turn["password"],
                ))
            config = RTCConfiguration(iceServers=ice_servers)

            pc = RTCPeerConnection(configuration=config)
            pc.addTransceiver("video", direction="recvonly")
            pc.addTransceiver("audio", direction="recvonly")

            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            if pc.iceGatheringState != "complete":
                done = asyncio.Event()
                @pc.on("icegatheringstatechange")
                def on_ice():
                    if pc.iceGatheringState == "complete":
                        done.set()
                try:
                    await asyncio.wait_for(done.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass

            sdp = pc.localDescription.sdp
            _LOGGER.info(f"SDP offer: {len(sdp)} chars")

            # Connect WS
            ws = await ws_session.ws_connect(ws_url)
            _LOGGER.info(f"WS connected. Session: {sid}")

            # Send offer
            await ws.send_str(json.dumps({"type": "offer", "sdp": sdp}))
            _LOGGER.info("Offer sent. Listening 30s for camera response...")

            # Also hit REST trigger while listening
            await try_req(ws_session, "GET",
                          f"{base_sig}?accessToken={stok}",
                          headers=bearer, label="trigger-while-ws")

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                r = await recv(ws, min(deadline - time.monotonic(), 5), "camera-resp")
                if r is None:
                    continue
                if isinstance(r, dict):
                    if r.get("type") == "answer":
                        _LOGGER.info(">>> CAMERA SENT ANSWER! <<<")
                    elif r.get("type") == "candidate":
                        _LOGGER.info(">>> ICE CANDIDATE! <<<")
                    else:
                        _LOGGER.info(f">>> MESSAGE: {json.dumps(r, default=str)[:200]}")

            await pc.close()
            try:
                await ws.close()
            except Exception:
                pass

        finally:
            await ws_session.close()

    finally:
        await account.disconnect()

    _LOGGER.info("")
    _LOGGER.info("Probe round 10 complete.")


if __name__ == "__main__":
    asyncio.run(main())
