# VR Teleoperation & Camera Streaming

**Date:** 2026-05-09  
**Status:** ✅ Implemented (consolidated into SteamD)

---

## Overview

openpilot supports teleoperation and stereoscopic camera streaming via the same unified protocol used by HumRobot and VisionPilot. A single headset APK (Pico / Meta Quest) can connect to any of the three platforms.

**Architecture change:** `vr_teleop` and `vr_streamd` have been merged into `steamd` as `UdpInput` and `UdpVideoStreamer`. SteamD remains the single source of external control authority.

---

## Camera Streaming

- **Daemon:** `steamd` (UDP streamer thread)
- **Path:** `selfdrive/steamd/video_streamer.py` — `UdpVideoStreamer`
- **Output:** `udp://<headset_ip>:5120` (MPEG-TS H264 unicast)
  - Target IP is configurable via `udp_stream_target_addr` in `config.py`
  - Use WireGuard tunnel IP (e.g. `10.200.200.5`) for 4G/CGNAT remote access
  - Use LAN IP for direct Wi-Fi (car AP mode)

### Features
- Reads from `MultiCameraClient` (STEREO_LEFT / STEREO_RIGHT, or ROAD / WIDE_ROAD fallback)
- Side-by-side stereo composition
- **Auto-detected baseline:** 80 mm (ExoPilot 01M) → 63 mm human IPD
- FFmpeg H264 encoding (`libx264` / `h264_rkmpp`, `ultrafast`, `zerolatency`)
- Camera switching via headset buttons
- Center assist overlay (press-and-hold any face button)
- Racing-game HUD overlay: speed arc, battery bar, throttle/brake bars, g-force ball, steering wheel
- Wide camera PiP always visible bottom-left

### Camera Button Mapping
| Button | View |
|--------|------|
| A | Wide (front) |
| B | Rear |
| X | Left |
| Y | Right |
| Hold any face button | Center assist overlay (stereo-corrected crop) |

### Enable
```bash
params put SteamDEnabled 1
params put SteamDRemoteControl 1
```

For remote access over 4G, also configure WireGuard (see `steamd/README.md`).

---

## Teleoperation Control

- **Daemon:** `steamd` (UDP input thread)
- **Path:** `selfdrive/steamd/inputs.py` — `UdpInput`
- **Input:** UDP port 5100 (unified protocol)
- **Output:** `carControl` cereal message via `CarControlPublisher`

### Control Mapping (SteamD native protocol)
| Input | Mapping |
|-------|---------|
| Left thumbstick X | `actuators.steer` (-1.0 … 1.0) |
| Right trigger | `actuators.accel` positive (m/s²) |
| Left trigger | `actuators.accel` negative (brake, m/s²) |
| Button A | Engage |
| Button B | Disengage |
| Both grips | Deadman switch |

### OpenArmX APK Compatibility
SteamD also accepts the OpenArmX text protocol (used by Pico 4 / Meta Quest APKs):
```
LEFT/RIGHT x y z qx qy qz qw trigger grip btn_a btn_b btn_x btn_y rate timestamp
```
`UdpInput._parse()` auto-detects this format and maps it to `UdpControllerState`.

> **Note:** OpenArmX sends hand pose (position + orientation) but no thumbstick axes. Steering must be derived from hand roll/yaw, or the APK source modified to add thumbstick output.

> **⚠️ OpenArmX License:** The OpenArmX APK is licensed under CC BY-NC-SA 4.0 (non-commercial, share-alike). Before redistributing or modifying, verify compliance with the license terms.

### Headset Video Player Setup
The OpenArmX APK sends controller data but does **not** include an H264 video receiver. You need a separate player on the headset:

| Platform | Recommended Player | Setup |
|----------|-------------------|-------|
| Pico 4 | VLC for Android (sideload) | Open network stream: `udp://@:5120` |
| Meta Quest | VLC for Android (sideload) | Open network stream: `udp://@:5120` |
| Custom APK | Unity VideoPlayer + custom UDP receiver | Bind `udp://<headset_ip>:5120`, decode MPEG-TS H264 |

For side-by-side stereo, ensure the player does not apply any 3D/SBS conversion — SteamD already outputs pre-composed SBS frames scaled to the target IPD.

Alternatively, use a **SteamD-native client** (HumRobot APK or custom UDP client) that receives H264 and sends the binary/JSON control protocol on port 5100.

### Safety
- Same dual-layer safety as SteamD:
  - Software layer (`ControlArbiter`): local override, link-loss, geofence
  - Hardware layer (TC275): controls_allowed gate
- `controlsd` auto-restarts when remote disengages (mutex via `JoystickDebugMode` param)
- Heartbeat timeout (500 ms) → progressive safe stop

---

## Integration with Existing Stack

```
Headset ──UDP 5100──► steamd ──carControl──► vehicled ──► CAN (TC275 gateway)
                        │
                        ▼
                  carState (feedback)

steamd ──UDP 5120 (H264)──► Headset (video player)
```

`steamd` publishes directly to the `carControl` cereal topic that `controlsd` normally owns. When `JoystickDebugMode` is true, `controlsd` refuses to start (process_config condition). When local override fires, `steamd` drops its PubMaster and clears the param, allowing `controlsd` to restart.

---

## See Also

- `selfdrive/steamd/README.md` — WireGuard VPN setup for 4G/CGNAT
- `selfdrive/steamd/DRONE_ROADMAP.md` — Remote control architecture & safety
- `selfdrive/steamd/TELEOP_AUDIT.md` — Current security audit (post-WebRTC)
- HumRobot: `docs/vr_teleop_protocol.md` — Unified protocol specification
