"""Probe WebRTC signaling - Round 6.

BREAKTHROUGH: The WebSocket IS a relay! Server enriches messages with
sessionId and originatior. Relay direction: second connector → first connector.

This probe:
1. Fully tests the relay behavior (both directions, message enrichment)
2. Simulates a full WebRTC handshake between two clients
3. Tests if the camera connects when we connect first (role reversal)
4. Investigates the originatior field behavior
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
            Path(__file__).parent / "camera_webrtc_probe6_results.log", mode="w"
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


async def receive_one(ws, timeout=5):
    """Receive one message, return parsed JSON or raw data."""
    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                return json.loads(msg.data)
            except json.JSONDecodeError:
                return msg.data
        elif msg.type == aiohttp.WSMsgType.BINARY:
            return msg.data
        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
            return {"_ws_close": True, "code": getattr(ws, "close_code", None)}
        return {"_type": str(msg.type), "data": str(msg.data)}
    except asyncio.TimeoutError:
        return None


async def main():
    _LOGGER.info("=" * 60)
    _LOGGER.info("WebRTC Signaling Probe - Round 6 (Relay Deep Dive)")
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
            # ─── TEST 1: Full relay characterization ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 1: Full relay characterization ───")
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"
            _LOGGER.info(f"Session: {session_id}")

            ws_a = await ws_session.ws_connect(ws_url)
            _LOGGER.info("Client A (1st connector / device role) connected")
            ws_b = await ws_session.ws_connect(ws_url)
            _LOGGER.info("Client B (2nd connector / client role) connected")

            # Test B → A (should work)
            _LOGGER.info("\n--- B→A test ---")
            await ws_b.send_str(json.dumps({"direction": "B-to-A", "seq": 1}))
            result = await receive_one(ws_a, timeout=5)
            _LOGGER.info(f"A received: {json.dumps(result, default=str) if result else 'nothing'}")

            # Test A → B (should fail based on previous probe)
            _LOGGER.info("\n--- A→B test ---")
            await ws_a.send_str(json.dumps({"direction": "A-to-B", "seq": 2}))
            result = await receive_one(ws_b, timeout=5)
            _LOGGER.info(f"B received: {json.dumps(result, default=str) if result else 'nothing'}")

            # Test B → A again with more data
            _LOGGER.info("\n--- B→A with rich message ---")
            rich_msg = {
                "type": "offer",
                "sdp": "v=0\r\no=- 123 456 IN IP4 0.0.0.0\r\n",
                "extra": "field",
            }
            await ws_b.send_str(json.dumps(rich_msg))
            result = await receive_one(ws_a, timeout=5)
            _LOGGER.info(f"A received: {json.dumps(result, default=str) if result else 'nothing'}")
            if result and isinstance(result, dict):
                _LOGGER.info(f"Server-added fields: sessionId={result.get('sessionId')}, originatior={result.get('originatior')}")

            # Test A → B with delay
            _LOGGER.info("\n--- A→B with delay ---")
            await asyncio.sleep(1)
            await ws_a.send_str(json.dumps({"direction": "A-to-B-delayed", "seq": 3}))
            result = await receive_one(ws_b, timeout=5)
            _LOGGER.info(f"B received: {json.dumps(result, default=str) if result else 'nothing (A→B still blocked)'}")

            # Test: can A receive if B sends after A sent?
            _LOGGER.info("\n--- Bidirectional sequence ---")
            await ws_a.send_str(json.dumps({"from": "A", "seq": 4}))
            await asyncio.sleep(0.5)
            await ws_b.send_str(json.dumps({"from": "B", "seq": 5}))
            # Check both
            result_a = await receive_one(ws_a, timeout=3)
            _LOGGER.info(f"A received: {json.dumps(result_a, default=str) if result_a else 'nothing'}")
            result_b = await receive_one(ws_b, timeout=3)
            _LOGGER.info(f"B received: {json.dumps(result_b, default=str) if result_b else 'nothing'}")

            # Connect a 3rd client
            _LOGGER.info("\n--- 3rd client test ---")
            ws_c = await ws_session.ws_connect(ws_url)
            _LOGGER.info("Client C (3rd connector) connected")
            await ws_c.send_str(json.dumps({"from": "C", "seq": 6}))
            result_a = await receive_one(ws_a, timeout=3)
            _LOGGER.info(f"A received from C: {json.dumps(result_a, default=str) if result_a else 'nothing'}")
            result_b = await receive_one(ws_b, timeout=3)
            _LOGGER.info(f"B received from C: {json.dumps(result_b, default=str) if result_b else 'nothing'}")
            await ws_c.close()

            # Test binary message relay
            _LOGGER.info("\n--- Binary relay test ---")
            await ws_b.send_bytes(b"\x00\x01\x02\x03test")
            result = await receive_one(ws_a, timeout=5)
            _LOGGER.info(f"A received binary: {result}")

            await ws_a.close()
            await ws_b.close()

            await asyncio.sleep(2)

            # ─── TEST 2: Simulate full WebRTC handshake between two clients ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 2: Full WebRTC handshake simulation ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"
            _LOGGER.info(f"Session: {session_id}")

            # Create "device" side (first connector)
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

            pc_device = RTCPeerConnection(configuration=config)
            pc_device.addTransceiver("video", direction="sendonly")
            pc_device.addTransceiver("audio", direction="sendonly")

            pc_client = RTCPeerConnection(configuration=config)
            pc_client.addTransceiver("video", direction="recvonly")
            pc_client.addTransceiver("audio", direction="recvonly")

            # Connect WebSockets
            ws_device = await ws_session.ws_connect(ws_url)  # First = device
            ws_client = await ws_session.ws_connect(ws_url)  # Second = client
            _LOGGER.info("Both WS connected (device first, client second)")

            # Client creates offer
            offer = await pc_client.createOffer()
            await pc_client.setLocalDescription(offer)
            sdp_offer = pc_client.localDescription.sdp
            _LOGGER.info(f"Client SDP offer created ({len(sdp_offer)} chars)")

            # Client sends offer via relay (B→A direction)
            offer_msg = json.dumps({"type": "offer", "sdp": sdp_offer})
            await ws_client.send_str(offer_msg)
            _LOGGER.info("Client sent offer through relay")

            # Device receives offer
            result = await receive_one(ws_device, timeout=5)
            if result and isinstance(result, dict) and result.get("sdp"):
                _LOGGER.info(f"Device received offer!")
                _LOGGER.info(f"  Server-added: sessionId={result.get('sessionId')}, originatior={result.get('originatior')}")
                _LOGGER.info(f"  SDP first 200 chars: {result['sdp'][:200]}")

                # Device sets remote description
                await pc_device.setRemoteDescription(
                    RTCSessionDescription(sdp=result["sdp"], type="offer")
                )
                _LOGGER.info("Device set remote description")

                # Device creates answer
                answer = await pc_device.createAnswer()
                await pc_device.setLocalDescription(answer)
                sdp_answer = pc_device.localDescription.sdp
                _LOGGER.info(f"Device SDP answer created ({len(sdp_answer)} chars)")

                # Device sends answer via relay (A→B direction - will this work?)
                answer_msg = json.dumps({"type": "answer", "sdp": sdp_answer})
                await ws_device.send_str(answer_msg)
                _LOGGER.info("Device sent answer through relay")

                # Client receives answer?
                result2 = await receive_one(ws_client, timeout=5)
                if result2 and isinstance(result2, dict) and result2.get("sdp"):
                    _LOGGER.info(">>> CLIENT RECEIVED ANSWER! Full handshake works! <<<")
                    _LOGGER.info(f"  Server-added: sessionId={result2.get('sessionId')}, originatior={result2.get('originatior')}")

                    # Set remote description on client
                    await pc_client.setRemoteDescription(
                        RTCSessionDescription(sdp=result2["sdp"], type="answer")
                    )
                    _LOGGER.info("Client set remote description - handshake complete!")
                else:
                    _LOGGER.info(f"Client did NOT receive answer (A→B blocked): {result2}")
                    _LOGGER.info("CONCLUSION: Relay is unidirectional (client→device only)")
                    _LOGGER.info("The camera must use a different mechanism to send the answer")
            else:
                _LOGGER.info(f"Device did NOT receive offer: {result}")

            await pc_device.close()
            await pc_client.close()
            await ws_device.close()
            await ws_client.close()

            await asyncio.sleep(2)

            # ─── TEST 3: Connect and wait for camera (real device) ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 3: Connect EARLY, wait for camera, then send offer ───")
            _LOGGER.info("(Camera may need time to connect after autoStart)")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"
            _LOGGER.info(f"Session: {session_id}")

            # Connect immediately (we'll be the first connector = device role)
            # Then the camera might connect as second = client role
            # In that case, the camera would send us the offer!
            ws_real = await ws_session.ws_connect(ws_url)
            _LOGGER.info("Connected (first connector). Listening for camera messages for 45s...")

            deadline = time.monotonic() + 45
            msg_count = 0
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(ws_real.receive(), timeout=remaining)
                    msg_count += 1
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        _LOGGER.info(f"  MSG #{msg_count} TEXT: {msg.data[:500]}")
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        _LOGGER.info(f"  MSG #{msg_count} BINARY ({len(msg.data)}b): {msg.data[:100].hex()}")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                        _LOGGER.info(f"  MSG #{msg_count} CLOSE: code={getattr(ws_real, 'close_code', '?')}")
                        break
                    else:
                        _LOGGER.info(f"  MSG #{msg_count} type={msg.type}")
                except asyncio.TimeoutError:
                    break

            _LOGGER.info(f"Received {msg_count} messages in 45s")
            try:
                await ws_real.close()
            except Exception:
                pass

        finally:
            await ws_session.close()

    finally:
        await account.disconnect()

    _LOGGER.info("")
    _LOGGER.info("Probe round 6 complete.")


if __name__ == "__main__":
    asyncio.run(main())
