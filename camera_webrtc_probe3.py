"""Probe WebRTC signaling - Round 3.

Focus: URL variations, query parameters, path components, and response headers.
The signaling WebSocket is a black hole - maybe we're connecting to the wrong
endpoint or missing required URL parameters.
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
            Path(__file__).parent / "camera_webrtc_probe3_results.log", mode="w"
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
            raise RuntimeError(f"generate-session failed: {resp.status} {body}")
        return await resp.json()


async def try_ws_url(ws_session, url, label, send_msg=None, timeout=8):
    """Try connecting to a WebSocket URL, optionally send a message, listen."""
    _LOGGER.info(f"  [{label}] Connecting: {url[:120]}...")
    try:
        async with ws_session.ws_connect(url) as ws:
            _LOGGER.info(f"  [{label}] Connected! response_headers:")
            # Log interesting response headers
            if hasattr(ws, '_response') and ws._response:
                for k, v in ws._response.headers.items():
                    if k.lower() not in ('date', 'server'):
                        _LOGGER.info(f"    {k}: {v}")

            if send_msg:
                _LOGGER.info(f"  [{label}] Sending: {send_msg[:150]}...")
                await ws.send_str(send_msg)

            deadline = time.monotonic() + timeout
            count = 0
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                count += 1
                if msg.type == aiohttp.WSMsgType.TEXT:
                    _LOGGER.info(f"  [{label}] RESPONSE TEXT: {msg.data[:1000]}")
                    return True, msg.data
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    _LOGGER.info(f"  [{label}] RESPONSE BINARY ({len(msg.data)}b): {msg.data[:200].hex()}")
                    return True, msg.data
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    _LOGGER.info(f"  [{label}] CLOSE: code={getattr(ws, 'close_code', '?')}")
                    return False, None

            _LOGGER.info(f"  [{label}] No response in {timeout}s")
            return False, None

    except aiohttp.WSServerHandshakeError as e:
        _LOGGER.info(f"  [{label}] Handshake REJECTED: {e.status} {e.message}")
        if hasattr(e, 'headers') and e.headers:
            for k, v in e.headers.items():
                _LOGGER.info(f"    {k}: {v}")
        return False, None
    except Exception as e:
        _LOGGER.info(f"  [{label}] ERROR: {type(e).__name__}: {e}")
        return False, None


async def main():
    _LOGGER.info("=" * 60)
    _LOGGER.info("WebRTC Signaling Probe - Round 3 (URL Variations)")
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
            _LOGGER.error("No LR5 Pro with camera")
            return

        device_id = lr5_pro.camera_metadata["deviceId"]
        _LOGGER.info(f"Device: {device_id}")

        id_token = await account.session.async_get_id_token()
        headers = {"Authorization": f"Bearer {id_token}"}
        ws_session = aiohttp.ClientSession()

        try:
            # Generate a session for tests
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            token = session_data["sessionToken"]
            _LOGGER.info(f"Session: {session_id}")
            _LOGGER.info(f"Signaling URL from API: {session_data['signalingURL']}")

            # Check the WS upgrade response headers for the normal URL
            _LOGGER.info("")
            _LOGGER.info("─── TEST 1: Check response headers on normal connection ───")
            normal_url = f"{session_data['signalingURL']}?accessToken={token}"
            await try_ws_url(ws_session, normal_url, "normal", timeout=5)

            await asyncio.sleep(1)

            # ─── TEST 2: URL path variations ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 2: URL path variations ───")

            # Fresh session for each batch
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            token = session_data["sessionToken"]

            base = "wss://watford.ienso-dev.com"
            test_msg = json.dumps({"type": "offer", "sdp": "v=0\r\n"})

            path_variations = [
                (f"{base}/api/signaling/{session_id}?accessToken={token}", "path-sessionId"),
                (f"{base}/api/signaling/session/{session_id}?accessToken={token}", "path-session-id"),
                (f"{base}/api/signaling?accessToken={token}&sessionId={session_id}", "param-sessionId"),
                (f"{base}/api/signaling?accessToken={token}&deviceId={device_id}", "param-deviceId"),
                (f"{base}/api/signaling?accessToken={token}&role=client", "param-role"),
                (f"{base}/api/signaling?accessToken={token}&clientId={session_id}", "param-clientId"),
                (f"{base}/api/signaling?accessToken={token}&sessionId={session_id}&deviceId={device_id}", "param-both"),
            ]

            for url, label in path_variations:
                success, _ = await try_ws_url(ws_session, url, label, send_msg=test_msg, timeout=5)
                if success:
                    _LOGGER.info(f">>> URL VARIATION '{label}' GOT RESPONSE! <<<")
                    break
                await asyncio.sleep(1)

            # ─── TEST 3: Check Watford API for other signaling endpoints ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 3: Probe Watford API for other endpoints ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}

            api_paths = [
                "/api/signaling",
                "/api/device-manager",
                "/api/device-manager/client",
                f"/api/device-manager/client/session/{session_id}",
                f"/api/device-manager/device/{device_id}",
                "/api/device-manager/client/signaling",
                f"/api/device-manager/client/signaling/{device_id}",
                "/api/rtc",
                "/api/webrtc",
                f"/api/stream/{device_id}",
                "/api/health",
                "/api/version",
                "/api",
                "/",
            ]

            for path in api_paths:
                url = f"https://watford.ienso-dev.com{path}"
                try:
                    async with ws_session.get(url, headers=headers, allow_redirects=False) as resp:
                        body_preview = ""
                        try:
                            body = await resp.text()
                            body_preview = body[:200]
                        except Exception:
                            pass
                        _LOGGER.info(f"  GET {path} → {resp.status} {body_preview}")
                except Exception as e:
                    _LOGGER.info(f"  GET {path} → ERROR: {e}")

            # ─── TEST 4: Try Cognito token directly on WS (not session token) ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 4: Try Cognito ID token on WS (instead of session token) ───")
            id_token = await account.session.async_get_id_token()

            cognito_urls = [
                (f"{base}/api/signaling?accessToken={id_token}", "cognito-token"),
                (f"{base}/api/signaling?accessToken={id_token}&deviceId={device_id}", "cognito-token-device"),
            ]
            for url, label in cognito_urls:
                await try_ws_url(ws_session, url, label, timeout=5)
                await asyncio.sleep(1)

            # ─── TEST 5: Check if there's an alternative signaling domain ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 5: Alternative signaling domains ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            token = session_data["sessionToken"]

            alt_domains = [
                f"wss://signaling.watford-prod.ienso-dev.com/api/signaling?accessToken={token}",
                f"wss://coturn.watford-prod.ienso-dev.com/signaling?accessToken={token}",
                f"wss://watford-prod.ienso-dev.com/api/signaling?accessToken={token}",
            ]
            for url in alt_domains:
                label = url.split("//")[1].split("/")[0]
                await try_ws_url(ws_session, url, label, timeout=5)
                await asyncio.sleep(1)

            # ─── TEST 6: Examine session JWT more carefully ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 6: Decode session JWT ───")
            import base64 as b64
            parts = token.split(".")
            for i, part in enumerate(parts[:2]):
                padded = part + "=" * (4 - len(part) % 4)
                try:
                    decoded = b64.b64decode(padded)
                    _LOGGER.info(f"  JWT part {i}: {decoded.decode('utf-8', errors='replace')}")
                except Exception as e:
                    _LOGGER.info(f"  JWT part {i}: decode error: {e}")

        finally:
            await ws_session.close()

    finally:
        await account.disconnect()

    _LOGGER.info("")
    _LOGGER.info("Probe round 3 complete.")


if __name__ == "__main__":
    asyncio.run(main())
