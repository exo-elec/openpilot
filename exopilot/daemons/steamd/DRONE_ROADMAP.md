# SteamD Remote-Control Roadmap — UDP Teleoperation

**Date:** 2026-05-23
**Scope:** Remote teleoperation via UDP (LAN or WireGuard VPN) with process-level control handoff.
**Status:** Active development. UDP streaming + control operational. WireGuard transport recommended for 4G.

---

## 0. Guiding Principle

> **Zero code changes inside `controlsd`, `selfdrived`, or the main control loop.**
> Authority handoff is handled the same way openpilot handles `joystickd`: a volatile param tells the process manager to exclude `controlsd` from the process list. When the driver overrides locally, the param is cleared and `controlsd` comes back automatically.

SteamD is **always** a monitoring system (video, telemetry, audit logging). Remote control is a temporary, driver-authorized session that borrows the same plumbing as joystick debug mode.

---

## 1. How the Original Joystick Pattern Works

Upstream openpilot (`tools/joystick/`):

1. `joystick_control.py` publishes `testJoystick` messages (from a laptop or gamepad).
2. `joystickd.py` subscribes to `testJoystick` and publishes **`carControl`** + **`controlsState`** at 100 Hz.
3. **`JoystickDebugMode`** param is `CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION`.
4. In `system/manager/process_config.py`:
   - `controlsd` has condition `and_(ignition_on, not_joystick, iscar)`
     → when `JoystickDebugMode=True`, `controlsd` **does not start**.
   - `joystickd` has condition `joystick`
     → when `JoystickDebugMode=True`, `joystickd` **starts**.
5. **No line of `controlsd.py` is modified.** The mutex is process-level, managed by the param + process config.
6. When the car goes offroad or manager restarts, the param auto-clears and `controlsd` returns.

We replicate this pattern exactly for SteamD remote control.

---

## 2. New Param: `SteamDRemoteControl`

```c
// common/params_keys.h
{"SteamDRemoteControl", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
```

- **Type:** Volatile bool. Survives neither manager restart nor offroad transition.
- **Written by:** `ui` (driver approval) or `steamd` (local override / link-loss).
- **Read by:** `system/manager/process_config.py` (process conditions).

This is the **single point of truth** for whether the vehicle is in remote-control mode.

---

## 3. Process-Config Changes (3 lines)

`system/manager/process_config.py`:

```python
# Existing helpers
def not_joystick(started, params, CP):
  return not _cached_param_bool(params, "JoystickDebugMode")

def joystick(started, params, CP):
  return _cached_param_bool(params, "JoystickDebugMode")

# NEW — mirror the joystick pattern exactly
def not_remote_control(started, params, CP):
  return not _cached_param_bool(params, "SteamDRemoteControl")

def remote_control(started, params, CP):
  return _cached_param_bool(params, "SteamDRemoteControl")
```

Then change the `controlsd` entry:

```python
# BEFORE
PythonProcess("controlsd", "selfdrive.controls.controlsd", and_(ignition_on, not_joystick, iscar)),

# AFTER
PythonProcess("controlsd", "selfdrive.controls.controlsd", and_(ignition_on, not_joystick, not_remote_control, iscar)),
```

`steamd` does **not** need a new process entry — it already runs under `SteamDEnabled`. When `SteamDRemoteControl` goes True, `steamd` simply starts its internal control publisher. The process itself stays alive for monitoring.

> **Impact on `controlsd.py`: ZERO lines changed.**
> **Impact on `selfdrived.py`: ZERO lines changed.**

---

## 4. Authority State Machine (inside `steamd`)

`steamd` always runs. It has two internal modes:

| Internal Mode | `SteamDRemoteControl` | `carControl` Publisher | Description |
|---|---|---|---|
| `MONITORING` | False | **None** (or zeroed keepalive) | Video, telemetry, audit log only. Remote client is view-only. |
| `REMOTE_ACTIVE` | True | **steamd** | Driver approved. `controlsd` is not running. SteamD publishes actuators. |
| `LOCAL_OVERRIDE` | False→cleared | None | Driver touched gas/brake/steering. SteamD clears param, sends final zeroed frame, returns to `MONITORING`. Manager restarts `controlsd`. |

---

## 5. Driver Consent Flow

### 5.1 Request
1. Remote client sends UDP control packet with `request_control: true`.
2. `steamd` receives it, writes param **`SteamDControlRequest = <client_id>`**.
3. `ui` polls the param every second. When non-empty, it shows a full-screen modal:
   - **Title:** "🔴 Remote Control Request"
   - **Body:** "`<client_id>` wants to drive the vehicle remotely."
   - **Buttons:** `[Allow]` `[Deny]`
4. Driver taps **Allow** → `ui` writes `SteamDRemoteControl = True`.
5. Manager evaluates conditions on next `ensure_running()` tick (~1 s):
   - `controlsd` condition → False → **stops `controlsd`**.
   - `steamd` is already running.
6. `ui` shows a persistent red banner:
   `"REMOTE CONTROL ACTIVE — Press brake to regain control immediately"`.

### 5.2 Auto-expiry & cleanup
- `SteamDRemoteControl` is `CLEAR_ON_OFFROAD_TRANSITION` — parking the car kills remote control automatically.
- `SteamDRemoteControl` is `CLEAR_ON_MANAGER_START` — reboot kills it automatically.
- `ui` can also write `False` if the driver presses a **Cancel** button on the banner.

---

## 6. Local Override — How the Driver Takes Back Control

`steamd` subscribes to `carState` inside its existing `SubMaster` and evaluates override triggers **every 100 Hz tick**.

### 6.1 Triggers (hard-coded)

| Trigger | `carState` Field | Action |
|---|---|---|
| **Brake pedal** | `brakePressed` | Immediate disengage. Send one final `carControl` with `enabled=False`, zero actuators. Clear `SteamDRemoteControl`. |
| **Gas pedal** | `gasPressed` | Same as brake (full disengage). |
| **Steering torque** | `steeringPressed` (torque > `DRIVER_TORQUE_THRESHOLD` = 2.0 Nm) | Same as brake. |
| **Cruise cancel** | Cancel event in `carState` buttons | Same as brake. |
| **Door open** | `doorOpen` | Same as brake. Block re-request until door closed. |

### 6.2 Override sequence
```python
# Inside steamd.py control loop (pseudo-code)
if self.sm['carState'].brakePressed or self.sm['carState'].gasPressed or \
   self.sm['carState'].steeringPressed:

    # 1. Final zeroed frame so vehicled sees a clean handoff
    self._send_zero_command()

    # 2. Clear the param → manager will restart controlsd
    self.params.put_bool("SteamDRemoteControl", False)

    # 3. Log audit event
    self.audit.log_override("local_override")

    # 4. Internal state
    self.arbiter.force_kill("local_override")
```

### 6.3 Why this is safe during the transition gap
Between `steamd` clearing the param and `controlsd` restarting (~1 s worst case):
- `steamd` stops sending `carControl`.
- `vehicled/safety/safety_manager.py` enforces a **200 ms heartbeat timeout** on `carControl`.
  No heartbeat → `controls_allowed = False` → vehicle commands zero actuators + hold brake.
- So the vehicle is never in an undefined state; it simply **safes itself** until `controlsd` is back online.

---

## 7. SteamD Internal Changes

### 7.1 Subscribe to `carState` and `gpsLocationExternal`
Current `steamd.py`:
```python
self.sm = messaging.SubMaster(['carState', 'gpsLocationExternal'])
```

### 7.2 Control-loop gating
In `steamd.py`, wrap the existing actuator publishing:

```python
def _should_publish_control(self) -> bool:
    return (
        self.params.get_bool("SteamDRemoteControl") and
        self.arbiter.may_publish() and
        not self._local_override_active()
    )
```

If `_should_publish_control()` is False, do not send `carControl`.

### 7.3 Link-loss behavior
- **0–500 ms:** Hold last command (small decay on accel).
- **500 ms–2 s:** Safe-stop deceleration ramp (`accel = -1.0 → -2.0 m/s²`).
- **> 2 s:** Clear `SteamDRemoteControl`, zero actuators, return to `MONITORING`.
  `controlsd` auto-restarts because the param is cleared.

---

## 8. Defense in Depth (`vehicled` safety layer)

Even though `controlsd` is untouched, we still harden `vehicled/safety/safety_manager.py` so a compromised or buggy `steamd` cannot send actuators while the driver is physically overriding.

```python
# In safety_manager.py::check_tx_message (existing method)
if carState.brakePressed and desired_accel > 0:
    violation_callback("remote_brake_override")
    desired_accel = min(desired_accel, 0.0)

if abs(carState.steeringTorque) > DRIVER_TORQUE_THRESHOLD and desired_steer != 0:
    violation_callback("remote_steer_override")
    desired_steer = 0.0
```

This is a **3-line addition** to `safety_manager.py` and provides the guarantee independent of `steamd`.

---

## 9. UDP Protocol

### 9.1 Client → SteamD (control)
Binary header + JSON payload on UDP port 5100:
```
<BB3d4ddddddq> + JSON
```

### 9.2 SteamD → Client (video)
H264 MPEG-TS over UDP unicast to `udp_stream_target_addr:udp_stream_target_port`.
FFmpeg command:
```bash
ffmpeg -f rawvideo -pix_fmt bgr24 -s 2560x720 -r 30 -i - \
  -c:v libx264 -preset ultrafast -tune zerolatency \
  -f mpegts udp://<headset_ip>:5120
```

---

## 10. Implementation Phases

### Phase 0 — Param & Process Config (1 day)
- [x] Add `SteamDRemoteControl` to `common/params_keys.h` (volatile bool).
- [x] Add `not_remote_control()` helper in `system/manager/process_config.py`.
- [ ] Add `not_remote_control` to `controlsd` process condition.
- [ ] Verify: toggling the param via shell correctly starts/stops `controlsd` while `steamd` remains running.

### Phase 1 — SteamD Control Gating (2 days)
- [x] In `steamd.py`, gate `carControl` on `params.get_bool("SteamDRemoteControl")`.
- [x] On param transition `False→True`, `steamd` begins publishing `carControl`.
- [x] On param transition `True→False`, `steamd` sends one final zeroed `carControl` and stops.

### Phase 2 — Local Override (2 days)
- [x] Read `carState.brakePressed`, `gasPressed`, `steeringPressed` every tick.
- [x] Implement override triggers: brake, gas, steer, door, cancel.
- [x] On trigger: send zeroed frame, clear `SteamDRemoteControl`.

### Phase 3 — Driver Consent UI (3 days)
- [ ] `steamd` writes `SteamDControlRequest` param on UDP `request_control`.
- [ ] `ui` polls param and shows approval modal.
- [ ] `ui` writes `SteamDRemoteControl = True` on Allow; clears request param on Deny.
- [ ] Persistent banner when remote is active.

### Phase 4 — Safety & Link-Loss (2 days)
- [ ] Add 3-line dual-input check in `vehicled/safety/safety_manager.py`.
- [x] Implement safe-stop ramp in `steamd` on link loss > 500 ms.
- [x] On link loss > 2 s, clear `SteamDRemoteControl` so `controlsd` auto-restarts.
- [ ] End-to-end test: WiFi drop → safe-stop → `controlsd` resumes.

### Phase 5 — Hardening (Week 5–6)
- [ ] Sequence number + HMAC on UDP control packets (anti-replay).
- [ ] Rate-limiting on control messages (token bucket).
- [ ] Audit log: SQLite at `/data/shared/exopilot/teleop_audit.db`.
- [ ] Geofence polygon (soft gate; clears param on breach).

---

## 11. Testing Checklist

| Scenario | Expected Behavior |
|---|---|
| Normal openpilot drive | `SteamDRemoteControl=False`. `controlsd` runs normally. `steamd` streams video only. |
| Remote client requests control | UI modal appears. `controlsd` still runs. `steamd` does not publish `carControl`. |
| Driver approves | `SteamDRemoteControl=True`. Manager stops `controlsd` within 1 s. `steamd` starts publishing `carControl`. |
| Remote active, driver taps brake | `steamd` clears param immediately. Final zeroed `carControl` sent. `controlsd` restarts within 1 s. |
| Remote active, network drops 1 s | Safe-stop ramp from 500 ms. If network returns < 2 s, remote resumes if driver didn't override. |
| Remote active, network drops 3 s | `SteamDRemoteControl` cleared. `controlsd` restarts. Vehicle safe. |
| Compromised `steamd` sends accel while brake pressed | `safety_manager` rejects TX. Violation logged. Vehicle sees zero accel. |
| Driver denies request | Request param cleared. No `carControl` race ever occurs. |
| Car goes offroad | `SteamDRemoteControl` auto-clears (param flag). `controlsd` restarts. |

---

## 12. Files Changed Summary

| File | Change | Lines |
|---|---|---|
| `common/params_keys.h` | Add `SteamDRemoteControl`, `SteamDControlRequest` params | +2 |
| `system/manager/process_config.py` | Add `not_remote_control()` helper; append to `controlsd` condition | +5 |
| `selfdrive/steamd/steamd.py` | Gate `carControl` on param; read `carState` for override; clear param on override | ~+30 |
| `selfdrive/steamd/config.py` | Add UDP target config | ~+5 |
| `selfdrive/steamd/inputs.py` | UDP control protocol | existing |
| `selfdrive/steamd/video_streamer.py` | UDP unicast H264 streaming | new |
| `selfdrive/vehicled/safety/safety_manager.py` | Add brake/steer override rejection | +3 |
| `selfdrive/controls/controlsd.py` | **None** | **0** |
| `selfdrive/selfdrived/selfdrived.py` | **None** | **0** |

---

## 13. Open Questions

1. **Should `steamd` also publish `controlsState`?**
   `joystickd` does, mainly so the on-device UI still shows curvature and speed. For SteamD, the remote client is the primary viewer, so this is optional.

2. **Should openpilot need to be engaged before remote control works?**
   Proposal: **No.** Driver approval via the UI modal replaces the normal engagement sequence. This is simpler and clearer for the user.

3. **Time-bounded sessions?**
   Should the driver be able to grant remote control for e.g. 5 minutes, after which it auto-expires? Easy to add later via a timer in `steamd`.

4. **`selfdriveState.enabled` interaction?**
   Because `controlsd` is stopped, `selfdriveState` stops updating its engagement state. When `controlsd` restarts after override, it picks up from a cold state. Is this acceptable, or should we preserve `selfdriveState` across the handoff?
   → Acceptable for v1; the driver can re-engage openpilot normally after taking back control.
