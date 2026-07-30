# RK3588 Target Device Configuration

Files in this folder are **reference configurations** derived from the vendor SDK.
They should be installed on the RK3588 Ubuntu 22.04 target device.

## Files

| File | Install Target | Purpose |
|------|---------------|---------|
| `99-rockchip-rk3588-env.sh` | `/etc/profile.d/` | GStreamer + Qt + GPU environment variables |
| `88-rockchip-camera.rules` | `/etc/udev/rules.d/` | Stable `/dev/video-cameraN` symlinks |
| `npu_powerctrl.sh` | `/usr/local/bin/` | NPU suspend/resume power management |
| `openpilot.service` | `/etc/systemd/system/` | systemd service for openpilot |
| `dt-overlays/` | kernel tree or `/boot/overlays/` | RTS5411S USB hub device tree overlays |

## Vendor Packages Required

The vendor SDK provides these `.deb` packages in `debian/packages/arm64/`.
Install them on the target Ubuntu 22.04 image:

```bash
# Core libraries (from vendor SDK or built from submodules)
sudo apt install -y \
  ./debian/packages/arm64/rga2/librga2_*.deb \
  ./debian/packages/arm64/rga2/librga-dev_*.deb \
  ./debian/packages/arm64/mpp/librockchip-mpp1_*.deb \
  ./debian/packages/arm64/mpp/librockchip-mpp-dev_*.deb \
  ./debian/packages/arm64/rknpu2/rknpu2.tar

# Camera / ISP
sudo apt install -y \
  ./debian/packages/arm64/rkaiq/*.deb \
  ./debian/packages/arm64/rkisp/*.deb

# GStreamer Rockchip plugins
sudo apt install -y \
  ./debian/packages/arm64/gst-rkmpp/*.deb \
  ./debian/packages/arm64/gstreamer/*.deb

# GPU driver
sudo apt install -y \
  ./debian/packages/arm64/libmali/*.deb
```

## Environment Variables

The most critical env vars for openpilot (set by `99-rockchip-rk3588-env.sh`):

| Variable | Value | Why |
|----------|-------|-----|
| `GST_GL_API` | `gles2` | GStreamer uses GLES2 for zero-copy textures |
| `GST_V4L2_USE_LIBV4L2` | `1` | Required for Rockchip ISP pixel formats |
| `GST_V4L2SRC_DEFAULT_DEVICE` | `/dev/video-camera0` | Default camera device |
| `GST_VIDEO_DECODER_QOS` | `0` | Prevents frame dropping in decoder |
| `QT_XCB_GL_INTEGRATION` | `xcb_egl` | GPU-accelerated Qt UI |

## NPU Power Management

The RK3588 NPU draws ~500mW when idle. Call `npu_powerctrl.sh suspend` before sleep:

```bash
# systemd service example
[Service]
ExecStartPre=/usr/local/bin/npu_powerctrl.sh resume
ExecStopPost=/usr/local/bin/npu_powerctrl.sh suspend
```

## USB Hub (RTS5411S)

ExoPilot 01M uses a USB 3.0 to RTS5411S hub for side cameras and TC275 bootloader.
See `dt-overlays/README.md` for build and install instructions.

## Model Path Convention

Vendor SDK places demo models at:
```
/usr/share/model/RK3588/mobilenet_v1.rknn
```

openpilot should use:
```
/data/media/0/models/
```
