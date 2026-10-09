# SteamD Teleop Audit — EOP10 Fork

**Date:** 2026-05-23
**Scope:** UDP-based teleoperation architecture, post-WebRTC removal.
**Status:** Architecture stable. WebRTC removed; UDP unicast + WireGuard recommended for remote.

---

## 0. Architecture Change (2026-05-23)

WebRTC has been removed entirely. SteamD now uses:
- **UDP H264 MPEG-TS** for video streaming (unicast to headset IP)
- **UDP binary/JSON** for control input (port 5100)
- **WireGuard VPN** recommended for 4G/CGNAT traversal
- **Direct Wi-Fi** works for LAN without VPN

Old WebRTC audit findings (CRITICAL-1 through CRITICAL-3, HIGH-1/2) are **obsolete** — the attack surface no longer exists.

---

## 1. Process Inventory

### Control authority (publishes `carControl` or actuates)
- `controlsd` — `selfdrive/controls/controlsd.py:52` (publishes `carControl`).
- `steamd` — `selfdrive/steamd/steamd.py` (publishes `carControl`; mutually exclusive with controlsd via `SteamDRemoteControl` param).
- `vehicled` (`card`) — `selfdrive/vehicled/car/card.py:48` (publishes `sendcan` after CAN-side safety checks).

### Safety / monitoring
- `selfdrived` — `selfdrive/selfdrived/selfdrived.py:69`. Owns engagement state machine.
- `driverd` — gated by `EOPDriverDEnabled` param.
- `recordd` — crash logging, unaffected.
- Safety manager — `selfdrive/vehicled/safety/safety_manager.py:26`. Layer-1 CAN safety: heartbeat (200 ms), steering rate, accel limits.

### Perception
- `modeld`, `gridd`, `pathd` — idle for teleop but left running.
- `radiard`, `plannerd` — radar absent in Tesla-only fork.

### I/O & infra
- `v4l2d`, `imud`, `micd`, `spkd`, `rtcd`, `socketd`, `pigeond`, `inferenced`, `bluetoothd`, `obd2d`
- `loggerd`, `mcapd`, `deleter`
- `ui` — driver-centric; approval modal + banner for remote control.

### Redundant with no human in vehicle
- `driverd` — distraction alerts have no audience.
- `soundd` — driver-targeted alerts have no audience.
- `ui` — takeover prompts confusing without occupant.

---

## 2. Engagement & Authority Model

### Path from UDP engage → `carControl.enabled`
1. Remote operator holds deadman (both grips) + presses A → UDP packet with `engage=true`.
2. `steamd` receives packet; if driver previously approved (`SteamDRemoteControl=True`), commands pass through.
3. `controlsd` is **not running** when `SteamDRemoteControl=True` (process-level mutex).
4. `steamd` publishes `carControl` at 100 Hz with `enabled=True`.
5. `vehicled` forwards to CAN after safety checks.

### NO_ENTRY gates (hard blocks)
- `seatbeltNotLatched` — bypassed in drone mode (future `DroneMode` param).
- `doorOpen` — still active; opens door → immediate disengage.
- `pedalPressed` (brake) — immediate disengage via arbiter.
- `driverUnresponsive` / `driverDistracted` — only if `driverd` enabled.

### CAN-layer safety (independent of controlsd)
- `MAX_STEERING_RATE = 200` (≈20 deg/s)
- `MAX_STEERING_ANGLE = 2700` (270°)
- `MAX_ACCEL = 340`, `MIN_ACCEL = 310`
- **Heartbeat timeout: 200 ms**
- Driver torque override: `DRIVER_TORQUE_THRESHOLD = 200` (2.0 Nm)
- RX message timeout: 1.2 s

---

## 3. What Exists Today

### UDP Control Input (`selfdrive/steamd/inputs.py:UdpInput`)
- Listens on UDP port 5100.
- Parses binary header `<BB3d4ddddddq` + JSON payload.
- Supports deadman, engage/disengage, view mode switching, assist overlay toggle.
- No sequence numbers yet (Phase 5 hardening).
- No HMAC yet (Phase 5 hardening).

### UDP Video Streamer (`selfdrive/steamd/video_streamer.py`)
- FFmpeg H264 MPEG-TS over UDP unicast.
- Target IP configurable (`udp_stream_target_addr`).
- Stereo side-by-side with auto baseline correction.
- Racing-game HUD overlay (speed arc, battery, throttle/brake bars, g-force ball, steering wheel).
- PiP wide camera bottom-left.

### Safety Features
- Process-level mutex via `SteamDRemoteControl` param.
- Lazy PubMaster — no publisher registration until authorized.
- Link-loss safe-stop — hard brake ramp until standstill.
- Local override — brake/gas/steer/door immediately kills session.
- Ignition gate — never publish if ignition off.
- Geofence — reject commands outside polygon.

### Gaps
- Per-message sequence numbers + HMAC (not yet implemented).
- Rate-limiting on control packets (not yet implemented).
- Geofence UI configuration (polygon must be set via param shell).
- DroneMode param for no-human operation.

---

## 4. Changes for "No Human Onboard" (Ground Drone)

### Becomes inapplicable
- `seatbeltNotLatched` — no belt.
- `driverUnresponsive` / `driverDistracted` — no driver.
- Driver-facing alerts (`soundd`, `ui`).

### Becomes more important
- **Link-loss → immediate safe-stop.** Current implementation publishes deceleration ramp.
- **Geofence.** Nothing exists beyond hardcoded polygon param.
- **Single `carControl` publisher.** Enforced by `SteamDRemoteControl` param.
- **RX freshness.** `safety_manager` enforces 1.2 s timeout.

### Recommended new param
`DroneMode` (or `UnmannedVehicle`) that, when set:
- Disables `driverd`, `soundd`, `ui` at process-config level.
- Skips seatbelt / face checks in `selfdrived`.
- Disables `controlsd` — `steamd` sole `carControl` publisher.
- Enables mandatory link-loss safe-stop.

---

## 5. Access Control — Current State

### What exists
- **Bearer token** on HTTP `/control` endpoint (`steamd.py`).
- **SteamDAuthToken** param — pre-shared secret for HTTP API.
- No auth on UDP control port (intended; auth handled by WireGuard tunnel).

### Threat model
1. **Network access on same LAN = vehicle control.** UDP port 5100 has no application-layer auth.
   - Mitigation: Run inside WireGuard VPN; LAN should be trusted.
2. **Replay attacks.** No sequence numbers on UDP control packets.
   - Mitigation: Phase 5 hardening (seq + HMAC).
3. **DoS on video stream.** UDP stream target is fixed; no rate limit on incoming connections.
   - Mitigation: Firewall rules on headset side.

### Required for production drone teleop
- Sequence number + HMAC on every control packet.
- Per-client command rate limit (1 command / 10 ms).
- Audit log of all commands (timestamp, client ID, fields, accepted/rejected).
- Mandatory geofence.
- mTLS for WireGuard peers (beyond pre-shared keys).

---

## Summary Table

| # | Area | Finding | Status |
|---|---|---|---|
| 1 | Process inventory | `controlsd` + `steamd` mutually exclusive via `SteamDRemoteControl` param. | ✅ Implemented |
| 2 | Engagement gates | NO_ENTRY blocks: seatbelt, door, pedal, face. CAN-layer safety enforces 200 ms heartbeat. | ✅ Implemented |
| 3 | UDP protocol | `UdpInput` parses binary+JSON. No seq/HMAC yet. No rate limit. | ⚠️ Phase 5 |
| 4 | Video streaming | UDP unicast H264 via FFmpeg. Stereo correction + HUD overlays. | ✅ Implemented |
| 5 | No-human changes | Seatbelt/door/face NO-OPs; link-loss safe-stop critical; geofence needed. | ⚠️ Partial |
| 6 | Auth / threat model | No app-layer auth on UDP control (WireGuard handles transport). HTTP has bearer token. | ⚠️ Phase 5 |

---

## Open Code State

- All WebRTC code removed (aiortc, channels, tracks, offer endpoint).
- File structure reorganized into focused modules.
- UDP protocol unified with HumRobot / OpenArm.
- HUD overlay gaming-style with battery, speed arc, g-force ball.
- Both `TELEOP_AUDIT.md` and `PIPELINE_AUDIT.md` updated to reflect current architecture.
