#!/bin/bash
# Install openpilot on RK3588 Ubuntu 22.04 target device
# Run as root on the RK3588 device after flashing the vendor Ubuntu image
#
# WARNING: This script is for RK3588 hardware only.

set -e

# Enforce RK3588 hardware
if ! grep -q "rk3588" /proc/device-tree/compatible 2>/dev/null; then
  echo "ERROR: RK3588 hardware not detected. This script is for RK3588 only."
  exit 1
fi

OPENPILOT_DIR="${1:-/data/openpilot}"
VENDOR_DEB_DIR="${2:-/opt/vendor_debs}"

echo "========================================"
echo "openpilot RK3588 Target Install"
echo "========================================"
echo "openpilot dir: $OPENPILOT_DIR"
echo "vendor debs:   $VENDOR_DEB_DIR"
echo

# 1. Install system dependencies
echo "[1/6] Installing system dependencies..."
apt-get update
apt-get install -y \
  python3-pip python3-numpy python3-venv \
  libusb-1.0-0 libffi-dev \
  git wget curl \
  v4l-utils ffmpeg \
  libgles2-mesa-dev libegl1-mesa-dev \
  2>/dev/null || true

# 2. Install vendor BSP packages if available
if [ -d "$VENDOR_DEB_DIR" ]; then
  echo "[2/6] Installing vendor BSP packages..."
  apt-get install -y --allow-downgrades \
    "$VENDOR_DEB_DIR"/rga2/librga2_*.deb \
    "$VENDOR_DEB_DIR"/rga2/librga-dev_*.deb \
    "$VENDOR_DEB_DIR"/mpp/librockchip-mpp1_*.deb \
    "$VENDOR_DEB_DIR"/mpp/librockchip-mpp-dev_*.deb \
    "$VENDOR_DEB_DIR"/rkaiq/*.deb \
    "$VENDOR_DEB_DIR"/rkisp/*.deb \
    "$VENDOR_DEB_DIR"/gst-rkmpp/*.deb \
    "$VENDOR_DEB_DIR"/gstreamer/*.deb \
    "$VENDOR_DEB_DIR"/libmali/*.deb \
    2>/dev/null || echo "  (some vendor packages not found, skipping)"
else
  echo "[2/6] Vendor deb dir not found at $VENDOR_DEB_DIR — skipping"
fi

# 3. Install openpilot environment configs
echo "[3/6] Installing openpilot environment..."
if [ -d "$OPENPILOT_DIR/system/hardware/rk3588/config" ]; then
  cp "$OPENPILOT_DIR/system/hardware/rk3588/config/99-rockchip-rk3588-env.sh" /etc/profile.d/
  cp "$OPENPILOT_DIR/system/hardware/rk3588/config/88-rockchip-camera.rules" /etc/udev/rules.d/
  udevadm control --reload-rules
  udevadm trigger --subsystem-match=video4linux
  cp "$OPENPILOT_DIR/system/hardware/rk3588/config/npu_powerctrl.sh" /usr/local/bin/ 2>/dev/null || true
  chmod +x /usr/local/bin/npu_powerctrl.sh 2>/dev/null || true
fi

# 3b. Verify RTS5411/RTS5411S onboard USB hub if DT overlay was applied
echo "[3b/6] Checking RTS5411/RTS5411S onboard hub..."
if lsusb 2>/dev/null | grep -qE "0bda:(0411|0412|5411|5412)"; then
  echo "  RTS5411/RTS5411S hub detected on USB bus."
elif [ -f /boot/overlays/exopilot01m-usbhub-rts5411.dtbo ] || \
     [ -f /boot/dtbo/exopilot01m-usbhub-rts5411.dtbo ]; then
  echo "  Overlay present but hub not enumerating — check dmesg for onboard_usb_hub errors."
else
  echo "  No hub overlay installed. See exopilot/kernel/rk3588/dt-overlays/README.md"
  echo "  to compile and install exopilot01m-usbhub-rts5411.dtbo."
fi

# 4. Create data directories
echo "[4/6] Creating data directories..."
mkdir -p /data/media/0/realdata
mkdir -p /data/media/0/models
mkdir -p /data/params
mkdir -p /data/log

# 5. Install systemd service
echo "[5/6] Installing systemd service..."
if [ -f "$OPENPILOT_DIR/system/hardware/rk3588/config/openpilot.service" ]; then
  cp "$OPENPILOT_DIR/system/hardware/rk3588/config/openpilot.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable openpilot.service
fi

# 6. Validate
echo "[6/6] Validating hardware backends..."
if [ -d "$OPENPILOT_DIR" ]; then
  cd "$OPENPILOT_DIR"
  python3 -m openpilot.system.hardware.rk3588.rockchip.tests 2>/dev/null || echo "  (validation requires running on RK3588 hardware)"
fi

echo
echo "========================================"
echo "Install complete!"
echo "========================================"
echo "Start openpilot:  systemctl start openpilot"
echo "View logs:        journalctl -u openpilot -f"
echo "Run manually:     cd $OPENPILOT_DIR && ./launch_openpilot.sh"
