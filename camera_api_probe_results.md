# LR5 Pro Camera API Probe Results

**Date:** 2026-02-15
**Device ID:** `{deviceId}` (camera's internal ID, not the LR5 serial)
**Serial:** `LR5-XX-XX-XX-XXXX-XXXXXX`
**Camera Serial:** `{cameraSerial}`
**Space ID:** `{spaceId}`

## API Base URLs

| Name | URL |
|------|-----|
| Camera API 1 | `https://rrntg65uwf.execute-api.us-east-1.amazonaws.com` |
| Camera API 2 | `https://7mnuil943l.execute-api.us-east-1.amazonaws.com` |
| Watford API | `https://watford.ienso-dev.com` |

## Results Summary

| # | Endpoint | Status | Auth |
|---|----------|--------|------|
| 1 | `GET /prod/v1/cameras` | 200 | Bearer (Cognito ID token) |
| 2 | `GET /prod/v1/cameras/{deviceId}` | 200 | Bearer |
| 3 | `GET /prod/v1/cameras/{deviceId}/videos/{date}` | 200 | Bearer |
| 4 | `GET /prod/v1/litters` | 200 | Bearer |
| 5 | `GET /prod/v1/robots/{serial}/litter` | 200 | Bearer |
| 6 | `GET /prod/v1/cameras/{deviceId}/reported-settings/videoSettings` | 200 | Bearer |
| 7 | `GET /prod/v1/cameras/{deviceId}/reported-settings/audioSettings` | 200 | Bearer |
| 8 | `GET /prod/v1/cameras/{deviceId}/watfordAISettings` | **403** | Requires AWS SigV4 |
| 9 | `GET /api/device-manager/client/generate-session/{deviceId}?autoStart=true` | 200 | Bearer |
| 10 | `WSS /api/signaling?accessToken={sessionToken}` | Connected | Session JWT |

---

## Probe 1: List Cameras

**Endpoint:** `GET https://rrntg65uwf.execute-api.us-east-1.amazonaws.com/prod/v1/cameras`
**Status:** 200

```json
[
  {
    "serial": "{deviceId}",
    "spaceId": "{spaceId}",
    "name": "Litter-Robot 5 Pro",
    "status": "ONLINE",
    "settings": {
      "motionRecordingType": "ALL_MOTION",
      "listenToGlassBreak": true,
      "listenToSmokeAlarm": true
    },
    "createdAt": 1764106963.260085,
    "updatedAt": 1770417075.045529,
    "deviceType": "WC1",
    "unitSerial": "LR5-XX-XX-XX-XXXX-XXXXXX"
  }
]
```

**Notes:**
- `serial` here is the camera's `deviceId`, not the LR5 serial
- `deviceType: "WC1"` = Whisker Camera 1 (LR5 Pro built-in)
- `unitSerial` maps back to the LR5 robot serial
- Settings include motion recording type and safety sound detection

---

## Probe 2: Get Specific Camera

**Endpoint:** `GET https://rrntg65uwf.execute-api.us-east-1.amazonaws.com/prod/v1/cameras/{deviceId}`
**Status:** 200

Same response as Probe 1 but unwrapped (single object, not array).

---

## Probe 3: List Videos for Date

**Endpoint:** `GET https://rrntg65uwf.execute-api.us-east-1.amazonaws.com/prod/v1/cameras/{deviceId}/videos/{YYYY-MM-DD}`
**Status:** 200

```json
[
  {
    "id": 32388070,
    "videoThumbnail": "https://whisker-camera-video-clips-prod-us-east-1.s3.amazonaws.com/device-files/{deviceId}/{uuid}?AWSAccessKeyId=...&Signature=...&Expires=...",
    "allowForward": true,
    "createdAt": 1771179783.78511,
    "forwardingCountdownEndsAt": 1771266339.952508,
    "eventType": "cat_detected",
    "hlsDuration": "00:21",
    "petDetections": []
  }
]
```

**Notes:**
- Thumbnails are S3 presigned URLs (time-limited)
- `eventType: "cat_detected"` - AI-triggered clips
- `hlsDuration` suggests HLS playback available
- `petDetections` array for pet identification (empty in test clips)
- `allowForward` / `forwardingCountdownEndsAt` for clip sharing

---

## Probe 4: List Litter Brands

**Endpoint:** `GET https://rrntg65uwf.execute-api.us-east-1.amazonaws.com/prod/v1/litters`
**Status:** 200

Large response - litter brand database with formulations, recommended cycle delays, and "Perfect Cycle" compatibility info. Not camera-related.

---

## Probe 5: Robot Litter Data

**Endpoint:** `GET https://rrntg65uwf.execute-api.us-east-1.amazonaws.com/prod/v1/robots/{serial}/litter`
**Status:** 200

```json
{
  "brandId": 1,
  "formulationId": 13,
  "typeId": 1,
  "recommendedCycleDelay": null,
  "isPerfectCycle": false,
  "formulationImage": null,
  "webViewUrl": null,
  "webViewCopy": null,
  "perfectCycleModalCopy": null,
  "acknowledged": false,
  "accepted": false,
  "acknowledgedAt": "None"
}
```

Not camera-related. User's selected litter formulation.

---

## Probe 6: Video Settings (CRITICAL FOR STREAMING)

**Endpoint:** `GET https://7mnuil943l.execute-api.us-east-1.amazonaws.com/prod/v1/cameras/{deviceId}/reported-settings/videoSettings`
**Status:** 200

```json
{
  "reportedSettings": [
    {
      "settingsType": "videoSettings",
      "data": {
        "streams": {
          "live-view": {
            "canvas": "sensor_0_1080p",
            "bitrate_control": {
              "bitrate": 1500000,
              "mode": "cbr",
              "stable_br_adjust": 0
            },
            "flip": [0, 0],
            "fps": 15,
            "gop": { "M": 1, "N": 20 },
            "resolution": "1920x1080",
            "state": "encoding",
            "type": "h264"
          },
          "recording": {
            "bitrate_control": {
              "bitrate": 2500000,
              "mode": "cbr",
              "stable_br_adjust": 0
            },
            "flip": [0, 0],
            "fps": 20,
            "gop": { "M": 1, "N": 40 },
            "resolution": "1920x1080",
            "state": "encoding",
            "type": "h264"
          },
          "setup": {
            "flip": [0, 0],
            "fps": 4,
            "quality_level": 80,
            "resolution": "640x480",
            "state": "encoding",
            "type": "mjpeg"
          },
          "snapshots": {
            "flip": [0, 0],
            "fps": 2,
            "quality_level": 80,
            "resolution": "1920x1080",
            "state": "encoding",
            "type": "mjpeg"
          },
          "snapshots-secondary": {
            "canvas": "sensor_1_720p",
            "flip": [0, 0],
            "fps": 2,
            "quality_level": 80,
            "resolution": "1280x720",
            "state": "encoding",
            "type": "mjpeg"
          }
        },
        "sensor": {
          "fps": 30,
          "hdr": "linear",
          "hflip": false,
          "mode": "fullhd",
          "name": "imx307",
          "vflip": false
        }
      },
      "timestamp": "2026-02-15T18:28:36.553Z"
    }
  ]
}
```

**Key findings:**
- **5 video streams** on the camera hardware
- **`live-view`**: H.264, 1080p, 15fps, 1.5Mbps CBR - this is what WebRTC will deliver
- **`recording`**: H.264, 1080p, 20fps, 2.5Mbps CBR - for cloud clip storage
- **`setup`**: MJPEG, 640x480, 4fps - low-res setup/preview stream
- **`snapshots`**: MJPEG, 1080p, 2fps - periodic full-res snapshots
- **`snapshots-secondary`**: MJPEG, 720p, 2fps - secondary snapshot stream
- **Sensor**: Sony IMX307 (popular IP camera sensor), fullHD, 30fps capable
- GOP structure: I-frame every 20 frames (live-view) / 40 frames (recording)

---

## Probe 7: Audio Settings

**Endpoint:** `GET https://7mnuil943l.execute-api.us-east-1.amazonaws.com/prod/v1/cameras/{deviceId}/reported-settings/audioSettings`
**Status:** 200

```json
{
  "reportedSettings": [
    {
      "settingsType": "audioSettings",
      "data": {
        "audio_in": {
          "global": {
            "mute": false,
            "volume": 79,
            "channels": 1,
            "latency_ms": 100,
            "sample_format": "int16",
            "sample_rate": 48000
          }
        },
        "audio_out": {
          "volume": 69,
          "gain": 0,
          "mute": false
        }
      },
      "timestamp": "2022-04-28T17:44:57.099Z"
    }
  ]
}
```

**Key findings:**
- Audio in: 48kHz, 16-bit, mono - microphone on camera
- Audio out: speaker on camera (for two-way audio)
- Both not muted by default
- Timestamp from 2022 suggests these are factory defaults

---

## Probe 8: Watford AI Settings

**Endpoint:** `GET https://7mnuil943l.execute-api.us-east-1.amazonaws.com/prod/v1/cameras/{deviceId}/watfordAISettings`
**Status:** 403

```json
{
  "message": "Invalid key=value pair (missing equal-sign) in Authorization header..."
}
```

**Notes:**
- This endpoint requires AWS Signature V4 authentication, not a Bearer token
- The error shows it's hashing the Bearer token and finding it doesn't match SigV4 format
- Lower priority - AI settings (pet detection config) not needed for streaming

---

## Probe 9: WebRTC Session Generation (CRITICAL FOR STREAMING)

**Endpoint:** `GET https://watford.ienso-dev.com/api/device-manager/client/generate-session/{deviceId}?autoStart=true`
**Status:** 200

```json
{
  "sessionId": "{uuid}",
  "sessionExpiration": "2026-02-15T19:18:52.000Z",
  "sessionToken": "eyJhbGciOiJSUzI1NiIs...(JWT)...",
  "signalingURL": "wss://watford.ienso-dev.com/api/signaling",
  "turnServer": {
    "username": "{sessionId}",
    "password": "{uuid}",
    "stunUrl": "stun:coturn.watford-prod.ienso-dev.com:3478",
    "turnUrl": ["turn:coturn.watford-prod.ienso-dev.com:3478"]
  },
  "autoStart": true
}
```

**Key findings:**
- Session JWT has 60-second lifetime (`iat` to `exp` delta)
- TURN credentials: username = sessionId, password = random UUID
- TURN server: `coturn.watford-prod.ienso-dev.com:3478`
- `autoStart: true` accepted but has no observable effect (see below)
- Signaling goes through Watford (iENSO's platform) WebSocket

**JWT payload (decoded):**
```json
{
  "sid": "{sessionId}",
  "iat": 1771183072,
  "exp": 1771183132,
  "aud": [],
  "iss": "device-signaling-authority"
}
```

---

## Probe 10: WebSocket Signaling

**Endpoint:** `WSS wss://watford.ienso-dev.com/api/signaling?accessToken={sessionToken}`
**Status:** Connected successfully

**Result:** No messages received in 15 seconds of passive listening.

**Notes:**
- The WebSocket is a **signaling channel**, not a media channel
- It requires the client to send a WebRTC SDP offer to initiate negotiation
- The camera will respond with an SDP answer and ICE candidates
- No unsolicited messages - this is expected behavior for a signaling-only channel

---

## Architecture Summary

```
                    Whisker App / pylitterbot
                            |
                   [Cognito ID Token]
                            |
                 +----------+----------+
                 |                     |
        Camera API 1              Camera API 2
   (rrntg65uwf...amazonaws)  (7mnuil943l...amazonaws)
         |                          |
   cameras, videos,          reported-settings
   litters, robots           (video, audio, AI)

                 Watford API (watford.ienso-dev.com)
                            |
              +-------------+-------------+
              |                           |
    generate-session              WSS /api/signaling
    (REST, Bearer token)        (WebSocket, session JWT)
              |                           |
    Returns: sessionId,          WebRTC SDP exchange
    sessionToken (JWT),          ICE candidate exchange
    TURN/STUN credentials               |
                                  Media Stream
                               (H.264 via WebRTC)
```

## Streaming Flow (Determined)

1. **Authenticate** with Cognito (existing pylitterbot flow)
2. **Get camera metadata** from robot object (`deviceId`)
3. **Generate session** via `GET /api/device-manager/client/generate-session/{deviceId}?autoStart=true`
4. **Connect WebSocket** to `wss://watford.ienso-dev.com/api/signaling?accessToken={sessionToken}`
5. **Create WebRTC peer connection** with TURN/STUN config
6. **Send SDP offer** through WebSocket
7. **Receive SDP answer** through WebSocket
8. **Exchange ICE candidates** through WebSocket
9. **Receive H.264 video** via WebRTC data channel / media track

---

## WebRTC Signaling Protocol Probe Results (2026-02-15)

### Signaling WebSocket Relay Behavior

The WebSocket at `wss://watford.ienso-dev.com/api/signaling?accessToken={sessionToken}` is a **two-party relay server**. Tested across 8 rounds of probing.

#### Confirmed Behaviors

| # | Finding | Evidence |
|---|---------|----------|
| 1 | **Two-party relay**: Only 2 WebSocket clients per session | 3rd connection kills both existing ones (close code 1006) |
| 2 | **2nd->1st relay works**: Messages from 2nd connector relay to 1st | Consistent across all tests |
| 3 | **Server enriches messages**: Adds `sessionId` and `originatior` (sic) | Every relayed message has these fields appended |
| 4 | **Only valid JSON objects relay**: Raw strings/non-JSON dropped | Raw "hello-from-device" not relayed; JSON with `type`/`action` fields relayed |
| 5 | **~18s idle timeout**: Server closes connection with code 1006 | Consistent across passive listen tests |
| 6 | **No subprotocol validation**: Server accepts any WebSocket subprotocol | Tested: webrtc, json, signaling, ienso, watford - all accepted |
| 7 | **Behind CloudFront**: `Via: 1.1 *.cloudfront.net (CloudFront)` | Response headers on every connection |

#### Message Relay Format

When Client B (2nd connector) sends to Client A (1st connector):

**Sent by B:**
```json
{"type": "offer", "sdp": "v=0\r\n...", "extra": "field"}
```

**Received by A:**
```json
{
  "type": "offer",
  "sdp": "v=0\r\n...",
  "extra": "field",
  "sessionId": "{sessionId}",
  "originatior": "{uuid}"
}
```

- `sessionId` = the session from `generate-session` (matches JWT `sid` claim)
- `originatior` = server-assigned UUID for the sending connection (note: typo in server code - "originatior" not "originator")
- All original fields preserved; server only appends

#### 1st->2nd Direction (Inconsistent)

In probe round 7, device(1st)->client(2nd) relay worked for JSON with `type` field. In probe round 8, the same test failed. **Direction 1st->2nd is unreliable** and may depend on timing or server state. The reliable direction is always **2nd->1st**.

#### Other REST Endpoints Discovered

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/device-manager/client/signaling` | GET | 400 | "Invalid request params input" - real endpoint, correct params unknown |
| `/api/device-manager/client/sessions` | GET | 400 | "Invalid request params input" - real endpoint |
| `/api/device-manager/client/devices` | GET | 400 | "Invalid request params input" - real endpoint |
| `/api/device-manager/client/signaling` | POST | 404 | POST not supported |
| `/api/device-manager/client/signaling` | WS | 401 | WebSocket upgrade rejected |
| `/api/device-manager/client/signaling?accessToken={sessionToken}` | GET | **500** | Server crash! Session token IS recognized |
| `/api/device-manager/client/sessions?accessToken={sessionToken}` | GET | **500** | Same crash on all 3 endpoints |
| `/api/device-manager/client/devices?accessToken={sessionToken}` | GET | **500** | accessToken param is parsed and processed |
| `/api/device-manager/client/signaling?accessToken={idToken}` | GET | 400 | ID token NOT recognized as accessToken |
| `/api/device-manager/client/signaling` | OPTIONS | **403** | "RBAC: access denied" (Istio RBAC) |
| `/api/device-manager/client/generate-session` (no deviceId) | GET | 400 | Confirms path param required |

**Key finding**: `accessToken={sessionToken}` in query string causes **500 Internal Server Error** regardless of other params. All combinations with `deviceId`, `sessionId`, `spaceId`, `householdId`, `role`, `action`, `type`, `autoStart` -- ALL return 500. The server recognizes and processes the session JWT but crashes.

Session token (JWT from generate-session) returns `401 "Jwt issuer is not configured"` when used as Bearer auth on REST API endpoints - only Cognito tokens work for REST.

#### WebSocket Sub-Path Behavior (Probe 10)

The `/api/signaling/` endpoint accepts WebSocket connections on ANY sub-path:
- `/api/signaling/connect` -> Connected
- `/api/signaling/{sessionId}` -> Connected
- `/api/signaling/{deviceId}` -> Connected
- `/api/signaling/offer` -> Connected

All use the same `accessToken` query param. The WS server is path-agnostic -- routing is entirely via the JWT's `sid` claim. Extra WS query params (`deviceId`, `sessionId`, `role`) are also silently accepted.

#### Infrastructure Details (from response headers)

| Component | Value |
|-----------|-------|
| Reverse proxy | **Istio envoy** (Kubernetes service mesh) |
| CDN | **CloudFront** (AWS) |
| CORS | `Access-Control-Allow-Credentials: true`, `Access-Control-Expose-Headers: WWW-Authenticate,Server-Authorization` |
| RBAC | Istio RBAC enforced on OPTIONS preflight |

#### Cognito Token Structure

Relevant claims in the Cognito ID token:

| Claim | Type | Notes |
|-------|------|-------|
| `sub` | UUID | Cognito user ID |
| `mid` | string | Numeric user ID (same as `account.user_id`) |
| `householdId` | string | Household grouping |
| `iss` | URL | Cognito user pool (`cognito-idp.us-east-1.amazonaws.com/...`) |
| `custom:source` | string | Migration status |
| Hasura claims | JSON | `x-hasura-user-id` embedded |

#### `autoStart` Has No Effect

Comparing `generate-session` with and without `?autoStart=true`:
- Response structure is identical
- `autoStart: true` is always returned regardless of query param
- TURN credentials change (as expected -- new session)
- No behavioral difference observed

#### Camera API Domains Need AWS SigV4

Both camera API domains (`rrntg65uwf.execute-api.us-east-1.amazonaws.com` and `7mnuil943l.execute-api.us-east-1.amazonaws.com`) return 403 for ALL new path explorations (`/signaling`, `/session`, `/webrtc`, `/stream`, `/connect`, `/live`, `/call`, `/generate-session`). They require **AWS Signature Version 4** authentication, not Bearer tokens. The error confirms: `"Invalid key=value pair (missing equal-sign) in Authorization header"`.

#### Parameters Exhaustively Tested (All Return 400)

Across probes 4-10, these query params were tested on `/signaling`, `/sessions`, `/devices`:

`channelId`, `channel`, `room`, `roomId`, `token`, `spaceId`, `type`, `action`, `mode`, `serial`, `device`, `camera`, `cameraId`, `id`, `deviceId`, `sessionId`, `page`, `size`, `count`, `offset`, `cursor`, `skip`, `take`, `per_page`, `pageSize`, `userId`, `accountId`, `unitSerial`, `active`, `online`, `autoStart`, `format`, `protocol`, `householdId`, `status`, `limit`, and various combinations thereof.

### SDP Offer Formats Tested

All 7 formats tried against both the relay (with 2 local clients) and the real camera:

| # | Format | Relay | Camera Response |
|---|--------|-------|-----------------|
| A | KVS-style (base64 payload) | Relays OK | No response |
| B | Simple JSON `{"type":"offer","sdp":"..."}` | Relays OK | No response |
| C | JSON + sessionId | Relays OK | No response |
| D | Ienso action wrapper | Relays OK | No response |
| E | CBOR KVS-style | Relays OK | No response |
| F | CBOR simple | Relays OK | No response |
| G | Raw SDP text | NOT relayed | No response |

### The Remaining Blocker: Camera Never Connects

**The camera device never connects to the signaling WebSocket for our sessions.**

Despite:
- Using `autoStart=true` in `generate-session`
- Having the Whisker app actively streaming the camera
- Testing with the camera in both front and inside view modes
- Waiting up to 50 seconds for the camera to appear
- Connecting as 1st connector (device role) to receive camera as 2nd connector
- Connecting as 2nd connector (client role) to send offer to camera as 1st connector

**Root cause hypothesis**: The `generate-session` API creates a session on the signaling server and returns credentials for the client, but the **camera firmware requires a separate trigger** to connect to the signaling channel for that specific session. This trigger likely comes through:

1. **AWS IoT Core** (the LR5 uses `ub.prod.iothings.site` for device communication)
2. **A device-side API call** that the Watford backend makes to the camera when `generate-session` is called
3. **A push notification** to the camera firmware via its own MQTT/IoT channel

The fact that @natekspencer confirmed "The app then sends a few commands to initiate the live stream" suggests there may be additional API calls between `generate-session` and the WebSocket connection that we haven't captured.

### Research Findings

- **iENSO is NOT Meari/PPStrong** - they are separate companies. iENSO is a Canadian embedded vision company.
- **Watford** is iENSO's proprietary camera platform. No public documentation exists.
- **The developer portal** at `docs.ienso-dev.com` requires authentication.
- **No open-source code** implements the Watford signaling protocol.
- **The Tuya WebRTC protocol** (MQTT-based, protocol 302) is well-documented but is NOT what iENSO/Watford uses.

### Next Steps

- [x] ~~Determine WebSocket message format~~ -> JSON with `type` field, server adds `sessionId` + `originatior`
- [x] ~~Implement WebRTC peer connection with `aiortc`~~ -> Works, ICE gathering completes, TURN allocations created
- [x] ~~Probe REST endpoints for camera trigger~~ -> Exhaustive testing; `accessToken` causes 500, all other params return 400
- [ ] **BLOCKER**: Determine how to trigger camera to connect to signaling WebSocket
  - Capture WebSocket frames from Flutter app (need rooted device with Frida or mitmproxy)
  - Ask @Doekse (has rooted Pixel 6a) to capture the traffic
  - The 400-returning endpoints likely hold the answer but params are unknown
  - Camera API domains need AWS SigV4 auth (not Bearer)
- [ ] Send SDP offer and handle SDP answer (blocked by above)
- [ ] Extract video frames from WebRTC media track
- [ ] Integrate into pylitterbot as camera streaming feature

### What We Know (Complete Picture)

1. **Auth flow**: Cognito login -> Bearer token -> `generate-session/{deviceId}` -> session JWT + TURN creds
2. **Signaling**: `wss://watford.ienso-dev.com/api/signaling?accessToken={sessionToken}` is a 2-party JSON relay
3. **Message format**: `{"type": "offer/answer/candidate", "sdp": "..."}`, server adds `sessionId` + `originatior`
4. **Relay behavior**: 2nd->1st reliable, 1st->2nd inconsistent, 3rd connection kills session, ~18s idle timeout
5. **TURN**: `coturn.watford-prod.ienso-dev.com:3478`, username=sessionId, password=random UUID
6. **Infrastructure**: Kubernetes + Istio envoy + CloudFront + AWS API Gateway
7. **Missing trigger**: Camera firmware never connects to our signaling sessions despite autoStart=true

### Probe Scripts

| Script | Purpose |
|--------|---------|
| `camera_probe.py` | REST API discovery (9 endpoints) |
| `camera_webrtc_probe.py` | Round 1: 7 SDP format attempts |
| `camera_webrtc_probe2.py` | Round 2: Passive listen, registration msgs, ICE gathering |
| `camera_webrtc_probe3.py` | Round 3: URL variations, response headers, alt domains |
| `camera_webrtc_probe4.py` | Round 4: REST signaling, endpoint enumeration |
| `camera_webrtc_probe5.py` | Round 5: **Relay discovery**, 400-endpoint probing |
| `camera_webrtc_probe6.py` | Round 6: Relay characterization, 3rd client test |
| `camera_webrtc_probe7.py` | Round 7: Type-based routing test |
| `camera_webrtc_probe8.py` | Round 8: Full handshake simulation, real camera attempt |
| `camera_webrtc_probe9.py` | Round 9: **500 error discovery**, param exhaustion, CORS/RBAC |
| `camera_webrtc_probe10.py` | Round 10: Exploiting 500, WS sub-paths, REST+WS combo, IoT |
