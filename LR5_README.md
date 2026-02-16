# Litter-Robot 5 / LR5 Pro — Implementation Notes

Reference documenting the LR5 REST API reverse engineering, implementation, and testing work done on pylitterbot.

## API Overview

The LR5 uses a **purely REST API** — completely separate from the LR3/LR4 GraphQL backend. All LR4-style GraphQL mutations and queries return 404 on the LR5 backend.

| Item | Value |
|------|-------|
| **Base URL** | `https://ub.prod.iothings.site` |
| **Auth** | Same AWS Cognito as LR3/LR4 |
| **Protocol** | REST (no GraphQL) |
| **WebSocket** | Not confirmed; LR5 appears to use REST polling |
| **Camera API** | `https://watford.ienso-dev.com` (iENSO Watford platform) |

## REST Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/robots/{serial}` | Get robot data (includes nested `state` dict) |
| `PATCH` | `/robots/{serial}` | Update settings (name, sleep schedules, night light, etc.) |
| `POST` | `/robots/{serial}/commands` | Send operational commands (`{"type": "COMMAND"}`) |
| `GET` | `/robots/{serial}/activities` | Get activity history (PET_VISIT, CYCLE_COMPLETED, etc.) |
| `GET` | `/robots/{serial}/state` | Get state only |
| `GET` | `/robots` | List all robots |

### No firmware endpoints

Firmware updates are OTA by device. No REST endpoints exist for checking or triggering firmware updates. Monitor via state fields:
- `espUpdateStatus` / `stmUpdateStatus` — update progress
- `isFirmwareUpdating` — boolean flag
- `firmwareVersions` — current versions (nested dict with `mcuVersion`, `wifiVersion`, `cameraVersion`, `edgeVersion`, `aiVersion`)

## Commands

Discovered via API 422 error response which returned the complete valid enum.

### Implemented (POST /robots/{serial}/commands)

| Command | Description | Verified |
|---------|-------------|----------|
| `CLEAN_CYCLE` | Start a clean cycle | Yes |
| `POWER_ON` | Power on | Yes |
| `POWER_OFF` | Power off | Yes |
| `REMOTE_RESET` | Clear errors, may trigger cycle | Yes |
| `FACTORY_RESET` | Factory reset | No (destructive) |
| `RESET_WASTE_LEVEL` | Reset drawer level indicator | Yes |
| `CHANGE_FILTER` | Reset filter replacement counter | Yes |
| `ONBOARD_PTAG_ON` | Enable pet tag onboarding | No |
| `ONBOARD_PTAG_OFF` | Disable pet tag onboarding | No |
| `PRIVACY_MODE_ON` | Enable privacy mode | Yes |
| `PRIVACY_MODE_OFF` | Disable privacy mode | Yes |

### In API enum but not dispatched

| Command | Reason |
|---------|--------|
| `NO_OP` | Valid per API enum but returns `INTERNAL_SERVER_ERROR` |
| `FEED_NOW` | Likely for Feeder-Robot or unreleased litter hopper accessory |
| `DISCARD_MEAL` | Likely for Feeder-Robot or unreleased litter hopper accessory |

### Settings (PATCH /robots/{serial})

| Key | Example Value | Purpose |
|-----|---------------|---------|
| `name` | `"My Litter-Robot"` | Robot name |
| `nightLightSettings` | `{"mode": "On", "brightness": 50, "color": "#FF00FF"}` | Night light config |
| `panelSettings` | `{"displayIntensity": "Low", "isKeypadLocked": true}` | Panel config |
| `soundSettings` | `{"volume": 75, "cameraAudioEnabled": true}` | Sound config (Pro: camera audio toggle) |
| `litterRobotSettings` | `{"cycleDelay": 15}` | Robot settings |
| `sleepSchedules` | `[{"dayOfWeek": 0, "isEnabled": true, ...}]` | Per-day sleep schedules |

## Implemented Methods

### Robot Control
| Method | Description |
|--------|-------------|
| `start_cleaning()` | Start a clean cycle |
| `reset()` | Remote reset (clears errors, may trigger cycle) |
| `reset_waste_drawer()` | Reset waste drawer level indicator |
| `change_filter()` | Reset filter replacement counter |
| `set_power_status(value)` | Power on/off |
| `set_privacy_mode(value)` | Enable/disable privacy mode |

### Night Light
| Method | Description |
|--------|-------------|
| `set_night_light(value)` | Turn night light on/off |
| `set_night_light_mode(mode)` | Set mode: Off, On, Auto |
| `set_night_light_brightness(brightness)` | Set brightness 0-100 |
| `set_night_light_color(color)` | Set color (hex string, e.g. `"#FF00FF"`) |

### Panel & Sound
| Method | Description |
|--------|-------------|
| `set_panel_brightness(brightness)` | Set panel brightness (Low/Medium/High) |
| `set_panel_lockout(value)` | Enable/disable panel lockout |
| `set_volume(volume)` | Set sound volume 0-100 |
| `set_camera_audio(value)` | Enable/disable camera audio (Pro only) |

### Scheduling
| Method | Description |
|--------|-------------|
| `set_wait_time(minutes)` | Set clean cycle wait time (3, 7, 15, 25, 30 min) |
| `set_sleep_mode(enabled, sleep_time, wake_time, day_of_week)` | Configure per-day sleep schedules |
| `set_name(name)` | Set robot name |

### Data Retrieval
| Method | Description |
|--------|-------------|
| `refresh()` | Refresh robot data from API |
| `get_activity_history(limit)` | Get activity history (returns `Activity` objects) |
| `get_activities(limit, offset, activity_type)` | Get raw activity data (richer than `get_activity_history`) |
| `get_firmware_details()` | Get firmware version info from state |
| `get_latest_firmware()` | Get formatted firmware version string |

## Data Structure

LR5 nests most live data inside a `state` dict, unlike LR4 which puts fields at the top level. This caused several bugs in the initial implementation where base class properties tried to read from `self._data` instead of `self._state`.

```
{
  "serial": "LR5-xx-xx-xx-xxxx-xxxxxx",
  "type": "LR5" | "LR5_PRO",
  "name": "...",
  "timezone": "America/New_York",
  "state": {
    "setupDateTime": "...",       # NOT at top level
    "lastSeen": "...",            # NOT at top level
    "isOnline": true,
    "state": "StRobotIdle",       # StPascalCase format
    "displayCode": "DcModeIdle",  # DcPascalCase format
    "statusIndicator": {"type": "READY", "title": "Ready"},
    "firmwareVersions": {
      "mcuVersion": {"title": "Robot Firmware", "value": "v5.7.5 2904_0106"},
      "wifiVersion": null,        # null on Pro models
      "cameraVersion": {"title": "Camera Firmware", "value": "1.2.2-1233"},
      "edgeVersion": {"title": "Edge Firmware", "value": "1.5.22"},
      "aiVersion": {"title": "AI Firmware", "value": "0.0.41"},
    },
    "odometerCleanCycles": 81,    # nested, not top-level
    "hopperStatus": "Disabled",   # Mixed-case (not UPPER_SNAKE)
    "globeLitterLevelIndicator": "Optimal",  # Mixed-case
    ...
  },
  "panelSettings": {...},
  "nightLightSettings": {...},
  "litterRobotSettings": {...},
  "sleepSchedules": [...],
  "soundSettings": {...},
  "cameraMetadata": {...},        # Pro only
  "nextFilterReplacementDate": "...",
}
```

## Key Differences from LR4

| Area | LR4 | LR5 |
|------|-----|-----|
| **API** | GraphQL (AppSync at `lr4.iothings.site`) | REST (`ub.prod.iothings.site`) |
| **State values** | `UPPER_SNAKE_CASE` (`ROBOT_IDLE`) | `StPascalCase` (`StRobotIdle`) |
| **Display codes** | `DC_MODE_IDLE` | `DcModeIdle` |
| **Enum values** | `UPPERCASE` (`DISABLED`) | Mixed-case (`Disabled`) |
| **Data nesting** | Flat (fields at top level) | Nested (`state` dict) |
| **WebSocket** | AppSync subscriptions | Not confirmed (REST polling) |
| **Firmware check** | GraphQL query | No endpoint (OTA only) |
| **Sleep schedules** | `weekdaySleepModeEnabled` dict keyed by day name | `sleepSchedules` list with `dayOfWeek` 0-6 |
| **Commands** | GraphQL mutations | POST with `{"type": "COMMAND"}` |
| **Night light mode** | On/Off only | Off/On/Auto with brightness and color |
| **Wait times** | 3, 7, 15 minutes | 3, 7, 15, 25, 30 minutes |

## LR5 Pro Differences

LR5 and LR5 Pro use the same API format. The only differences:
- `type` field: `"LR5"` vs `"LR5_PRO"`
- Pro has `cameraMetadata` (deviceId, serialNumber, spaceId)
- Pro has additional firmware versions: `cameraVersion`, `edgeVersion`, `aiVersion`
- Pro `wifiVersion` is null in `firmwareVersions` (ESP firmware version also null in state)
- Pro has `cameraAudioEnabled` in `soundSettings`

## Bugs Found and Fixed

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| `setup_date` returns None | Base class reads `self._data.get("setupDateTime")` but LR5 nests it in `state` | Override to read from `self._state` |
| `last_seen` returns None | Same nesting issue | Override to read from `self._state` |
| `_parse_sleep_info` always breaks on first schedule | `start <= now or end >= now` is almost always true | Changed to `start <= now <= end` |
| `get_latest_firmware` says "PIC" | LR5 calls it MCU, not PIC | Changed label to "MCU" |
| `get_latest_firmware` uses `Dict` | Import was removed | Changed to lowercase `dict` |
| `get_firmware_details` ESP is None on Pro | `wifiVersion` is null, no fallback | Added fallback to `espFirmwareVersion` state field |
| All GraphQL methods 404 | LR5 has no GraphQL | Replaced with REST equivalents or NotImplementedError |
| `NO_OP` returns 500 | Valid enum value but server error | Removed from dispatch, documented as comment |
| Case-insensitive enum values | API returns `"Disabled"` not `"DISABLED"` | Added case-insensitive fallback to `to_enum()` |
| `cycle_count` reads wrong location | LR5 nests in `state` | Override to read from `self._state` |
| `NightLightMode` enum case mismatch | API returns `"Off"/"On"/"Auto"` not `"off"/"on"/"auto"` | Changed enum values to capitalized form |

## Clean Cycle Timing (from live test)

```
+0s    StRobotClean / StCycleDump   (globe rotation)
+55s   StCycleDfi                   (drawer level measurement)
+62s   StCycleLevel                 (litter level measurement)
+121s  StCycleHome                  (return to home position)
```

Total: ~2 minutes.

## PATCH Propagation Delay

Settings changes via PATCH return 204 immediately but take **5-10 seconds** before GET reflects the change. The code does optimistic local updates to avoid stale reads.

## Testing

### Unit Tests
- **83 LR5 tests** in `tests/test_litterrobot5.py`
- Full suite: 152/153 pass (1 pre-existing `test_account.py` failure)
- Test data in `tests/common.py`: `LITTER_ROBOT_5_DATA` and `LITTER_ROBOT_5_PRO_DATA`

### Live Hardware Tests
- Comprehensive live test suite run against LR5 Pro hardware
- Verified all commands, settings, state parsing, and error handling
- Credentials loaded from `~/.config/whisker/credentials`

## PRs and Issues

| PR/Issue | Status | Description |
|----------|--------|-------------|
| PR #338 | Open | Complete LR5/LR5 Pro implementation (main PR) |
| PR #330 | Open (superseded) | Investigation docs, can be closed |
| Issue #318 | Open | LR5 Pro camera support — `camera_metadata` exposed, streaming not implemented |
| Issue #247 | Open | Litter hopper (LR4) — not directly related |

## Not Yet Implemented

- **Camera streaming** — requires iENSO Watford WebRTC integration (see `camera_probe_findings_public.md`)
- **WebSocket real-time updates** — LR5 may not support subscriptions
- **Litter hopper commands** — `FEED_NOW`/`DISCARD_MEAL` exist in API but hardware not available
- **`FACTORY_RESET`** — callable via `_dispatch_command()` but untested (destructive)
- **`ONBOARD_PTAG_ON/OFF`** — callable via `_dispatch_command()` but untested

## Credits

- **@xeospeed** — Documented the REST API base URL and command structure
- **@Doekse** — Confirmed findings with rooted Pixel, discovered camera API details
- **@natekspencer** — Upstream pylitterbot maintainer, camera signaling URL discovery
- **@Legendberg** — LR5/LR5 Pro implementation, camera API probing, HA integration
