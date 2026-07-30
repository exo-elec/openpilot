# RK3588 Hardware Platform (ExoPilot 01M)

This directory contains the hardware abstraction, camera configuration, and target device configs for the **RK3588** platform used by **ExoPilot 01M**.

## Platform Overview

| Feature | RK3588 (ExoPilot 01M) |
|---------|----------------------|
| SoC | Rockchip RK3588 (cost-optimized RK3588, quad A76 + quad A55) |
| NPU | RKNPU2 (6 TOPS, RK3576-compatible runtime) |
| RGA | Rockchip RGA 2D accelerator |
| MPP | Rockchip Media Process Platform |
| GPU | Mali-G610 MC4 (OpenGL ES 3.2, Vulkan 1.2) |
| RAM | 4 GB LPDDR4X |
| Storage | 64 GB eMMC + SD card |
| Camera | 4× MIPI CSI-2 + 3× USB UVC |
| Audio | I2S DAC (speaker) + USB audio |
| Voice Input | ❌ No on-board mic (steering-torque DMS only) |
| Speaker | ✅ Yes (I2S DAC) |
| Face Camera | ❌ Repurposed as rear_camera (170° UVC) |
| Hailo-8 | ❌ Not present on ExoPilot 01M |
| Night IR | ❌ No IR illuminator |
| USB fan-out | ✅ Realtek RTS5411S onboard hub (1 × USB3 → 4 × USB3/USB2) on `usb_drd0_dwc3` (USB-C) — see `config/dt-overlays/` |

## Driver Attention Monitoring

ExoPilot 01M does **not** have a driver-facing camera or Hailo-8 coprocessor.
Instead, `driverd` runs in **steering-torque-only mode**:

- Hands-on-wheel detection via torque sensor
- Speed-dependent engagement threshold
- Escalating warnings (visual → audible → pre-brake)

No face detection, no head pose, no eye gaze.

## Directory Layout

```
system/hardware/rk3588/
├── hardware.py              # RK3588Hardware class (detect, capabilities, backends)
├── camera_config.py         # USB camera configurations (side_left, side_right, rear_camera)
├── config/
│   ├── 88-rockchip-camera.rules    # udev rules for /dev/video-cameraN + USB cameras
│   ├── 99-rockchip-rk3588-env.sh   # Environment variables (LD_LIBRARY_PATH, etc.)
│   ├── npu_powerctrl.sh           # NPU power domain control
│   ├── openpilot.service          # systemd service definition
│   ├── install_target.sh          # One-shot target install script
│   └── dt-overlays/               # RTS5411S onboard USB hub overlay + kernel config fragment
├── tests.py                 # Host-side config validation (no hardware needed)
```

## Quick Start (on target device)

```bash
# 1. Install dependencies and configs
sudo bash system/hardware/rk3588/config/install_target.sh /data/openpilot

# 2. Start openpilot
sudo systemctl start openpilot

# 3. Check logs
sudo journalctl -u openpilot -f
```

Or run manually:
```bash
cd /data/openpilot
./launch_openpilot.sh full
```

## Hardware Backends

```python
from openpilot.system.hardware.rockchip import RockchipBackendFactory

# RGA 2D operations
rga = RockchipBackendFactory.create("rga")
rga.resize(image, 640, 480)

# NPU inference
rknn = RockchipBackendFactory.create("rknn")
rknn.load_model("model.rknn")
outputs = rknn.infer(inputs)

# Video codec
mpp = RockchipBackendFactory.create("mpp")
mpp.create_decoder("h264_0", config)
```

All backends are loaded lazily. On non-RK3588 hosts they gracefully report `not available` because the `.so` files are aarch64-only.

## Camera Array

Physical layout: **stereo pair on TOP**, **mono cameras on BOTTOM**.
Bottom mono arrangement: **wide_road (+80 mm) — road (0 mm)**.

| Position | Camera | Sensor | Mode | Resolution | Y Offset | Purpose |
|----------|--------|--------|------|------------|----------|---------|
| TOP | stereo_left | GC4653 | SDR | 1280×720 | **−40 mm** | Depth estimation |
| TOP | stereo_right | GC4653 | SDR | 1280×720 | **+40 mm** | Depth estimation |
| BOTTOM (left) | wide_road | OX03C10 | HDR4 | 1920×1280 | **+80 mm** | Wide-angle cross-traffic (120°) |
| BOTTOM (right) | road | OX03C10 | HDR4 | 1920×1280 | **0 mm** | Forward perception (60°) |

**Stereo baseline:** 80 mm

### USB Cameras (via RTS5411S Hub)

| Camera | Type | Resolution | Y Offset | Z Height | Yaw | Purpose |
|--------|------|------------|----------|----------|-----|---------|
| side_left | UVC | 1280×720 | **+850 mm** | ~750 mm | **150°** | Left blind spot, adjacent lane |
| side_right | UVC | 1280×720 | **−850 mm** | ~750 mm | **210°** | Right blind spot, adjacent lane |
| rear_camera | UVC | 640×480 | **0 mm** | ~500 mm | **180°** | Reverse / backup view |

## Validation

Host-side (no hardware required):
```bash
python3 -m openpilot.system.hardware.rk3588.tests
```

Target-side (requires RK3588 hardware):
```bash
python3 -m openpilot.system.hardware.rockchip.tests
```
