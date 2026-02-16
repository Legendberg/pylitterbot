## WebRTC Signaling Protocol — Full Probe Results (10 Rounds)

I've spent a full day doing deep protocol probing of the camera WebRTC signaling system. Here's everything I've found — hopefully useful for anyone working on camera streaming integration.

### Signaling WebSocket Relay Behavior

The WebSocket at `wss://watford.ienso-dev.com/api/signaling?accessToken={sessionToken}` is a **two-party relay server**.

| # | Finding | Evidence |
|---|---------|----------|
| 1 | **Two-party relay**: Only 2 WebSocket clients per session | 3rd connection kills both existing ones (close code 1006) |
| 2 | **2nd→1st relay works**: Messages from 2nd connector relay to 1st | Consistent across all tests |
| 3 | **Server enriches messages**: Adds `sessionId` and `originatior` (sic — typo in server code) | Every relayed message has these fields appended |
| 4 | **Only valid JSON objects relay**: Raw strings/non-JSON are dropped | Raw text not relayed; JSON with `type`/`action` fields relayed |
| 5 | **~18s idle timeout**: Server closes connection with code 1006 | Consistent across passive listen tests |
| 6 | **No subprotocol validation**: Server accepts any WebSocket subprotocol | Tested: webrtc, json, signaling, ienso, watford — all accepted |
| 7 | **Path-agnostic**: `/api/signaling/connect`, `/api/signaling/{id}`, `/api/signaling/offer` all accept WS connections | Routing is entirely via the JWT's `sid` claim |

### Message Relay Format

When Client B (2nd connector) sends:
```json
{"type": "offer", "sdp": "v=0\r\n...", "extra": "field"}
```

Client A (1st connector) receives:
```json
{
  "type": "offer",
  "sdp": "v=0\r\n...",
  "extra": "field",
  "sessionId": "{sessionId}",
  "originatior": "{uuid}"
}
```

- `sessionId` = matches the session from `generate-session` (and the JWT `sid` claim)
- `originatior` = server-assigned UUID for the sending connection
- All original fields preserved; server only appends

### 1st→2nd Direction (Inconsistent)

In one probe round, device(1st)→client(2nd) relay worked for JSON with `type` field. In the next round, the same test failed. **Direction 1st→2nd is unreliable** and may depend on timing or server state.

### REST Endpoints Discovered on Watford

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/device-manager/client/signaling` | GET | 400 | "Invalid request params input" — real endpoint, correct params unknown |
| `/api/device-manager/client/sessions` | GET | 400 | "Invalid request params input" — real endpoint |
| `/api/device-manager/client/devices` | GET | 400 | "Invalid request params input" — real endpoint |
| `/api/device-manager/client/signaling` | POST | 404 | POST not supported on this route |
| `/api/device-manager/client/signaling` | WS | 401 | WebSocket upgrade rejected on device-manager path |
| `/api/device-manager/client/signaling?accessToken={sessionToken}` | GET | **500** | Server crash — session token IS recognized and processed |
| `/api/device-manager/client/sessions?accessToken={sessionToken}` | GET | **500** | Same crash on all 3 endpoints |
| `/api/device-manager/client/devices?accessToken={sessionToken}` | GET | **500** | `accessToken` param is parsed |
| `/api/device-manager/client/signaling?accessToken={idToken}` | GET | 400 | Cognito ID token NOT recognized as accessToken |
| `/api/device-manager/client/signaling` | OPTIONS | **403** | "RBAC: access denied" (Istio service mesh RBAC) |
| `/api/device-manager/client/generate-session` (no deviceId) | GET | 400 | Confirms path param required |

**Key finding**: `accessToken={sessionToken}` causes **500 Internal Server Error** regardless of additional params. Tested with `deviceId`, `sessionId`, `spaceId`, `householdId`, `role`, `action`, `type`, `autoStart` — ALL return 500. The server recognizes the session JWT but crashes trying to process it in a REST context.

Session token as `Authorization: Bearer` on REST endpoints returns `401 "Jwt issuer is not configured"` — only Cognito tokens work for REST auth.

### `autoStart` Has No Effect

Comparing `generate-session` with and without `?autoStart=true`:
- Response structure is identical
- `autoStart: true` is always returned regardless of query param
- No behavioral difference observed

### Parameters Exhaustively Tested (All Return 400)

Across 10 probe rounds, these query params were tested on `/signaling`, `/sessions`, `/devices`:

`channelId`, `channel`, `room`, `roomId`, `token`, `spaceId`, `type`, `action`, `mode`, `serial`, `device`, `camera`, `cameraId`, `id`, `deviceId`, `sessionId`, `page`, `size`, `count`, `offset`, `cursor`, `skip`, `take`, `per_page`, `pageSize`, `userId`, `accountId`, `unitSerial`, `active`, `online`, `autoStart`, `format`, `protocol`, `householdId`, `status`, `limit`, and various combinations.

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

### Infrastructure

| Component | Value |
|-----------|-------|
| Reverse proxy | **Istio envoy** (Kubernetes service mesh) |
| CDN | **CloudFront** (AWS) |
| CORS | `Access-Control-Allow-Credentials: true` |
| RBAC | Istio RBAC enforced on OPTIONS preflight |

### Camera API Domains Need AWS SigV4

Both camera API domains on API Gateway return 403 for all new path explorations (`/signaling`, `/session`, `/webrtc`, `/stream`, `/connect`, `/live`, `/call`). They require **AWS Signature Version 4** authentication, not Bearer tokens.

### Cognito Token Structure

Relevant claims in the Cognito ID token:

| Claim | Type | Notes |
|-------|------|-------|
| `sub` | UUID | Cognito user ID |
| `mid` | string | Numeric user ID (same as `account.user_id`) |
| `householdId` | string | Household grouping |
| `iss` | URL | Cognito user pool |
| `custom:source` | string | Migration status |
| Hasura claims | JSON | `x-hasura-user-id` embedded |

### The Blocker: Camera Never Connects

**The camera device never connects to the signaling WebSocket for our sessions.**

Despite:
- Using `autoStart=true` in `generate-session`
- Having the Whisker app actively streaming the camera
- Testing with the camera in both front and inside view modes
- Waiting up to 50 seconds for the camera to appear
- Connecting as 1st connector (device role) to receive camera as 2nd connector
- Connecting as 2nd connector (client role) to send offer to camera as 1st connector
- Calling REST endpoints while WS is connected (attempting to trigger camera)
- Reconnecting after idle timeout to maintain presence

### What's Working (Complete Picture)

1. **Auth flow**: Cognito login → Bearer token → `generate-session/{deviceId}` → session JWT + TURN creds
2. **Signaling**: `wss://watford.ienso-dev.com/api/signaling?accessToken={sessionToken}` is a 2-party JSON relay
3. **Message format**: `{"type": "offer/answer/candidate", "sdp": "..."}`, server adds `sessionId` + `originatior`
4. **Relay behavior**: 2nd→1st reliable, 1st→2nd inconsistent, 3rd connection kills session, ~18s idle timeout
5. **TURN**: `coturn.watford-prod.ienso-dev.com:3478`, username=sessionId, password=random UUID
6. **WebRTC peer connections**: aiortc creates valid offers, ICE gathering completes, TURN allocations succeed
7. **Infrastructure**: Kubernetes + Istio envoy + CloudFront + AWS API Gateway

### What's Missing

There is an **undiscovered trigger mechanism** between `generate-session` and the camera connecting to the signaling WebSocket. This is likely:
1. An additional REST call we haven't found (the 400-returning endpoints may hold the answer)
2. An AWS IoT Core MQTT message pushed to the camera firmware
3. Something in the camera API Gateway endpoints (which need SigV4 auth)

The fact that @natekspencer confirmed "the app sends a few commands to initiate the live stream" strongly suggests additional API calls exist that we haven't captured.

### Research Notes

- **iENSO is NOT Meari/PPStrong** — separate companies. iENSO is a Canadian embedded vision company.
- **Watford** is iENSO's proprietary camera platform. No public documentation exists.
- **The developer portal** at `docs.ienso-dev.com` requires authentication.
- **No open-source code** implements the Watford signaling protocol.
- **The Tuya WebRTC protocol** (MQTT-based) is well-documented but is NOT what iENSO/Watford uses.
