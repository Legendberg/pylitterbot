"""Probe WebRTC signaling - Round 9.

Focus: Crack the 400-returning REST endpoints.

Three endpoints return 400 "Invalid request params input" (not 404):
  /api/device-manager/client/signaling
  /api/device-manager/client/sessions
  /api/device-manager/client/devices

This is a Hapi.js/Joi validation error = the route EXISTS but we're
sending wrong params. Previous probes tried many query params but all
returned the same 400.

New strategies:
1. OPTIONS requests to discover allowed methods / CORS headers
2. Try `accessToken` as query param (mirroring WS URL pattern)
3. POST with JSON body (POST to base returned 404, but try with body)
4. Try pagination-style params (page, limit, offset, cursor, skip)
5. Test without auth header (maybe some endpoints are public with token in query)
6. Try body params on GET (some APIs accept this)
7. Compare generate-session with/without autoStart
8. Try calling signaling while WebSocket is actively connected
9. Fuzz more sub-paths under device-manager
10. Try the endpoints with session token in Authorization header
"""

import asyncio
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
            Path(__file__).parent / "camera_webrtc_probe9_results.log", mode="w"
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
    """Make HTTP request and log response. Returns (status, body, headers)."""
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

            # Only log interesting results (not the same old 400)
            is_interesting = status != 400 or "Invalid request params input" not in resp_body
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


async def main():
    _LOGGER.info("=" * 60)
    _LOGGER.info("WebRTC Signaling Probe - Round 9 (Cracking 400 Endpoints)")
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
        unit_serial = lr5_pro.serial
        _LOGGER.info(f"Device: {device_id}")
        _LOGGER.info(f"Space: {space_id}")
        _LOGGER.info(f"Unit Serial: {unit_serial}")
        _LOGGER.info(f"Camera metadata keys: {list(lr5_pro.camera_metadata.keys())}")
        _LOGGER.info(f"Camera metadata: {json.dumps(lr5_pro.camera_metadata, default=str)}")

        id_token = await account.session.async_get_id_token()
        bearer_headers = {"Authorization": f"Bearer {id_token}"}
        ws_session = aiohttp.ClientSession()

        try:
            # Generate a session for testing
            session_data = await generate_session(ws_session, bearer_headers, device_id)
            session_id = session_data["sessionId"]
            session_token = session_data["sessionToken"]
            _LOGGER.info(f"Session: {session_id}")
            _LOGGER.info(f"Full generate-session response keys: {list(session_data.keys())}")
            _LOGGER.info(f"Full response: {json.dumps(session_data, default=str)[:1000]}")

            base_sig = f"{WATFORD_API}/api/device-manager/client/signaling"
            base_ses = f"{WATFORD_API}/api/device-manager/client/sessions"
            base_dev = f"{WATFORD_API}/api/device-manager/client/devices"

            # ─── TEST 1: OPTIONS requests ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 1: OPTIONS requests (method discovery) ───")
            for url, label in [
                (base_sig, "signaling"),
                (base_ses, "sessions"),
                (base_dev, "devices"),
                (f"{WATFORD_API}/api/device-manager/client/generate-session/{device_id}", "gen-session"),
            ]:
                await try_req(ws_session, "OPTIONS", url, headers=bearer_headers, label=label)
                await asyncio.sleep(0.3)

            # ─── TEST 2: accessToken as query param (like WS URL) ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 2: accessToken as query param ───")
            for base, label in [(base_sig, "sig"), (base_ses, "ses"), (base_dev, "dev")]:
                # With session token
                await try_req(ws_session, "GET", f"{base}?accessToken={session_token}",
                              headers=bearer_headers, label=f"{label}-sessToken")
                # With session token ONLY (no bearer)
                await try_req(ws_session, "GET", f"{base}?accessToken={session_token}",
                              label=f"{label}-sessToken-noAuth")
                # With id token in query
                await try_req(ws_session, "GET", f"{base}?accessToken={id_token}",
                              headers=bearer_headers, label=f"{label}-idToken")
                await asyncio.sleep(0.3)

            # ─── TEST 3: Session token in Authorization header ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 3: Session token as Bearer ───")
            sess_headers = {"Authorization": f"Bearer {session_token}"}
            for base, label in [(base_sig, "sig"), (base_ses, "ses"), (base_dev, "dev")]:
                await try_req(ws_session, "GET", base, headers=sess_headers, label=f"{label}-sessBear")
                await asyncio.sleep(0.3)

            # ─── TEST 4: POST with JSON bodies ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 4: POST with various JSON bodies ───")
            post_bodies = [
                ({"sessionId": session_id}, "sid"),
                ({"deviceId": device_id}, "did"),
                ({"sessionId": session_id, "deviceId": device_id}, "both"),
                ({"accessToken": session_token}, "token"),
                ({"action": "connect", "deviceId": device_id}, "connect"),
                ({"action": "start", "sessionId": session_id}, "start"),
                ({"type": "offer", "sessionId": session_id}, "offer"),
                ({}, "empty"),
            ]
            for body, label in post_bodies:
                await try_req(ws_session, "POST", base_sig,
                              headers=bearer_headers,
                              body=json.dumps(body), ct="application/json",
                              label=f"sig-{label}")
                await asyncio.sleep(0.3)

            # Also try sessions endpoint with POST
            for body, label in post_bodies[:4]:
                await try_req(ws_session, "POST", base_ses,
                              headers=bearer_headers,
                              body=json.dumps(body), ct="application/json",
                              label=f"ses-{label}")
                await asyncio.sleep(0.2)

            # ─── TEST 5: Different param name patterns ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 5: Systematic query param names ───")
            # These are params we HAVEN'T tried yet
            new_params = [
                # Pagination / filtering
                f"page=1",
                f"size=10",
                f"count=10",
                f"offset=0",
                f"cursor=0",
                f"skip=0",
                f"take=10",
                f"per_page=10",
                f"pageSize=10",
                # Auth-related
                f"accessToken={session_token}",
                f"token={session_token}",
                f"auth={session_token}",
                # Identifiers we haven't tried
                f"userId={account.user_id if hasattr(account, 'user_id') else 'unknown'}",
                f"accountId={account.account_id if hasattr(account, 'account_id') else 'unknown'}",
                f"spaceId={space_id}",
                f"unitSerial={unit_serial}",
                # Composite
                f"deviceId={device_id}&spaceId={space_id}",
                f"sessionId={session_id}&accessToken={session_token}",
                f"deviceId={device_id}&sessionId={session_id}&accessToken={session_token}",
                # Boolean flags
                f"active=true",
                f"online=true",
                f"autoStart=true",
                # Format flags
                f"format=json",
                f"type=webrtc",
                f"protocol=webrtc",
            ]

            for params in new_params:
                await try_req(ws_session, "GET", f"{base_sig}?{params}",
                              headers=bearer_headers, label=f"sig?{params[:40]}")
                await asyncio.sleep(0.15)

            # Quick subset for sessions/devices too
            for params in new_params[:8]:
                await try_req(ws_session, "GET", f"{base_ses}?{params}",
                              headers=bearer_headers, label=f"ses?{params[:40]}")
                await try_req(ws_session, "GET", f"{base_dev}?{params}",
                              headers=bearer_headers, label=f"dev?{params[:40]}")
                await asyncio.sleep(0.15)

            # ─── TEST 6: generate-session WITHOUT autoStart ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 6: generate-session with/without autoStart ───")
            id_token = await account.session.async_get_id_token()
            bearer_headers = {"Authorization": f"Bearer {id_token}"}

            sess_no_auto = await generate_session(ws_session, bearer_headers, device_id, auto_start=False)
            _LOGGER.info(f"Without autoStart: {json.dumps(sess_no_auto, default=str)[:500]}")
            _LOGGER.info(f"Has autoStart key: {'autoStart' in sess_no_auto}")
            _LOGGER.info(f"autoStart value: {sess_no_auto.get('autoStart')}")

            # Compare the two
            for key in set(list(session_data.keys()) + list(sess_no_auto.keys())):
                v1 = session_data.get(key)
                v2 = sess_no_auto.get(key)
                if v1 != v2 and key not in ("sessionId", "sessionToken", "sessionExpiration"):
                    _LOGGER.info(f"  DIFF in '{key}': autoStart={v1} vs noAutoStart={v2}")

            # ─── TEST 7: POST to generate-session (maybe it accepts POST too?) ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 7: POST/PUT/DELETE on generate-session and new sub-paths ───")
            gen_url = f"{WATFORD_API}/api/device-manager/client/generate-session/{device_id}"
            await try_req(ws_session, "POST", gen_url, headers=bearer_headers,
                          body=json.dumps({"autoStart": True}), ct="application/json",
                          label="gen-POST")
            await try_req(ws_session, "POST", gen_url + "?autoStart=true",
                          headers=bearer_headers, label="gen-POST-qs")
            await try_req(ws_session, "DELETE", gen_url, headers=bearer_headers,
                          label="gen-DELETE")
            await asyncio.sleep(0.5)

            # Sub-paths we haven't tried
            new_sub_paths = [
                f"/api/device-manager/client/start-session/{session_id}",
                f"/api/device-manager/client/join-session/{session_id}",
                f"/api/device-manager/client/close-session/{session_id}",
                f"/api/device-manager/client/notify/{device_id}",
                f"/api/device-manager/client/wake/{device_id}",
                f"/api/device-manager/client/trigger/{device_id}",
                f"/api/device-manager/client/call/{device_id}",
                f"/api/device-manager/client/connect/{device_id}",
                f"/api/device-manager/client/signal/{device_id}",
                f"/api/device-manager/client/signal/{session_id}",
                f"/api/device-manager/client/ice/{device_id}",
                f"/api/device-manager/client/sdp/{device_id}",
                f"/api/device-manager/client/peer/{device_id}",
                f"/api/device-manager/client/stream/{device_id}",
                f"/api/device-manager/client/live/{device_id}",
                f"/api/device-manager/client/camera/{device_id}",
                f"/api/device-manager/client/session/{session_id}",
                f"/api/device-manager/client/session/{session_id}/signaling",
                f"/api/device-manager/client/session/{session_id}/offer",
                f"/api/device-manager/client/session/{session_id}/start",
                # Device-manager/device paths (camera-side API?)
                f"/api/device-manager/device/connect/{device_id}",
                f"/api/device-manager/device/session/{session_id}",
                f"/api/device-manager/device/register/{device_id}",
                # Other patterns
                f"/api/device-manager/client/generate-session",
                f"/api/signaling/{session_id}",
                f"/api/signaling/{device_id}",
                f"/api/signaling/offer",
                f"/api/signaling/connect",
            ]
            for path in new_sub_paths:
                url = f"{WATFORD_API}{path}"
                # Try GET
                s, b, h = await try_req(ws_session, "GET", url, headers=bearer_headers,
                                        label=f"GET {path.split('/')[-1]}")
                # If GET returns 404 try POST
                if s == 404:
                    await try_req(ws_session, "POST", url, headers=bearer_headers,
                                  body=json.dumps({"sessionId": session_id, "deviceId": device_id}),
                                  ct="application/json",
                                  label=f"POST {path.split('/')[-1]}")
                await asyncio.sleep(0.15)

            # ─── TEST 8: REST + WebSocket combo ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 8: Call REST endpoints while WS is connected ───")
            id_token = await account.session.async_get_id_token()
            bearer_headers = {"Authorization": f"Bearer {id_token}"}

            session_data2 = await generate_session(ws_session, bearer_headers, device_id)
            sid2 = session_data2["sessionId"]
            tok2 = session_data2["sessionToken"]
            ws_url = f"{session_data2['signalingURL']}?accessToken={tok2}"

            # Connect WebSocket
            ws = await ws_session.ws_connect(ws_url)
            _LOGGER.info(f"WS connected for session {sid2}")

            # Now try REST endpoints with this active session
            await try_req(ws_session, "GET", f"{base_sig}?sessionId={sid2}",
                          headers=bearer_headers, label="sig-active-sess")
            await try_req(ws_session, "GET", f"{base_sig}?sessionId={sid2}&accessToken={tok2}",
                          headers=bearer_headers, label="sig-active-both")
            await try_req(ws_session, "GET", f"{base_ses}?sessionId={sid2}",
                          headers=bearer_headers, label="ses-active")
            await try_req(ws_session, "GET", f"{base_dev}?deviceId={device_id}&sessionId={sid2}",
                          headers=bearer_headers, label="dev-active")

            # Check if WS got any message from the REST calls
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=3)
                _LOGGER.info(f">>> WS got message after REST calls: type={msg.type} data={msg.data[:300] if hasattr(msg.data, '__getitem__') else msg.data}")
            except asyncio.TimeoutError:
                _LOGGER.info("WS: no messages after REST calls (3s)")

            try:
                await ws.close()
            except Exception:
                pass

            # ─── TEST 9: Try to discover the user/account ID format ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 9: Account/user identity fields ───")
            # Check what identity fields are available
            if hasattr(account, 'user_id'):
                _LOGGER.info(f"account.user_id: {account.user_id}")
            if hasattr(account, 'account_id'):
                _LOGGER.info(f"account.account_id: {account.account_id}")
            if hasattr(account, 'user'):
                _LOGGER.info(f"account.user: {account.user}")
            if hasattr(account, 'session'):
                sess = account.session
                _LOGGER.info(f"session type: {type(sess).__name__}")
                _LOGGER.info(f"session attrs: {[a for a in dir(sess) if not a.startswith('_')]}")

            # Try to get user sub from Cognito token
            try:
                import base64
                parts = id_token.split(".")
                # Decode JWT payload (add padding)
                payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
                decoded = json.loads(base64.b64decode(payload))
                _LOGGER.info(f"ID token claims: {json.dumps(decoded, default=str)[:500]}")
                cognito_sub = decoded.get("sub", "")
                _LOGGER.info(f"Cognito sub: {cognito_sub}")

                # Try endpoints with cognito sub
                if cognito_sub:
                    await try_req(ws_session, "GET",
                                  f"{base_sig}?userId={cognito_sub}",
                                  headers=bearer_headers, label="sig-cogSub")
                    await try_req(ws_session, "GET",
                                  f"{base_dev}?userId={cognito_sub}",
                                  headers=bearer_headers, label="dev-cogSub")
                    await try_req(ws_session, "GET",
                                  f"{base_ses}?userId={cognito_sub}",
                                  headers=bearer_headers, label="ses-cogSub")
            except Exception as e:
                _LOGGER.info(f"JWT decode error: {e}")

            # ─── TEST 10: WebSocket with longer wait + keep-alive reconnect ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 10: WebSocket keep-alive with reconnect ───")
            id_token = await account.session.async_get_id_token()
            bearer_headers = {"Authorization": f"Bearer {id_token}"}

            session_data3 = await generate_session(ws_session, bearer_headers, device_id)
            sid3 = session_data3["sessionId"]
            tok3 = session_data3["sessionToken"]
            ws_url3 = f"{session_data3['signalingURL']}?accessToken={tok3}"

            _LOGGER.info(f"Session: {sid3}")
            _LOGGER.info("Will connect, send keepalive, reconnect on close for 45s total")

            overall_deadline = time.monotonic() + 45
            connect_count = 0
            total_msgs = 0

            while time.monotonic() < overall_deadline:
                connect_count += 1
                _LOGGER.info(f"  Connect attempt #{connect_count}")
                try:
                    ws_ka = await ws_session.ws_connect(ws_url3)
                except Exception as e:
                    _LOGGER.info(f"  Connect failed: {e}")
                    await asyncio.sleep(2)
                    continue

                # Send a keepalive/ping-like message
                try:
                    await ws_ka.send_str(json.dumps({"type": "ping"}))
                except Exception:
                    pass

                while time.monotonic() < overall_deadline:
                    remaining = min(overall_deadline - time.monotonic(), 8)
                    if remaining <= 0:
                        break
                    try:
                        msg = await asyncio.wait_for(ws_ka.receive(), timeout=remaining)
                        total_msgs += 1
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            _LOGGER.info(f"  WS #{connect_count} TEXT: {msg.data[:300]}")
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            _LOGGER.info(f"  WS #{connect_count} BIN: {msg.data[:50].hex()}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR,
                                         aiohttp.WSMsgType.CLOSE):
                            _LOGGER.info(f"  WS #{connect_count} CLOSED code={getattr(ws_ka, 'close_code', '?')}")
                            break
                    except asyncio.TimeoutError:
                        # Send another keepalive
                        try:
                            await ws_ka.send_str(json.dumps({"type": "ping"}))
                        except Exception:
                            break

                try:
                    await ws_ka.close()
                except Exception:
                    pass
                await asyncio.sleep(1)

            _LOGGER.info(f"Keep-alive test: {connect_count} connections, {total_msgs} messages in 45s")

            # ─── TEST 11: HEAD requests and response header analysis ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 11: HEAD requests + detailed response headers ───")
            id_token = await account.session.async_get_id_token()
            bearer_headers = {"Authorization": f"Bearer {id_token}"}

            for base, label in [(base_sig, "sig"), (base_ses, "ses"), (base_dev, "dev")]:
                s, b, h = await try_req(ws_session, "HEAD", base, headers=bearer_headers,
                                        label=f"HEAD-{label}")
                # Log ALL response headers
                _LOGGER.info(f"  Full headers for {label}:")
                for k, v in h.items():
                    _LOGGER.info(f"    {k}: {v}")
                await asyncio.sleep(0.3)

            # ─── TEST 12: Different Content-Types for POST ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 12: POST with different content types ───")
            ct_body_combos = [
                ("application/x-www-form-urlencoded", f"sessionId={session_id}&deviceId={device_id}", "form"),
                ("text/plain", session_id, "text"),
                ("application/octet-stream", session_id.encode(), "binary"),
                ("multipart/form-data", None, "multipart"),  # skip actual multipart
            ]
            for ct, body, label in ct_body_combos:
                if body is None:
                    continue
                await try_req(ws_session, "POST", base_sig,
                              headers=bearer_headers,
                              body=body, ct=ct, label=f"POST-{label}")
                await asyncio.sleep(0.2)

            # Also try POST to sessions and devices
            form_body = f"sessionId={session_id}&deviceId={device_id}"
            await try_req(ws_session, "POST", base_ses,
                          headers=bearer_headers,
                          body=form_body, ct="application/x-www-form-urlencoded",
                          label="ses-form")
            await try_req(ws_session, "POST", base_dev,
                          headers=bearer_headers,
                          body=form_body, ct="application/x-www-form-urlencoded",
                          label="dev-form")

        finally:
            await ws_session.close()

    finally:
        await account.disconnect()

    _LOGGER.info("")
    _LOGGER.info("Probe round 9 complete.")


if __name__ == "__main__":
    asyncio.run(main())
