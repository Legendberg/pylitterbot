"""Probe WebRTC signaling - Round 2.

Hypotheses to test:
1. Camera needs time to join signaling room after generate-session
2. ICE gathering must complete before sending offer
3. Camera sends a message first (we need to wait longer)
4. A registration/join message is needed before SDP
5. WebSocket subprotocol is required
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
            Path(__file__).parent / "camera_webrtc_probe2_results.log", mode="w"
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
            raise RuntimeError(f"generate-session failed: {resp.status} {body}")
        return await resp.json()


async def listen_ws(ws, seconds, label=""):
    """Listen for any WebSocket message for N seconds."""
    prefix = f"[{label}] " if label else ""
    deadline = time.monotonic() + seconds
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
            _LOGGER.info(f"{prefix}MSG #{count} TEXT ({len(msg.data)} chars): {msg.data[:2000]}")
        elif msg.type == aiohttp.WSMsgType.BINARY:
            _LOGGER.info(f"{prefix}MSG #{count} BINARY ({len(msg.data)} bytes): {msg.data[:200].hex()}")
            # Try JSON
            try:
                _LOGGER.info(f"{prefix}  JSON: {json.loads(msg.data)}")
            except Exception:
                pass
            # Try CBOR
            try:
                import cbor2
                _LOGGER.info(f"{prefix}  CBOR: {cbor2.loads(msg.data)}")
            except Exception:
                pass
        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
            _LOGGER.info(f"{prefix}MSG #{count} CLOSE/ERROR: code={getattr(ws, 'close_code', '?')} data={msg.data}")
            return count
        else:
            _LOGGER.info(f"{prefix}MSG #{count} type={msg.type} data={msg.data}")

    _LOGGER.info(f"{prefix}{'No messages' if count == 0 else f'{count} messages'} in {seconds}s")
    return count


async def create_pc_with_gathered_sdp(session_data):
    """Create PC, wait for ICE gathering to complete, return full SDP."""
    turn = session_data["turnServer"]
    ice_servers = []
    if turn.get("stunUrl"):
        ice_servers.append(RTCIceServer(urls=[turn["stunUrl"]]))
    turn_urls = turn.get("turnUrl", [])
    if isinstance(turn_urls, str):
        turn_urls = [turn_urls]
    if turn_urls:
        ice_servers.append(RTCIceServer(
            urls=turn_urls,
            username=turn["username"],
            credential=turn["password"],
        ))

    config = RTCConfiguration(iceServers=ice_servers)
    pc = RTCPeerConnection(configuration=config)
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Wait for ICE gathering to complete (up to 10s)
    if pc.iceGatheringState != "complete":
        _LOGGER.info(f"ICE gathering state: {pc.iceGatheringState}, waiting...")
        gathering_done = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def on_gathering_change():
            _LOGGER.info(f"ICE gathering state changed: {pc.iceGatheringState}")
            if pc.iceGatheringState == "complete":
                gathering_done.set()

        try:
            await asyncio.wait_for(gathering_done.wait(), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.info("ICE gathering didn't complete in 10s, proceeding anyway")

    sdp = pc.localDescription.sdp
    _LOGGER.info(f"SDP ready ({len(sdp)} chars), ICE state: {pc.iceGatheringState}")
    return pc, sdp


async def main():
    _LOGGER.info("=" * 60)
    _LOGGER.info("WebRTC Signaling Probe - Round 2")
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
            _LOGGER.error("No LR5 Pro with camera found")
            return

        device_id = lr5_pro.camera_metadata["deviceId"]
        _LOGGER.info(f"Device: {device_id}")

        id_token = await account.session.async_get_id_token()
        headers = {"Authorization": f"Bearer {id_token}"}
        ws_session = aiohttp.ClientSession()

        try:
            # ─── TEST 1: Long passive listen (30s) ───
            # Maybe the camera sends something first after connecting
            _LOGGER.info("")
            _LOGGER.info("─── TEST 1: Passive listen 30s (no messages sent) ───")
            session_data = await generate_session(ws_session, headers, device_id)
            _LOGGER.info(f"Session: {session_data['sessionId']}")
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"
            async with ws_session.ws_connect(ws_url) as ws:
                _LOGGER.info("Connected. Listening passively for 30s...")
                await listen_ws(ws, 30, "passive")

            await asyncio.sleep(2)

            # ─── TEST 2: Send join/register messages before SDP ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 2: Registration messages before SDP ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            _LOGGER.info(f"Session: {session_id}")
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"

            join_messages = [
                ("join", json.dumps({"action": "join", "sessionId": session_id})),
                ("register", json.dumps({"type": "register", "sessionId": session_id})),
                ("connect", json.dumps({"action": "connect", "deviceId": device_id, "sessionId": session_id})),
                ("start", json.dumps({"type": "start-stream", "sessionId": session_id, "deviceId": device_id})),
                ("hello", json.dumps({"type": "hello", "role": "client"})),
                ("init", json.dumps({"action": "init", "clientId": session_id, "deviceId": device_id})),
            ]

            async with ws_session.ws_connect(ws_url) as ws:
                _LOGGER.info("Connected. Trying registration messages...")
                for name, msg in join_messages:
                    _LOGGER.info(f"  Sending '{name}': {msg[:200]}")
                    await ws.send_str(msg)
                    # Quick listen after each
                    got = await listen_ws(ws, 3, name)
                    if got > 0:
                        _LOGGER.info(f"  >>> GOT RESPONSE after '{name}'! <<<")
                        # Listen more
                        await listen_ws(ws, 10, f"{name}-extended")
                        break

            await asyncio.sleep(2)

            # ─── TEST 3: Wait then send SDP (camera may need time to join) ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 3: Wait 10s, then send SDP offer (with gathered ICE) ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            _LOGGER.info(f"Session: {session_id}")

            # Create PC and gather ICE candidates
            pc, sdp = await create_pc_with_gathered_sdp(session_data)
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"

            try:
                async with ws_session.ws_connect(ws_url) as ws:
                    _LOGGER.info("Connected. Waiting 10s for camera to join room...")
                    got = await listen_ws(ws, 10, "pre-wait")
                    if got > 0:
                        _LOGGER.info("Got message during wait!")

                    # Now send the offer in the most likely formats
                    offers = [
                        ("simple", json.dumps({"type": "offer", "sdp": sdp})),
                        ("kvs", json.dumps({
                            "action": "SDP_OFFER",
                            "messagePayload": base64.b64encode(
                                json.dumps({"type": "offer", "sdp": sdp}).encode()
                            ).decode(),
                        })),
                    ]
                    for name, msg in offers:
                        _LOGGER.info(f"Sending offer ({name}, {len(msg)} chars)...")
                        await ws.send_str(msg)
                        got = await listen_ws(ws, 8, name)
                        if got > 0:
                            _LOGGER.info(f">>> RESPONSE to {name}! <<<")
                            await listen_ws(ws, 15, f"{name}-extended")
                            break
            finally:
                await pc.close()

            await asyncio.sleep(2)

            # ─── TEST 4: Try WebSocket subprotocols ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 4: WebSocket subprotocols ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            _LOGGER.info(f"Session: {session_data['sessionId']}")
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"

            subprotocols = ["webrtc", "json", "signaling", "ienso", "watford"]
            for proto in subprotocols:
                _LOGGER.info(f"  Trying subprotocol: {proto}")
                try:
                    async with ws_session.ws_connect(
                        ws_url, protocols=[proto]
                    ) as ws:
                        _LOGGER.info(f"  Connected with protocol={ws.protocol}")
                        msg = json.dumps({"type": "offer", "sdp": "test"})
                        await ws.send_str(msg)
                        got = await listen_ws(ws, 5, proto)
                        if got > 0:
                            _LOGGER.info(f">>> SUBPROTOCOL {proto} WORKS! <<<")
                            break
                except Exception as e:
                    _LOGGER.info(f"  Subprotocol {proto} failed: {e}")

            await asyncio.sleep(2)

            # ─── TEST 5: Multiple sessions - one listens, one sends ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 5: Connect WS, send ping/empty, check server behavior ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            session_id = session_data["sessionId"]
            _LOGGER.info(f"Session: {session_id}")
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"

            async with ws_session.ws_connect(ws_url) as ws:
                _LOGGER.info("Connected. Sending minimal messages...")

                # Try empty/minimal messages to provoke error responses
                test_msgs = [
                    ("empty-obj", json.dumps({})),
                    ("ping-text", "ping"),
                    ("empty-str", ""),
                    ("number", "1"),
                    ("array", json.dumps([])),
                    ("null", "null"),
                    ("help", json.dumps({"action": "help"})),
                    ("list-actions", json.dumps({"type": "list"})),
                ]
                for name, msg in test_msgs:
                    if msg:  # skip truly empty
                        _LOGGER.info(f"  Sending '{name}': {msg[:100]}")
                        await ws.send_str(msg)
                    got = await listen_ws(ws, 2, name)
                    if got > 0:
                        _LOGGER.info(f">>> RESPONSE to '{name}'! <<<")
                        await listen_ws(ws, 10, f"{name}-extended")
                        break

                # Also try binary ping
                _LOGGER.info("  Sending WS ping frame...")
                await ws.ping(b"probe")
                got = await listen_ws(ws, 3, "ws-ping")

            # ─── TEST 6: Try generating session WITHOUT autoStart ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 6: Session without autoStart, then listen ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            url_no_auto = f"{WATFORD_API}/api/device-manager/client/generate-session/{device_id}"
            async with ws_session.get(url_no_auto, headers=headers) as resp:
                if resp.status == 200:
                    session_data = await resp.json()
                    _LOGGER.info(f"Session (no autoStart): {session_data['sessionId']}")
                    _LOGGER.info(f"autoStart in response: {session_data.get('autoStart')}")
                    token = session_data["sessionToken"]
                    ws_url = f"{session_data['signalingURL']}?accessToken={token}"
                    async with ws_session.ws_connect(ws_url) as ws:
                        sdp_msg = json.dumps({"type": "offer", "sdp": "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"})
                        await ws.send_str(sdp_msg)
                        await listen_ws(ws, 8, "no-autostart")
                else:
                    _LOGGER.info(f"Session without autoStart: {resp.status}")
                    body = await resp.text()
                    _LOGGER.info(f"Body: {body[:500]}")

        finally:
            await ws_session.close()

    finally:
        await account.disconnect()

    _LOGGER.info("")
    _LOGGER.info("Probe round 2 complete.")


if __name__ == "__main__":
    asyncio.run(main())
