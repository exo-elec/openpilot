# SteamD — VR Teleoperation Daemon

Unified VR teleoperation daemon replacing `teleoprtc_repo` + `webrtcd` + `bodyteleop` + `joystickd` + `vr_streamd` + `vr_teleop`.

## Overview

SteamD provides VR teleoperation for Tesla/gateway vehicles with:
- **VR View**: Stereo camera streaming via low-latency UDP H264
- **Assist View**: Picture-in-picture with stereo depth assist overlay
- **Direct Control**: Low-latency vehicle control via UDP binary/JSON protocol
- **Web Interface**: Browser-based status monitor

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         SteamD                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  Web Server │  │  UDP Input  │  │  Control Loop (100Hz)   │ │
│  │  Port 5000  │  │  Port 5100  │  │  - Safety watchdog      │ │
│  │  aiohttp    │  │  binary/JSON│  │  - Timeout handling     │ │
│  │  - HTML/JS  │  │  - VR ctrl  │  │  - carControl pub       │ │
│  └─────────────┘  └──────┬──────┘  └─────────────────────────┘ │
│         │                │                                      │
│         └────────────────┼──────────────────────────────────────┘
│                          │                                      │
│  ┌───────────────────────┴───────────────────────────────────┐  │
│  │                    VisionIPC / Cereal                      │  │
│  │  - wideRoad camera (PiP overlay)                          │  │
│  │  - road camera (main view)                                │  │
│  │  - stereo left/right (VR depth)                           │  │
│  │  - carControl (vehicle output)                            │  │
│  │  - carState (vehicle feedback)                            │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ UDP H264 (MPEG-TS)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    VR Headset (Pico / Quest)                     │
│  ┌─────────────────────┐  ┌─────────────────────────────────┐   │
│  │   Stereo VR View    │  │   Assist Overlay (hold btn)     │   │
│  │   - Side-by-side    │  │   - Depth verification          │   │
│  │   - Head-locked     │  │   - Stereo crop center          │   │
│  └─────────────────────┘  └─────────────────────────────────┘   │
│                                                                  │
│  Controls: VR Controller (A/B/X/Y + Grips)                      │
│  - L-Thumbstick X : Steering (legacy clients)                    │
│  - Hand roll/yaw  : Steering (OpenArmX APK, no thumbstick)       │
│  - R-Trigger      : Throttle                                     │
│  - L-Trigger      : Brake                                        │
│  - L-Grip + R-Grip: Deadman (must hold both)                     │
│  - A              : Engage (deadman required)                    │
│  - B              : Disengage                                    │
│  - X/Y            : Camera view (left/right)                     │
│  - Hold any face  : Assist overlay                               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `steamd.py` | Main daemon (web server + control loop + UDP streamer init) |
| `config.py` | SteamDConfig dataclass |
| `inputs.py` | Input abstractions (UDP, Joystick, Keyboard) |
| `camera_client.py` | VisionIPC multi-camera client |
| `video_streamer.py` | UDP unicast H264 streamer (FFmpeg) |
| `hud_renderer.py` | Racing-game telemetry HUD overlays |
| `video_utils.py` | NV12→RGB conversion, track ID helpers |
| `arbiter.py` | Control authority + local override logic |
| `publisher.py` | carControl message builder / sender |
| `audit.py` | SQLite audit logging |
| `geofence.py` | GPS geofence gate |
| `stereo_correction.py` | Stereo baseline correction for VR IPD |

## Network Setup

### Option A: Direct Wi-Fi (LAN) — Garage / Lot
1. Car and headset on same Wi-Fi
2. Set `udp_stream_target_addr` to headset IP (e.g., `192.168.1.20`)
3. Headset sends control to car IP on port 5100

### Option B: Remote over 4G (WireGuard VPN) — Road / Remote
**Recommended for real-world use.**

```
[Pico 4] ←Wi-Fi/4G→ [WireGuard Server] ←EC25 4G→ [Car RK3588]
   10.200.200.5      (cloud VPS or         10.200.200.2
                     home with public IP)
```

**Why WireGuard:**
- EC25 is behind CGNAT — inbound UDP blocked
- WireGuard uses outbound UDP (port 51820) which CGNAT allows
- Once tunnel is up, both sides have static VPN IPs
- ChaCha20-Poly1305 encryption — no need for app-layer crypto
- Kernel module on Linux (RK3588) — very low CPU overhead

**Step 1: Set up WireGuard server** (cloud VPS or home router with public IP)
```bash
# On Ubuntu server
sudo apt install wireguard
sudo wg genkey | tee privatekey | wg pubkey > publickey

# /etc/wireguard/wg0.conf
[Interface]
PrivateKey = <server_private_key>
Address = 10.200.200.1/24
ListenPort = 51820
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

[Peer] # Car
PublicKey = <car_public_key>
AllowedIPs = 10.200.200.2/32

[Peer] # Headset
PublicKey = <headset_public_key>
AllowedIPs = 10.200.200.5/32
```

**Step 2: Car (RK3588) WireGuard client**
```bash
sudo apt install wireguard

# /etc/wireguard/wg0.conf
[Interface]
PrivateKey = <car_private_key>
Address = 10.200.200.2/24

[Peer]
PublicKey = <server_public_key>
AllowedIPs = 10.200.200.0/24
Endpoint = <server_public_ip>:51820
PersistentKeepalive = 25
```

Enable:
```bash
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0
```

**Step 3: Headset (Pico 4) WireGuard**
Pico OS is Android-based. Install the WireGuard APK from Pico Store or sideload:
```bash
adb install wireguard-<version>.apk
```

Create tunnel with same server public key, assign `10.200.200.5/24`.

**Step 4: SteamD config**
```python
config = SteamDConfig(
    udp_stream_target_addr="10.200.200.5",  # headset WireGuard IP
    udp_stream_target_port=5120,
    udp_listen_addr="0.0.0.0",               # accept control from any WG peer
    udp_listen_port=5100,
)
```

**Firewall rules on server:**
```bash
# Allow WireGuard
sudo ufw allow 51820/udp

# Optional: block video stream port from internet (only allow via WG)
sudo ufw deny 5120/udp
sudo ufw allow from 10.200.200.0/24 to any port 5120/udp
```

**Verify connectivity:**
```bash
# On car
ping 10.200.200.5

# On headset (if shell access available)
ping 10.200.200.2
```

**Bandwidth check:**
```bash
# On car, test upload to server
iperf3 -c 10.200.200.1

# Expected: 10-30 Mbps upload on 4G LTE
# H264 stream needs ~4 Mbps — plenty of headroom
```

## Usage

```bash
# Enable via params
python3 -c "from openpilot.common.params import Params; Params().put_bool('SteamDEnabled', True)"
```

### VR Controller Mapping

| Button | Action |
|--------|--------|
| L-Thumbstick X | Steering (legacy clients) |
| Hand roll/yaw | Steering (OpenArmX APK) |
| R-Trigger | Throttle (progressive) |
| L-Trigger | Brake (progressive) |
| **L-Grip + R-Grip** | **Deadman — must hold both** |
| A | Engage (only while deadman held) |
| B | Disengage |
| X | Switch to left camera view |
| Y | Switch to right camera view |
| Hold any face button | Show stereo assist overlay |

Wire protocol: client sends `gas`/`brake` as 0..1 normalized; SteamD scales
to m/s² before publishing `carControl.actuators.accel`.

### OpenArmX APK Compatibility

OpenArmX sends hand pose (position + orientation quaternion) but no thumbstick.
SteamD auto-detects the text protocol and derives steering from hand orientation:

| Config | Behavior |
|--------|----------|
| `openarmx_steer_source="roll"` | Hand roll → steering (like turning a wheel) |
| `openarmx_steer_source="yaw"` | Hand yaw → steering (like pointing left/right) |
| `openarmx_steer_source="pitch"` | Hand pitch → steering |
| `openarmx_steer_source="position"` | Hand lateral offset → steering (legacy) |

```python
config = SteamDConfig(
    openarmx_steer_source="roll",
    openarmx_max_roll_deg=45.0,
)
```

## Safety Architecture

- **Process-level mutex**: `SteamDRemoteControl` param gates both SteamD publishing and `controlsd` startup.
- **Lazy PubMaster**: SteamD does not register as a `carControl` publisher until authorized.
- **Local override**: brake, gas, steering torque, door open → immediate disengage + auto-restart `controlsd`.
- **Link-loss safe-stop**: hard-brake ramp until standstill (not coasting).
- **Ignition gate**: never publish if ignition is off.
- **Geofence**: reject commands outside configured GPS polygon.

## Configuration

```python
from openpilot.selfdrive.steamd.config import SteamDConfig

config = SteamDConfig(
    web_port=5000,
    enable_udp_input=True,
    enable_udp_stream=True,
    udp_stream_target_addr="192.168.1.20",  # headset IP
    udp_stream_target_port=5120,
    udp_stream_fps=30,
    udp_stream_bitrate_kbps=4000,
    control_timeout_sec=0.5,
)
```

## Comparison with comma.ai Stack

| Aspect | comma.ai (teleoprtc) | SteamD |
|--------|---------------------|--------|
| Components | 3 (web.py, webrtcd, teleoprtc) | 1 (steamd) |
| Protocol | WebRTC | UDP H264 + binary/JSON |
| Hardware | comma body only | Tesla/gateway |
| VR support | Basic browser | Native Pico/Quest |
| Dependencies | Many (aiortc, etc.) | FFmpeg + aiohttp |

## Migration from teleoprtc

SteamD replaces:
- `teleoprtc_repo/` submodule → **Removed**
- `system/webrtc/webrtcd` → **Removed**
- `tools/bodyteleop/` → **Replaced with SteamD UDP protocol**
- `selfdrive/vr_streamd/` → **Merged into SteamD**
- `selfdrive/vr_teleop/` → **Merged into SteamD**

## Safety

- **Heartbeat timeout**: 500ms (disengages if no control input)
- **Zero-command on disconnect**: Immediately stops vehicle
- **Engagement button**: Must explicitly engage control
- **WireGuard encryption**: Transport-layer encryption for remote use

## TODO

- [x] UDP unicast streaming (replaces WebRTC)
- [x] Racing-game HUD overlay (speed, gear, battery, g-force)
- [x] Stereo correction (ExoPilot 01M baseline)
- [x] OpenArmX quaternion steering (roll/yaw/pitch)
- [ ] RGA preprocessing for efficient video encoding
- [ ] H264 hardware encoding via MPP on RK3588
- [ ] Sequence-number + HMAC on control packets (anti-replay)
