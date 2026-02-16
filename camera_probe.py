"""Probe LR5 Pro camera REST APIs and WebRTC session generation."""

import asyncio
import configparser
import json
import logging
from pathlib import Path

from pylitterbot import Account

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
_LOGGER = logging.getLogger(__name__)

# Camera API endpoints discovered from @Doekse's HAR captures
CAMERA_API_1 = "https://rrntg65uwf.execute-api.us-east-1.amazonaws.com"
CAMERA_API_2 = "https://7mnuil943l.execute-api.us-east-1.amazonaws.com"
WATFORD_API = "https://watford.ienso-dev.com"


def load_credentials():
    """Load credentials from ~/.config/whisker/credentials."""
    c = configparser.ConfigParser()
    c.read(Path.home() / ".config" / "whisker" / "credentials")
    return c.get("whisker", "email"), c.get("whisker", "password")


async def main() -> None:
    """Probe camera APIs."""
    username, password = load_credentials()
    account = Account()

    try:
        await account.connect(
            username=username, password=password, load_robots=True, load_pets=False
        )

        # Find LR5 Pro robot
        lr5_pro = None
        for robot in account.robots:
            print(f"Robot: {robot.name} | Serial: {robot.serial} | Model: {robot.model}")
            if hasattr(robot, "camera_metadata"):
                cam_meta = robot.camera_metadata
                print(f"  Camera metadata: {json.dumps(cam_meta, indent=2)}")
                if cam_meta:
                    lr5_pro = robot

        if not lr5_pro:
            print("\nNo LR5 Pro with camera metadata found.")
            return

        device_id = lr5_pro.camera_metadata["deviceId"]
        serial = lr5_pro.serial
        session = account.session

        print(f"\n{'='*60}")
        print(f"Probing camera APIs for device: {device_id}")
        print(f"Serial: {serial}")
        print(f"{'='*60}")

        # Get the bearer token for direct requests
        id_token = await session.async_get_id_token()
        headers = {"Authorization": f"Bearer {id_token}"}

        # ─── Probe 1: List cameras ───
        print(f"\n--- Probe 1: GET {CAMERA_API_1}/prod/v1/cameras ---")
        try:
            async with session.websession.get(
                f"{CAMERA_API_1}/prod/v1/cameras", headers=headers
            ) as resp:
                print(f"Status: {resp.status}")
                print(f"Headers: {dict(resp.headers)}")
                body = await resp.text()
                print(f"Body: {body[:2000]}")
        except Exception as e:
            print(f"Error: {e}")

        # ─── Probe 2: Get specific camera ───
        print(f"\n--- Probe 2: GET {CAMERA_API_1}/prod/v1/cameras/{device_id} ---")
        try:
            async with session.websession.get(
                f"{CAMERA_API_1}/prod/v1/cameras/{device_id}", headers=headers
            ) as resp:
                print(f"Status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:2000]}")
        except Exception as e:
            print(f"Error: {e}")

        # ─── Probe 3: List videos for today ───
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(
            f"\n--- Probe 3: GET {CAMERA_API_1}/prod/v1/cameras/{device_id}/videos/{today} ---"
        )
        try:
            async with session.websession.get(
                f"{CAMERA_API_1}/prod/v1/cameras/{device_id}/videos/{today}",
                headers=headers,
            ) as resp:
                print(f"Status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:2000]}")
        except Exception as e:
            print(f"Error: {e}")

        # ─── Probe 4: Get litters ───
        print(f"\n--- Probe 4: GET {CAMERA_API_1}/prod/v1/litters ---")
        try:
            async with session.websession.get(
                f"{CAMERA_API_1}/prod/v1/litters", headers=headers
            ) as resp:
                print(f"Status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:2000]}")
        except Exception as e:
            print(f"Error: {e}")

        # ─── Probe 5: Robot litter data ───
        print(f"\n--- Probe 5: GET {CAMERA_API_1}/prod/v1/robots/{serial}/litter ---")
        try:
            async with session.websession.get(
                f"{CAMERA_API_1}/prod/v1/robots/{serial}/litter", headers=headers
            ) as resp:
                print(f"Status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:2000]}")
        except Exception as e:
            print(f"Error: {e}")

        # ─── Probe 6: Camera video settings (reported) ───
        print(
            f"\n--- Probe 6: GET {CAMERA_API_2}/prod/v1/cameras/{device_id}/reported-settings/videoSettings ---"
        )
        try:
            async with session.websession.get(
                f"{CAMERA_API_2}/prod/v1/cameras/{device_id}/reported-settings/videoSettings",
                headers=headers,
            ) as resp:
                print(f"Status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:2000]}")
        except Exception as e:
            print(f"Error: {e}")

        # ─── Probe 7: Camera audio settings (reported) ───
        print(
            f"\n--- Probe 7: GET {CAMERA_API_2}/prod/v1/cameras/{device_id}/reported-settings/audioSettings ---"
        )
        try:
            async with session.websession.get(
                f"{CAMERA_API_2}/prod/v1/cameras/{device_id}/reported-settings/audioSettings",
                headers=headers,
            ) as resp:
                print(f"Status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:2000]}")
        except Exception as e:
            print(f"Error: {e}")

        # ─── Probe 8: Watford AI settings ───
        print(
            f"\n--- Probe 8: GET {CAMERA_API_2}/prod/v1/cameras/{device_id}/watfordAISettings ---"
        )
        try:
            async with session.websession.get(
                f"{CAMERA_API_2}/prod/v1/cameras/{device_id}/watfordAISettings",
                headers=headers,
            ) as resp:
                print(f"Status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:2000]}")
        except Exception as e:
            print(f"Error: {e}")

        # ─── Probe 9: Watford session generation ───
        print(
            f"\n--- Probe 9: GET {WATFORD_API}/api/device-manager/client/generate-session/{device_id}?autoStart=true ---"
        )
        try:
            async with session.websession.get(
                f"{WATFORD_API}/api/device-manager/client/generate-session/{device_id}?autoStart=true",
                headers=headers,
            ) as resp:
                print(f"Status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:2000]}")
                if resp.status == 200:
                    session_data = json.loads(body)
                    print(f"\nSession ID: {session_data.get('sessionId')}")
                    print(f"Signaling URL: {session_data.get('signalingURL')}")
                    print(f"Expiration: {session_data.get('sessionExpiration')}")
                    turn = session_data.get("turnServer", {})
                    print(f"STUN: {turn.get('stunUrl')}")
                    print(f"TURN: {turn.get('turnUrl')}")
        except Exception as e:
            print(f"Error: {e}")

        # ─── Probe 10: Watford WebSocket connect + listen ───
        print(f"\n--- Probe 10: WebSocket signaling probe ---")
        try:
            body_data = json.loads(body) if resp.status == 200 else None
            if body_data and body_data.get("sessionToken"):
                token = body_data["sessionToken"]
                ws_url = f"{body_data['signalingURL']}?accessToken={token}"
                print(f"Connecting to: {ws_url[:80]}...")

                import aiohttp

                ws_session = aiohttp.ClientSession()
                try:
                    async with ws_session.ws_connect(ws_url) as ws:
                        print("WebSocket connected! Listening for 15 seconds...")
                        try:
                            await asyncio.wait_for(
                                _ws_listener(ws), timeout=15
                            )
                        except asyncio.TimeoutError:
                            print("(15 second timeout reached)")
                        except Exception as e:
                            print(f"WebSocket error: {e}")
                finally:
                    await ws_session.close()
            else:
                print("No session token available, skipping WebSocket probe")
        except Exception as e:
            print(f"Error: {e}")

    finally:
        await account.disconnect()


async def _ws_listener(ws):
    """Listen for WebSocket messages and print them."""
    import aiohttp

    msg_count = 0
    async for msg in ws:
        msg_count += 1
        print(f"\n  Message #{msg_count}:")
        print(f"  Type: {msg.type}")
        if msg.type == aiohttp.WSMsgType.TEXT:
            print(f"  Data (text): {msg.data[:2000]}")
        elif msg.type == aiohttp.WSMsgType.BINARY:
            data = msg.data
            print(f"  Data (binary, {len(data)} bytes): {data[:200].hex()}")
            # Try CBOR decode
            try:
                import cbor2

                decoded = cbor2.loads(data)
                print(f"  CBOR decoded: {json.dumps(decoded, default=str, indent=2)}")
            except Exception:
                pass
            # Try JSON decode
            try:
                decoded = json.loads(data)
                print(f"  JSON decoded: {json.dumps(decoded, indent=2)}")
            except Exception:
                pass
        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            print(f"  WebSocket closed/error: {msg.data}")
            break


if __name__ == "__main__":
    asyncio.run(main())
