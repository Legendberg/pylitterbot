"""Probe WebRTC signaling - Round 8.

KEY FINDING: Relay IS bidirectional for JSON with type/action fields!
But TEST 2 failed - maybe timing, state machine, or message size issue.

This probe isolates the failure: test various sequences and message sizes.
Then attempt a real WebRTC handshake with the actual camera.
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
            Path(__file__).parent / "camera_webrtc_probe8_results.log", mode="w"
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
    """Receive one message with logging."""
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
    _LOGGER.info("WebRTC Signaling Probe - Round 8")
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
            return

        device_id = lr5_pro.camera_metadata["deviceId"]
        _LOGGER.info(f"Device: {device_id}")

        id_token = await account.session.async_get_id_token()
        headers = {"Authorization": f"Bearer {id_token}"}
        ws_session = aiohttp.ClientSession()

        try:
            # ─── TEST 1: Sequence matters? ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 1: Does client-sends-first break device→client? ───")
            session_data = await generate_session(ws_session, headers, device_id)
            sid = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"

            ws_d = await ws_session.ws_connect(ws_url)
            ws_c = await ws_session.ws_connect(ws_url)
            _LOGGER.info(f"Session: {sid}")

            # Step 1: Client sends first (like in real scenario)
            _LOGGER.info("Step 1: Client sends offer-like msg")
            await ws_c.send_str(json.dumps({"type": "offer", "sdp": "test-offer"}))
            r = await recv(ws_d, 5, "dev-gets-offer")

            # Step 2: Delay, then device sends answer
            _LOGGER.info("Step 2: Wait 2s, then device sends answer")
            await asyncio.sleep(2)
            await ws_d.send_str(json.dumps({"type": "answer", "sdp": "test-answer"}))
            r = await recv(ws_c, 5, "cli-gets-answer")
            if r:
                _LOGGER.info(">>> BIDIRECTIONAL after client-first! <<<")
            else:
                _LOGGER.info(">>> UNIDIRECTIONAL (device blocked after client sends first) <<<")

            await ws_d.close()
            await ws_c.close()
            await asyncio.sleep(1)

            # ─── TEST 2: Device sends FIRST, then exchange ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 2: Device sends first, then full exchange ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            sid = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"

            ws_d = await ws_session.ws_connect(ws_url)
            ws_c = await ws_session.ws_connect(ws_url)
            _LOGGER.info(f"Session: {sid}")

            # Device speaks first
            _LOGGER.info("Step 1: Device sends ready signal")
            await ws_d.send_str(json.dumps({"type": "ready"}))
            r = await recv(ws_c, 5, "cli-gets-ready")

            # Client sends offer
            _LOGGER.info("Step 2: Client sends offer")
            await ws_c.send_str(json.dumps({"type": "offer", "sdp": "test-offer"}))
            r = await recv(ws_d, 5, "dev-gets-offer")

            # Device sends answer
            _LOGGER.info("Step 3: Device sends answer")
            await asyncio.sleep(1)
            await ws_d.send_str(json.dumps({"type": "answer", "sdp": "test-answer"}))
            r = await recv(ws_c, 5, "cli-gets-answer")
            if r:
                _LOGGER.info(">>> FULL EXCHANGE WORKS (device-first) <<<")

            await ws_d.close()
            await ws_c.close()
            await asyncio.sleep(1)

            # ─── TEST 3: Large message relay ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 3: Large message relay (SDP-sized) ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            sid = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"

            ws_d = await ws_session.ws_connect(ws_url)
            ws_c = await ws_session.ws_connect(ws_url)

            # Device sends first (to establish bidirectional)
            await ws_d.send_str(json.dumps({"type": "ready"}))
            r = await recv(ws_c, 5, "ready")

            # Client sends LARGE SDP offer
            fake_sdp = "v=0\r\n" + "a=dummy-line\r\n" * 400  # ~6000 chars
            _LOGGER.info(f"Sending large offer ({len(fake_sdp)} chars)")
            await ws_c.send_str(json.dumps({"type": "offer", "sdp": fake_sdp}))
            r = await recv(ws_d, 5, "dev-large-offer")

            # Device sends large answer
            await asyncio.sleep(1)
            fake_answer = "v=0\r\n" + "a=answer-line\r\n" * 400
            await ws_d.send_str(json.dumps({"type": "answer", "sdp": fake_answer}))
            r = await recv(ws_c, 5, "cli-large-answer")
            if r:
                _LOGGER.info(">>> LARGE MESSAGES RELAY OK <<<")

            await ws_d.close()
            await ws_c.close()
            await asyncio.sleep(1)

            # ─── TEST 4: REAL WebRTC handshake via relay ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 4: Real WebRTC handshake via relay ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            sid = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"
            _LOGGER.info(f"Session: {sid}")

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

            # Create both peer connections
            pc_device = RTCPeerConnection(configuration=config)
            pc_device.addTransceiver("video", direction="sendrecv")
            pc_device.addTransceiver("audio", direction="sendrecv")

            pc_client = RTCPeerConnection(configuration=config)
            pc_client.addTransceiver("video", direction="recvonly")
            pc_client.addTransceiver("audio", direction="recvonly")

            # Connect
            ws_d = await ws_session.ws_connect(ws_url)
            ws_c = await ws_session.ws_connect(ws_url)
            _LOGGER.info("Both connected")

            # Device sends ready
            await ws_d.send_str(json.dumps({"type": "ready"}))
            r = await recv(ws_c, 3, "client-ready")

            # Client creates offer with ICE
            offer = await pc_client.createOffer()
            await pc_client.setLocalDescription(offer)

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
            _LOGGER.info(f"Client SDP offer: {len(sdp_offer)} chars")

            # Send offer
            await ws_c.send_str(json.dumps({"type": "offer", "sdp": sdp_offer}))

            # Device receives
            r = await recv(ws_d, 10, "dev-offer")
            if not r or not isinstance(r, dict) or not r.get("sdp"):
                _LOGGER.error("Device didn't get offer!")
                return

            _LOGGER.info("Device got offer, creating answer...")

            # Device processes offer and creates answer
            await pc_device.setRemoteDescription(
                RTCSessionDescription(sdp=r["sdp"], type="offer")
            )
            answer = await pc_device.createAnswer()
            await pc_device.setLocalDescription(answer)

            # Wait for device ICE
            if pc_device.iceGatheringState != "complete":
                done2 = asyncio.Event()
                @pc_device.on("icegatheringstatechange")
                def on_ice2():
                    if pc_device.iceGatheringState == "complete":
                        done2.set()
                try:
                    await asyncio.wait_for(done2.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass

            sdp_answer = pc_device.localDescription.sdp
            _LOGGER.info(f"Device SDP answer: {len(sdp_answer)} chars")

            # Send answer
            await asyncio.sleep(1)
            await ws_d.send_str(json.dumps({"type": "answer", "sdp": sdp_answer}))
            _LOGGER.info("Device sent answer")

            # Client receives
            r2 = await recv(ws_c, 10, "cli-answer")
            if r2 and isinstance(r2, dict) and r2.get("sdp"):
                _LOGGER.info(">>> CLIENT GOT ANSWER! Setting remote description... <<<")
                await pc_client.setRemoteDescription(
                    RTCSessionDescription(sdp=r2["sdp"], type="answer")
                )
                _LOGGER.info(f"Client connection state: {pc_client.connectionState}")
                _LOGGER.info(f"Client ICE state: {pc_client.iceConnectionState}")

                # Wait for connection
                _LOGGER.info("Waiting for peer connection (10s)...")
                for i in range(20):
                    await asyncio.sleep(0.5)
                    if pc_client.connectionState == "connected":
                        _LOGGER.info(">>> PEER CONNECTION ESTABLISHED! <<<")
                        break
                    elif pc_client.connectionState == "failed":
                        _LOGGER.info("Peer connection failed")
                        break
                _LOGGER.info(f"Final state: conn={pc_client.connectionState} ice={pc_client.iceConnectionState}")
            else:
                _LOGGER.info("Client did NOT receive answer")

            await pc_device.close()
            await pc_client.close()
            try:
                await ws_d.close()
                await ws_c.close()
            except Exception:
                pass

            await asyncio.sleep(2)

            # ─── TEST 5: Try with actual camera (now that we know the protocol) ───
            _LOGGER.info("")
            _LOGGER.info("─── TEST 5: Send offer to REAL camera (we connect 2nd) ───")
            id_token = await account.session.async_get_id_token()
            headers = {"Authorization": f"Bearer {id_token}"}
            session_data = await generate_session(ws_session, headers, device_id)
            sid = session_data["sessionId"]
            token = session_data["sessionToken"]
            ws_url = f"{session_data['signalingURL']}?accessToken={token}"
            _LOGGER.info(f"Session: {sid}")

            # Create client PC
            pc_real = RTCPeerConnection(configuration=config)
            pc_real.addTransceiver("video", direction="recvonly")
            pc_real.addTransceiver("audio", direction="recvonly")

            @pc_real.on("track")
            def on_track(track):
                _LOGGER.info(f">>> TRACK: kind={track.kind} id={track.id} <<<")

            @pc_real.on("connectionstatechange")
            async def on_conn():
                _LOGGER.info(f"Connection: {pc_real.connectionState}")

            offer = await pc_real.createOffer()
            await pc_real.setLocalDescription(offer)
            if pc_real.iceGatheringState != "complete":
                done3 = asyncio.Event()
                @pc_real.on("icegatheringstatechange")
                def on_ice3():
                    if pc_real.iceGatheringState == "complete":
                        done3.set()
                try:
                    await asyncio.wait_for(done3.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass

            sdp = pc_real.localDescription.sdp

            # Connect WS and send offer
            ws_real = await ws_session.ws_connect(ws_url)
            _LOGGER.info("Connected to signaling. Sending offer...")
            await ws_real.send_str(json.dumps({"type": "offer", "sdp": sdp}))

            # Listen for response
            _LOGGER.info("Listening for camera response (30s)...")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                r = await recv(ws_real, min(deadline - time.monotonic(), 10), "camera")
                if r is None:
                    continue
                if isinstance(r, dict):
                    if r.get("type") == "answer":
                        _LOGGER.info(">>> CAMERA SENT ANSWER! <<<")
                        await pc_real.setRemoteDescription(
                            RTCSessionDescription(sdp=r["sdp"], type="answer")
                        )
                        # Continue listening for candidates
                    elif r.get("type") == "candidate":
                        _LOGGER.info(f">>> ICE CANDIDATE from camera <<<")
                    elif r.get("_close"):
                        break

            await pc_real.close()
            try:
                await ws_real.close()
            except Exception:
                pass

        finally:
            await ws_session.close()

    finally:
        await account.disconnect()

    _LOGGER.info("\nProbe round 8 complete.")


if __name__ == "__main__":
    asyncio.run(main())
