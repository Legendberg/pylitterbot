"""Probe WebRTC signaling - Round 4.

Key discovery: /api/device-manager/client/signaling returns 400 (not 404).
This is a REAL endpoint that needs correct parameters.

Hypothesis: Signaling may be REST-based (like WHIP) rather than WebSocket.
The WebSocket at /api/signaling might be for device-side only, or a legacy endpoint.
"""

import asyncio
import base64
import configparser
import json
import logging
import sys
from pathlib import Path

import aiohttp
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer

from pylitterbot import Account

WATFORD_API = "https://watford.ienso-dev.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent / "camera_webrtc_probe4_results.log", mode="w"
        ),
    ],
)
_LOGGER = logging.getLogger(__name__)


def load_credentials():
    c = configparser.ConfigParser()
    c.read(Path.home() / ".config" / "whisker" / "credentials")
    return c.get("whisker", "email"), c.get("whisker", "password")


async def generate_session(websession, headers, device_id):
    url = f"{WATFORD_API}/api/device-manager/client/generate-session/{device_id}?autoStart=true"
    async with websession.get(url, headers=headers) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"generate-session: {resp.status} {body}")
        return await resp.json()


async def create_pc_with_gathered_sdp(session_data):
    """Create PC, gather ICE, return full SDP."""
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
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ice_servers))
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Wait for ICE gathering
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

    return pc, pc.localDescription.sdp


async def try_request(ws_session, method, url, headers, body=None, content_type=None, label=""):
    """Make an HTTP request and log the full response."""
    req_headers = dict(headers)
    if content_type:
        req_headers["Content-Type"] = content_type

    _LOGGER.info(f"  [{label}] {method} {url}")
    if body:
        preview = body if len(body) <= 200 else body[:200] + "..."
        _LOGGER.info(f"  [{label}] Body: {preview}")

    try:
        if method == "POST":
            async with ws_session.post(url, headers=req_headers, data=body) as resp:
                resp_body = await resp.text()
                _LOGGER.info(f"  [{label}] → {resp.status}")
                _LOGGER.info(f"  [{label}] Response: {resp_body[:1000]}")
                # Log interesting headers
                for k in ("content-type", "location", "x-amzn-errortype"):
                    if k in resp.headers:
                        _LOGGER.info(f"  [{label}] {k}: {resp.headers[k]}")
                return resp.status, resp_body
        elif method == "PUT":
            async with ws_session.put(url, headers=req_headers, data=body) as resp:
                resp_body = await resp.text()
                _LOGGER.info(f"  [{label}] → {resp.status}")
                _LOGGER.info(f"  [{label}] Response: {resp_body[:1000]}")
                return resp.status, resp_body
        elif method == "PATCH":
            async with ws_session.patch(url, headers=req_headers, data=body) as resp:
                resp_body = await resp.text()
                _LOGGER.info(f"  [{label}] → {resp.status}")
                _LOGGER.info(f"  [{label}] Response: {resp_body[:1000]}")
                return resp.status, resp_body
        else:
            async with ws_session.get(url, headers=req_headers) as resp:
                resp_body = await resp.text()
                _LOGGER.info(f"  [{label}] → {resp.status}")
                _LOGGER.info(f"  [{label}] Response: {resp_body[:1000]}")
                return resp.status, resp_body
    except Exception as e:
        _LOGGER.info(f"  [{label}] ERROR: {e}")
        return 0, str(e)


async def main():
    _LOGGER.info("=" * 60)
    _LOGGER.info("WebRTC Signaling Probe - Round 4 (REST Signaling)")
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
            _LOGGER.error("No LR5 Pro")
            return

        device_id = lr5_pro.camera_metadata["deviceId"]
        _LOGGER.info(f"Device: {device_id}")

        id_token = await account.session.async_get_id_token()
        headers = {"Authorization": f"Bearer {id_token}"}
        ws_session = aiohttp.ClientSession()
        pc = None

        try:
            # Generate session and real SDP
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            session_token = session_data["sessionToken"]
            _LOGGER.info(f"Session: {session_id}")

            pc, sdp = await create_pc_with_gathered_sdp(session_data)
            _LOGGER.info(f"SDP ready ({len(sdp)} chars)")

            # ─── TEST 1: Probe /api/device-manager/client/signaling with params ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 1: /api/device-manager/client/signaling GET with params ───")

            base_sig = f"{WATFORD_API}/api/device-manager/client/signaling"
            get_variations = [
                (f"{base_sig}?sessionId={session_id}", "get-sessionId"),
                (f"{base_sig}?deviceId={device_id}", "get-deviceId"),
                (f"{base_sig}?sessionId={session_id}&deviceId={device_id}", "get-both"),
                (f"{base_sig}/{device_id}", "get-path-device"),
                (f"{base_sig}/{session_id}", "get-path-session"),
                (f"{base_sig}/{device_id}?sessionId={session_id}", "get-path-device-param"),
            ]
            for url, label in get_variations:
                await try_request(ws_session, "GET", url, headers, label=label)
                await asyncio.sleep(0.5)

            # ─── TEST 2: POST SDP to signaling endpoint ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 2: POST SDP to signaling endpoints ───")

            # Use session token auth for these
            session_headers = {"Authorization": f"Bearer {session_token}"}
            both_headers = {"Authorization": f"Bearer {id_token}"}

            post_variations = [
                # POST with JSON SDP body
                (base_sig, both_headers, json.dumps({"type": "offer", "sdp": sdp}), "application/json", "post-json"),
                (base_sig, both_headers, json.dumps({"type": "offer", "sdp": sdp, "sessionId": session_id}), "application/json", "post-json-sid"),
                (f"{base_sig}/{device_id}", both_headers, json.dumps({"type": "offer", "sdp": sdp}), "application/json", "post-device-json"),
                (f"{base_sig}/{session_id}", both_headers, json.dumps({"type": "offer", "sdp": sdp}), "application/json", "post-session-json"),

                # WHIP-style: POST raw SDP with application/sdp content type
                (base_sig, both_headers, sdp, "application/sdp", "post-whip"),
                (f"{base_sig}/{device_id}", both_headers, sdp, "application/sdp", "post-whip-device"),
                (f"{base_sig}/{session_id}", both_headers, sdp, "application/sdp", "post-whip-session"),

                # With session token
                (base_sig, session_headers, json.dumps({"type": "offer", "sdp": sdp}), "application/json", "post-session-token"),
            ]
            for url, hdrs, body, ct, label in post_variations:
                await try_request(ws_session, "POST", url, hdrs, body=body, content_type=ct, label=label)
                await asyncio.sleep(0.5)

            # ─── TEST 3: Try other HTTP methods on signaling ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 3: PUT/PATCH on signaling ───")
            body_json = json.dumps({"type": "offer", "sdp": sdp[:200], "sessionId": session_id})
            await try_request(ws_session, "PUT", base_sig, headers, body=body_json, content_type="application/json", label="put")
            await try_request(ws_session, "PATCH", base_sig, headers, body=body_json, content_type="application/json", label="patch")

            # ─── TEST 4: Explore other device-manager endpoints ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 4: Enumerate device-manager endpoints ───")

            dm_paths = [
                f"/api/device-manager/client/sessions",
                f"/api/device-manager/client/sessions/{session_id}",
                f"/api/device-manager/client/stream/{device_id}",
                f"/api/device-manager/client/offer/{device_id}",
                f"/api/device-manager/client/connect/{device_id}",
                f"/api/device-manager/client/devices",
                f"/api/device-manager/client/devices/{device_id}",
                f"/api/device-manager/client/start/{device_id}",
                f"/api/device-manager/client/webrtc/{device_id}",
                f"/api/device-manager/client/{device_id}/signaling",
                f"/api/device-manager/client/{device_id}/offer",
                f"/api/device-manager/client/{device_id}/stream",
                f"/api/device-manager/device/{device_id}/signaling",
                f"/api/device-manager/device/{device_id}/connect",
                # Watford-specific paths
                f"/api/watford/signaling",
                f"/api/watford/stream/{device_id}",
                f"/api/stream/{device_id}",
                f"/api/offer/{device_id}",
            ]
            for path in dm_paths:
                await try_request(ws_session, "GET", f"{WATFORD_API}{path}", headers, label=path.split("/")[-1])
                await asyncio.sleep(0.3)

            # ─── TEST 5: POST to interesting 404 paths (some might accept POST) ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 5: POST to promising endpoints ───")
            post_paths = [
                f"/api/device-manager/client/stream/{device_id}",
                f"/api/device-manager/client/offer/{device_id}",
                f"/api/device-manager/client/connect/{device_id}",
                f"/api/device-manager/client/{device_id}/signaling",
                f"/api/device-manager/client/{device_id}/offer",
            ]
            short_sdp = json.dumps({"type": "offer", "sdp": sdp[:200], "sessionId": session_id})
            for path in post_paths:
                await try_request(
                    ws_session, "POST", f"{WATFORD_API}{path}", headers,
                    body=short_sdp, content_type="application/json",
                    label=f"POST-{path.split('/')[-1]}"
                )
                await asyncio.sleep(0.3)

            # ─── TEST 6: Try WebSocket on /api/device-manager/client/signaling ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 6: WebSocket on /api/device-manager/client/signaling ───")
            ws_sig_urls = [
                f"wss://watford.ienso-dev.com/api/device-manager/client/signaling?accessToken={session_token}",
                f"wss://watford.ienso-dev.com/api/device-manager/client/signaling?accessToken={session_token}&sessionId={session_id}",
                f"wss://watford.ienso-dev.com/api/device-manager/client/signaling/{device_id}?accessToken={session_token}",
                f"wss://watford.ienso-dev.com/api/device-manager/client/signaling?accessToken={id_token}",
            ]
            for url in ws_sig_urls:
                label = "ws-dm-signaling"
                _LOGGER.info(f"  [{label}] Trying WS: ...{url[60:120]}...")
                try:
                    async with ws_session.ws_connect(url) as ws:
                        _LOGGER.info(f"  [{label}] Connected!")
                        await ws.send_str(json.dumps({"type": "offer", "sdp": "v=0\r\n"}))
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=5)
                            _LOGGER.info(f"  [{label}] RESPONSE: type={msg.type} data={msg.data}")
                        except asyncio.TimeoutError:
                            _LOGGER.info(f"  [{label}] No response in 5s")
                except aiohttp.WSServerHandshakeError as e:
                    _LOGGER.info(f"  [{label}] Handshake rejected: {e.status} {e.message}")
                except Exception as e:
                    _LOGGER.info(f"  [{label}] Error: {e}")
                await asyncio.sleep(0.5)

        finally:
            if pc:
                await pc.close()
            await ws_session.close()

    finally:
        await account.disconnect()

    _LOGGER.info("")
    _LOGGER.info("Probe round 4 complete.")


if __name__ == "__main__":
    asyncio.run(main())
