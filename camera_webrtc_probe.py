"""Probe WebRTC signaling protocol for LR5 Pro camera.

Sends real SDP offers in multiple candidate formats through the WebSocket
signaling channel to discover which message format the camera expects.
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
import cbor2
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer, RTCSessionDescription

from pylitterbot import Account

WATFORD_API = "https://watford.ienso-dev.com"

# Logging to both console and file
LOG_FILE = Path(__file__).parent / "camera_webrtc_probe_results.log"
_LOGGER = logging.getLogger("webrtc_probe")
_LOGGER.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.INFO)
_console.setFormatter(_fmt)
_file = logging.FileHandler(LOG_FILE, mode="w")
_file.setLevel(logging.DEBUG)
_file.setFormatter(_fmt)
_LOGGER.addHandler(_console)
_LOGGER.addHandler(_file)


def load_credentials():
    """Load credentials from ~/.config/whisker/credentials."""
    c = configparser.ConfigParser()
    c.read(Path.home() / ".config" / "whisker" / "credentials")
    return c.get("whisker", "email"), c.get("whisker", "password")


async def generate_session(websession, headers, device_id):
    """Generate a fresh WebRTC session (60s JWT expiry)."""
    url = f"{WATFORD_API}/api/device-manager/client/generate-session/{device_id}?autoStart=true"
    async with websession.get(url, headers=headers) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"generate-session failed: {resp.status} {body}")
        return await resp.json()


async def create_peer_connection(session_data):
    """Create RTCPeerConnection with TURN/STUN from session data."""
    turn = session_data["turnServer"]
    ice_servers = []

    # STUN
    if turn.get("stunUrl"):
        ice_servers.append(RTCIceServer(urls=[turn["stunUrl"]]))

    # TURN
    turn_urls = turn.get("turnUrl", [])
    if isinstance(turn_urls, str):
        turn_urls = [turn_urls]
    if turn_urls:
        ice_servers.append(
            RTCIceServer(
                urls=turn_urls,
                username=turn["username"],
                credential=turn["password"],
            )
        )

    config = RTCConfiguration(iceServers=ice_servers)
    pc = RTCPeerConnection(configuration=config)

    # Add recvonly transceivers for video and audio
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")

    return pc


async def try_format(
    label: str,
    websession: aiohttp.ClientSession,
    headers: dict,
    device_id: str,
    build_message,
):
    """Try a single signaling format. Returns (success, response_data)."""
    _LOGGER.info("=" * 60)
    _LOGGER.info(f"FORMAT {label}")
    _LOGGER.info("=" * 60)

    # 1. Fresh session
    _LOGGER.info("Generating fresh session...")
    try:
        session_data = await generate_session(websession, headers, device_id)
    except Exception as e:
        _LOGGER.error(f"Session generation failed: {e}")
        return False, None
    session_id = session_data["sessionId"]
    token = session_data["sessionToken"]
    _LOGGER.info(f"Session: {session_id}")

    # 2. Create peer connection
    pc = await create_peer_connection(session_data)
    track_received = asyncio.Event()
    track_info = {}

    @pc.on("track")
    def on_track(track):
        _LOGGER.info(f"TRACK RECEIVED: kind={track.kind}, id={track.id}")
        track_info["kind"] = track.kind
        track_info["id"] = track.id
        track_received.set()

    @pc.on("connectionstatechange")
    async def on_state_change():
        _LOGGER.info(f"Connection state: {pc.connectionState}")

    @pc.on("iceconnectionstatechange")
    async def on_ice_state_change():
        _LOGGER.info(f"ICE connection state: {pc.iceConnectionState}")

    # 3. Create SDP offer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    sdp_text = pc.localDescription.sdp
    _LOGGER.debug(f"SDP offer (first 500 chars):\n{sdp_text[:500]}")

    # 4. Build the message for this format
    msg_data, is_binary = build_message(sdp_text, session_id)

    # 5. Connect WebSocket and send
    ws_url = f"{session_data['signalingURL']}?accessToken={token}"
    _LOGGER.info(f"Connecting WebSocket...")

    success = False
    response_data = None

    try:
        async with websession.ws_connect(ws_url) as ws:
            _LOGGER.info("WebSocket connected")

            # Send the message
            if is_binary:
                _LOGGER.info(f"Sending BINARY message ({len(msg_data)} bytes)")
                _LOGGER.debug(f"Hex (first 200): {msg_data[:200].hex()}")
                await ws.send_bytes(msg_data)
            else:
                preview = msg_data if len(msg_data) <= 500 else msg_data[:500] + "..."
                _LOGGER.info(f"Sending TEXT message ({len(msg_data)} chars)")
                _LOGGER.debug(f"Message preview: {preview}")
                await ws.send_str(msg_data)

            # 6. Listen for response (10 seconds)
            _LOGGER.info("Listening for response (10s)...")
            deadline = time.monotonic() + 10
            msg_count = 0

            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                msg_count += 1
                _LOGGER.info(f"  Response #{msg_count}: type={msg.type}")

                if msg.type == aiohttp.WSMsgType.TEXT:
                    _LOGGER.info(f"  TEXT ({len(msg.data)} chars): {msg.data[:2000]}")
                    response_data = msg.data
                    success = True

                    # Try to parse as JSON and check for SDP answer
                    try:
                        parsed = json.loads(msg.data)
                        _LOGGER.info(f"  Parsed JSON keys: {list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}")
                        await _handle_answer(pc, parsed, ws, label)
                    except json.JSONDecodeError:
                        _LOGGER.info("  (not JSON)")

                elif msg.type == aiohttp.WSMsgType.BINARY:
                    data = msg.data
                    _LOGGER.info(f"  BINARY ({len(data)} bytes): {data[:200].hex()}")
                    response_data = data
                    success = True

                    # Try CBOR
                    try:
                        decoded = cbor2.loads(data)
                        _LOGGER.info(f"  CBOR decoded: {json.dumps(decoded, default=str)[:2000]}")
                        await _handle_answer(pc, decoded, ws, label)
                    except Exception:
                        pass
                    # Try JSON
                    try:
                        decoded = json.loads(data)
                        _LOGGER.info(f"  JSON decoded: {json.dumps(decoded)[:2000]}")
                        await _handle_answer(pc, decoded, ws, label)
                    except Exception:
                        pass

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    _LOGGER.info(f"  WebSocket closed/error: {msg.data}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSE:
                    close_code = ws.close_code
                    _LOGGER.info(f"  WebSocket CLOSE frame: code={close_code}")
                    break

            if msg_count == 0:
                _LOGGER.info("  No response received in 10 seconds")

            # If we got a successful answer, wait for track
            if success and track_info:
                _LOGGER.info("Waiting for media track (5s)...")
                try:
                    await asyncio.wait_for(track_received.wait(), timeout=5)
                    _LOGGER.info(f"Media track: {track_info}")
                except asyncio.TimeoutError:
                    _LOGGER.info("No media track in 5s (may need more time)")

    except Exception as e:
        _LOGGER.error(f"WebSocket error: {e}")
    finally:
        await pc.close()

    return success, response_data


async def _handle_answer(pc, parsed, ws, label):
    """If parsed contains an SDP answer, set it and continue handshake."""
    if not isinstance(parsed, dict):
        return

    # Look for SDP answer in various locations
    sdp_answer = None
    answer_type = None

    # Direct: {"type": "answer", "sdp": "..."}
    if parsed.get("type") == "answer" and parsed.get("sdp"):
        sdp_answer = parsed["sdp"]
        answer_type = "direct"

    # KVS-style: {"action": "SDP_ANSWER", "messagePayload": "<base64>"}
    elif parsed.get("action") == "SDP_ANSWER" and parsed.get("messagePayload"):
        try:
            payload = json.loads(base64.b64decode(parsed["messagePayload"]))
            sdp_answer = payload.get("sdp")
            answer_type = "kvs-base64"
        except Exception:
            pass

    # Nested: {"action": "answer", "data": {"type": "answer", "sdp": "..."}}
    elif isinstance(parsed.get("data"), dict) and parsed["data"].get("sdp"):
        sdp_answer = parsed["data"]["sdp"]
        answer_type = "nested-data"

    # Generic messagePayload (non-base64)
    elif parsed.get("messagePayload") and isinstance(parsed["messagePayload"], str):
        # Could be raw SDP or JSON string
        payload = parsed["messagePayload"]
        if payload.startswith("v=0"):
            sdp_answer = payload
            answer_type = "raw-payload"
        else:
            try:
                inner = json.loads(payload)
                if isinstance(inner, dict) and inner.get("sdp"):
                    sdp_answer = inner["sdp"]
                    answer_type = "json-payload"
            except Exception:
                pass

    if sdp_answer:
        _LOGGER.info(f"SDP ANSWER FOUND (format: {answer_type})")
        _LOGGER.info(f"SDP answer (first 300 chars): {sdp_answer[:300]}")
        try:
            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=sdp_answer, type="answer")
            )
            _LOGGER.info("Remote description set successfully!")

            # Listen for ICE candidates
            _LOGGER.info("Listening for ICE candidates (5s)...")
            deadline = time.monotonic() + 5
            ice_count = 0
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        _LOGGER.info(f"  ICE msg: {msg.data[:500]}")
                        ice_count += 1
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        try:
                            decoded = cbor2.loads(msg.data)
                            _LOGGER.info(f"  ICE msg (CBOR): {json.dumps(decoded, default=str)[:500]}")
                        except Exception:
                            _LOGGER.info(f"  ICE msg (binary): {msg.data[:200].hex()}")
                        ice_count += 1
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                        break
                except asyncio.TimeoutError:
                    break
            _LOGGER.info(f"Received {ice_count} additional messages after answer")

        except Exception as e:
            _LOGGER.error(f"Failed to set remote description: {e}")
    else:
        _LOGGER.debug("No SDP answer found in response")


# ─── Message format builders ───

def build_kvs_style(sdp: str, session_id: str):
    """Format A: KVS-style with base64-encoded messagePayload."""
    payload = base64.b64encode(
        json.dumps({"type": "offer", "sdp": sdp}).encode()
    ).decode()
    msg = json.dumps({
        "action": "SDP_OFFER",
        "messagePayload": payload,
    })
    return msg, False


def build_simple_json(sdp: str, session_id: str):
    """Format B: Simple JSON offer."""
    msg = json.dumps({"type": "offer", "sdp": sdp})
    return msg, False


def build_json_with_session(sdp: str, session_id: str):
    """Format C: JSON offer with sessionId."""
    msg = json.dumps({"type": "offer", "sdp": sdp, "sessionId": session_id})
    return msg, False


def build_ienso_action(sdp: str, session_id: str):
    """Format D: Ienso-style action wrapper."""
    msg = json.dumps({
        "action": "offer",
        "data": {"type": "offer", "sdp": sdp},
    })
    return msg, False


def build_cbor_kvs(sdp: str, session_id: str):
    """Format E: CBOR-encoded KVS-style message."""
    payload = base64.b64encode(
        json.dumps({"type": "offer", "sdp": sdp}).encode()
    ).decode()
    data = cbor2.dumps({
        "action": "SDP_OFFER",
        "messagePayload": payload,
    })
    return data, True


def build_cbor_simple(sdp: str, session_id: str):
    """Format F: CBOR-encoded simple offer."""
    data = cbor2.dumps({"type": "offer", "sdp": sdp})
    return data, True


def build_raw_sdp(sdp: str, session_id: str):
    """Format G: Raw SDP text (no JSON wrapper)."""
    return sdp, False


# All formats in order of likelihood
FORMATS = [
    ("A: KVS-style (base64 payload)", build_kvs_style),
    ("B: Simple JSON", build_simple_json),
    ("C: JSON + sessionId", build_json_with_session),
    ("D: Ienso action wrapper", build_ienso_action),
    ("E: CBOR KVS-style", build_cbor_kvs),
    ("F: CBOR simple", build_cbor_simple),
    ("G: Raw SDP text", build_raw_sdp),
]


async def main():
    _LOGGER.info("LR5 Pro WebRTC Signaling Protocol Probe")
    _LOGGER.info(f"Log file: {LOG_FILE}")
    _LOGGER.info("")

    # Authenticate
    username, password = load_credentials()
    account = Account()

    try:
        await account.connect(
            username=username, password=password, load_robots=True, load_pets=False
        )

        # Find LR5 Pro
        lr5_pro = None
        for robot in account.robots:
            if hasattr(robot, "camera_metadata") and robot.camera_metadata:
                lr5_pro = robot
                break

        if not lr5_pro:
            _LOGGER.error("No LR5 Pro with camera found")
            return

        device_id = lr5_pro.camera_metadata["deviceId"]
        _LOGGER.info(f"Camera device: {device_id}")

        # Get bearer token
        id_token = await account.session.async_get_id_token()
        headers = {"Authorization": f"Bearer {id_token}"}

        # Try each format
        ws_session = aiohttp.ClientSession()
        try:
            for label, builder in FORMATS:
                success, response = await try_format(
                    label, ws_session, headers, device_id, builder
                )

                if success:
                    _LOGGER.info("")
                    _LOGGER.info("*" * 60)
                    _LOGGER.info(f"SUCCESS: Format {label} got a response!")
                    _LOGGER.info("*" * 60)
                    # Stop probing - focus on this format
                    break

                # Delay between attempts (rate limiting + token refresh)
                _LOGGER.info("Waiting 3 seconds before next attempt...")
                await asyncio.sleep(3)

                # Refresh token if needed (Cognito tokens last ~1hr, but be safe)
                id_token = await account.session.async_get_id_token()
                headers = {"Authorization": f"Bearer {id_token}"}
            else:
                _LOGGER.info("")
                _LOGGER.info("=" * 60)
                _LOGGER.info("All formats tried - none received a response")
                _LOGGER.info("=" * 60)
        finally:
            await ws_session.close()

    finally:
        await account.disconnect()

    _LOGGER.info(f"\nFull log saved to: {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
