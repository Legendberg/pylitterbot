"""Probe WebRTC signaling - Round 5.

Test: Is the WebSocket a relay? Connect two clients to the same session
and see if messages from one appear on the other.

Also: deeper probing of the 400-returning endpoints.
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
            Path(__file__).parent / "camera_webrtc_probe5_results.log", mode="w"
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


async def main():
    _LOGGER.info("=" * 60)
    _LOGGER.info("WebRTC Signaling Probe - Round 5")
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

        try:
            # ─── TEST 1: Two WS clients, same session token ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 1: Relay test - two WS clients, same session ───")
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"
            _LOGGER.info(f"Session: {session_id}")

            # Connect both clients
            ws_a = await ws_session.ws_connect(ws_url)
            _LOGGER.info("Client A connected")

            # Same token, second connection
            ws_b = await ws_session.ws_connect(ws_url)
            _LOGGER.info("Client B connected")

            # Client A sends, Client B listens
            test_msg = json.dumps({"test": "relay-probe", "from": "clientA"})
            _LOGGER.info(f"Client A sending: {test_msg}")
            await ws_a.send_str(test_msg)

            # Listen on B
            _LOGGER.info("Listening on Client B (5s)...")
            try:
                msg = await asyncio.wait_for(ws_b.receive(), timeout=5)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    _LOGGER.info(f"Client B received TEXT: {msg.data}")
                    _LOGGER.info(">>> RELAY CONFIRMED! <<<")
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    _LOGGER.info(f"Client B received BINARY: {msg.data[:200].hex()}")
                    _LOGGER.info(">>> RELAY CONFIRMED! <<<")
                else:
                    _LOGGER.info(f"Client B received: type={msg.type}")
            except asyncio.TimeoutError:
                _LOGGER.info("Client B: no message in 5s (relay NOT confirmed)")

            # Also listen on A (echo?)
            _LOGGER.info("Listening on Client A for echo (3s)...")
            try:
                msg = await asyncio.wait_for(ws_a.receive(), timeout=3)
                _LOGGER.info(f"Client A received: type={msg.type} data={getattr(msg, 'data', '')}")
            except asyncio.TimeoutError:
                _LOGGER.info("Client A: no echo")

            # Client B sends, Client A listens
            test_msg2 = json.dumps({"test": "relay-probe", "from": "clientB"})
            await ws_b.send_str(test_msg2)
            _LOGGER.info(f"Client B sent: {test_msg2}")
            try:
                msg = await asyncio.wait_for(ws_a.receive(), timeout=5)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    _LOGGER.info(f"Client A received: {msg.data}")
                    _LOGGER.info(">>> BIDIRECTIONAL RELAY! <<<")
                else:
                    _LOGGER.info(f"Client A received: type={msg.type}")
            except asyncio.TimeoutError:
                _LOGGER.info("Client A: no message from B")

            await ws_a.close()
            await ws_b.close()

            await asyncio.sleep(2)

            # ─── TEST 2: Deep probe the 400-returning endpoints ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 2: Deep probe 400-returning endpoints ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}

            # Try many different parameter names for /signaling
            base = f"{WATFORD_API}/api/device-manager/client/signaling"
            param_combos = [
                # Common WebRTC / IoT param names
                "channelId=test",
                "channel=test",
                "room=test",
                "roomId=test",
                f"token={token}",
                f"spaceId={lr5_pro.camera_metadata.get('spaceId', '')}",
                "type=offer",
                "action=connect",
                "mode=webrtc",
                f"serial={lr5_pro.serial}",
                # Watford-specific guesses
                f"device={device_id}",
                f"camera={device_id}",
                f"cameraId={device_id}",
                f"id={device_id}",
                # Combined
                f"device={device_id}&session={session_id}",
                f"deviceId={device_id}&token={token}",
            ]

            for params in param_combos:
                url = f"{base}?{params}"
                try:
                    async with ws_session.get(url, headers=headers) as resp:
                        body = await resp.text()
                        status_str = f"{resp.status}"
                        if resp.status != 400:
                            status_str = f">>> {resp.status} <<< (DIFFERENT!)"
                        _LOGGER.info(f"  ?{params[:60]:60s} → {status_str} {body[:100]}")
                except Exception as e:
                    _LOGGER.info(f"  ?{params[:60]:60s} → ERROR: {e}")
                await asyncio.sleep(0.2)

            # Same for /sessions
            _LOGGER.info("")
            base_s = f"{WATFORD_API}/api/device-manager/client/sessions"
            session_params = [
                f"sessionId={session_id}",
                f"deviceId={device_id}",
                f"id={session_id}",
                f"device={device_id}",
                f"status=active",
                "limit=10",
            ]
            for params in session_params:
                url = f"{base_s}?{params}"
                try:
                    async with ws_session.get(url, headers=headers) as resp:
                        body = await resp.text()
                        status_str = f"{resp.status}"
                        if resp.status != 400:
                            status_str = f">>> {resp.status} <<< (DIFFERENT!)"
                        _LOGGER.info(f"  sessions?{params[:50]:50s} → {status_str} {body[:200]}")
                except Exception as e:
                    _LOGGER.info(f"  sessions?{params[:50]:50s} → ERROR: {e}")
                await asyncio.sleep(0.2)

            # Same for /devices
            _LOGGER.info("")
            base_d = f"{WATFORD_API}/api/device-manager/client/devices"
            device_params = [
                f"deviceId={device_id}",
                f"id={device_id}",
                f"serial={lr5_pro.serial}",
                f"spaceId={lr5_pro.camera_metadata.get('spaceId', '')}",
                "status=online",
                "limit=10",
            ]
            for params in device_params:
                url = f"{base_d}?{params}"
                try:
                    async with ws_session.get(url, headers=headers) as resp:
                        body = await resp.text()
                        status_str = f"{resp.status}"
                        if resp.status != 400:
                            status_str = f">>> {resp.status} <<< (DIFFERENT!)"
                        _LOGGER.info(f"  devices?{params[:50]:50s} → {status_str} {body[:200]}")
                except Exception as e:
                    _LOGGER.info(f"  devices?{params[:50]:50s} → ERROR: {e}")
                await asyncio.sleep(0.2)

            # ─── TEST 3: Check if there are additional APIs on the Camera API domains ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 3: Camera API domain WebRTC endpoints ───")
            cam_api1 = "https://rrntg65uwf.execute-api.us-east-1.amazonaws.com"
            cam_api2 = "https://7mnuil943l.execute-api.us-east-1.amazonaws.com"

            api_paths = [
                "/prod/v1/signaling",
                "/prod/v1/webrtc",
                f"/prod/v1/cameras/{device_id}/signaling",
                f"/prod/v1/cameras/{device_id}/stream",
                f"/prod/v1/cameras/{device_id}/webrtc",
                f"/prod/v1/cameras/{device_id}/offer",
                f"/prod/v1/cameras/{device_id}/session",
            ]
            for api_base in [cam_api1, cam_api2]:
                api_name = "API1" if api_base == cam_api1 else "API2"
                for path in api_paths:
                    try:
                        async with ws_session.get(f"{api_base}{path}", headers=headers) as resp:
                            body = await resp.text()
                            if resp.status != 403:  # Skip forbidden (auth issue)
                                _LOGGER.info(f"  {api_name} {path} → {resp.status} {body[:150]}")
                            else:
                                _LOGGER.info(f"  {api_name} {path} → 403")
                    except Exception as e:
                        _LOGGER.info(f"  {api_name} {path} → ERROR: {e}")
                    await asyncio.sleep(0.2)

        finally:
            await ws_session.close()

    finally:
        await account.disconnect()

    _LOGGER.info("")
    _LOGGER.info("Probe round 5 complete.")


if __name__ == "__main__":
    asyncio.run(main())
