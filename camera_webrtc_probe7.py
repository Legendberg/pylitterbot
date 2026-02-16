"""Probe WebRTC signaling - Round 7.

Test if relay routing depends on message type:
- "offer" from client(B) → device(A)
- "answer" from device(A) → client(B)
- "candidate" bidirectional

Also: complete WebRTC handshake between two local peers via the relay.
"""

import asyncio
import configparser
import json
import logging
import sys
import time
from pathlib import Path

import aiohttp
from aiortc import (
    RTCPeerConnection, RTCConfiguration, RTCIceServer,
    RTCSessionDescription,
)

from pylitterbot import Account

WATFORD_API = "https://watford.ienso-dev.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent / "camera_webrtc_probe7_results.log", mode="w"
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


async def recv(ws, timeout=5, label=""):
    """Receive one message, return parsed JSON or None."""
    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
        if msg.type == aiohttp.WSMsgType.TEXT:
            data = json.loads(msg.data) if msg.data.startswith("{") else msg.data
            _LOGGER.info(f"  [{label}] RECEIVED: {json.dumps(data, default=str)[:500]}")
            return data
        elif msg.type == aiohttp.WSMsgType.BINARY:
            _LOGGER.info(f"  [{label}] BINARY ({len(msg.data)}b): {msg.data[:100].hex()}")
            return msg.data
        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
            _LOGGER.info(f"  [{label}] CLOSE: code={getattr(ws, 'close_code', '?')}")
            return {"_close": True}
        return None
    except asyncio.TimeoutError:
        _LOGGER.info(f"  [{label}] (no message in {timeout}s)")
        return None


async def main():
    _LOGGER.info("=" * 60)
    _LOGGER.info("WebRTC Signaling Probe - Round 7 (Type-Based Routing)")
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
            # ─── TEST 1: Type-based routing test ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 1: Does message 'type' affect routing direction? ───")
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"
            _LOGGER.info(f"Session: {session_id}")

            ws_device = await ws_session.ws_connect(ws_url)  # First = device
            ws_client = await ws_session.ws_connect(ws_url)  # Second = client
            _LOGGER.info("Device(1st) and Client(2nd) connected")

            # Test: device sends type=answer → does client receive?
            _LOGGER.info("\n--- Device sends type='answer' → Client? ---")
            await ws_device.send_str(json.dumps({"type": "answer", "sdp": "fake-answer"}))
            r = await recv(ws_client, 5, "client-hears-answer")

            # Test: device sends type=candidate → does client receive?
            _LOGGER.info("\n--- Device sends type='candidate' → Client? ---")
            await ws_device.send_str(json.dumps({"type": "candidate", "candidate": "fake"}))
            r = await recv(ws_client, 5, "client-hears-candidate")

            # Test: device sends type=SDP_ANSWER (KVS style) → client?
            _LOGGER.info("\n--- Device sends type='SDP_ANSWER' → Client? ---")
            await ws_device.send_str(json.dumps({"action": "SDP_ANSWER", "payload": "fake"}))
            r = await recv(ws_client, 5, "client-hears-sdp-answer")

            # Test: device sends raw string → client?
            _LOGGER.info("\n--- Device sends raw string → Client? ---")
            await ws_device.send_str("hello-from-device")
            r = await recv(ws_client, 5, "client-hears-raw")

            # Verify B→A still works
            _LOGGER.info("\n--- Verify client→device still works ---")
            await ws_client.send_str(json.dumps({"type": "offer", "test": True}))
            r = await recv(ws_device, 5, "device-hears-offer")

            await ws_device.close()
            await ws_client.close()

            await asyncio.sleep(2)

            # ─── TEST 2: Full WebRTC handshake via relay ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 2: Full WebRTC handshake between two local peers via relay ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"
            _LOGGER.info(f"Session: {session_id}")

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

            # Create peer connections
            pc_client = RTCPeerConnection(configuration=config)
            pc_client.addTransceiver("video", direction="recvonly")
            pc_client.addTransceiver("audio", direction="recvonly")

            # Connect WebSockets (device first, then client)
            ws_device = await ws_session.ws_connect(ws_url)
            ws_client = await ws_session.ws_connect(ws_url)
            _LOGGER.info("Both WS connected")

            # Client creates and sends offer
            offer = await pc_client.createOffer()
            await pc_client.setLocalDescription(offer)

            # Wait for ICE gathering
            if pc_client.iceGatheringState != "complete":
                done = asyncio.Event()
                @pc_client.on("icegatheringstatechange")
                def on_ice():
                    if pc_client.iceGatheringState == "complete":
                        done.set()
                try:
                    await asyncio.wait_for(done.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass

            sdp_offer = pc_client.localDescription.sdp
            _LOGGER.info(f"Client offer ready ({len(sdp_offer)} chars), ICE: {pc_client.iceGatheringState}")

            # Send offer via relay
            await ws_client.send_str(json.dumps({"type": "offer", "sdp": sdp_offer}))
            _LOGGER.info("Client sent offer")

            # Device receives offer
            r = await recv(ws_device, 10, "device-recv-offer")
            if r and isinstance(r, dict) and r.get("sdp"):
                _LOGGER.info(f"Device got offer! originatior={r.get('originatior')}")
                _LOGGER.info(f"SDP (first 200): {r['sdp'][:200]}")

                # Now check if device can respond (A→B might work with proper type)
                # Send a test answer
                await ws_device.send_str(json.dumps({
                    "type": "answer",
                    "sdp": "v=0\r\nfake-answer-sdp\r\n",
                }))
                _LOGGER.info("Device sent test answer")

                r2 = await recv(ws_client, 5, "client-recv-answer")
                if r2 and isinstance(r2, dict):
                    _LOGGER.info(f">>> CLIENT GOT ANSWER! Type-based routing works! <<<")
                else:
                    _LOGGER.info("Client did NOT receive answer from device")
                    _LOGGER.info("CONCLUSION: Relay is strictly client→device (unidirectional)")
                    _LOGGER.info("")
                    _LOGGER.info("The SDP answer must come through a different channel.")
                    _LOGGER.info("Possible channels: REST API polling, separate WS, or")
                    _LOGGER.info("the relay might work differently with the real camera firmware.")
            else:
                _LOGGER.info("Device did NOT receive offer - relay broken")

            await pc_client.close()
            try:
                await ws_device.close()
                await ws_client.close()
            except Exception:
                pass

            await asyncio.sleep(2)

            # ─── TEST 3: Connect as first, wait for camera to connect as second ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 3: We connect FIRST (device role), wait for camera ───")
            _LOGGER.info("If autoStart makes camera connect second, it would send us an offer")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"
            _LOGGER.info(f"Session: {session_id}")

            ws_wait = await ws_session.ws_connect(ws_url)
            _LOGGER.info("Connected first. Waiting up to 50s for camera...")

            deadline = time.monotonic() + 50
            msg_count = 0
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                r = await recv(ws_wait, min(remaining, 10), f"wait-{msg_count}")
                if r is None:
                    continue
                msg_count += 1
                if isinstance(r, dict) and r.get("_close"):
                    _LOGGER.info(f"Connection closed after {time.monotonic() - (deadline - 50):.0f}s")
                    break
                if isinstance(r, dict) and r.get("type"):
                    _LOGGER.info(f">>> GOT MESSAGE WITH TYPE: {r.get('type')} <<<")
                    _LOGGER.info(f"Full message: {json.dumps(r, default=str)}")

            _LOGGER.info(f"Total: {msg_count} messages")
            try:
                await ws_wait.close()
            except Exception:
                pass

        finally:
            await ws_session.close()

    finally:
        await account.disconnect()

    _LOGGER.info("")
    _LOGGER.info("Probe round 7 complete.")


if __name__ == "__main__":
    asyncio.run(main())
