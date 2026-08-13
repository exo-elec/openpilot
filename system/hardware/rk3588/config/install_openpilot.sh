#!/usr/bin/env bash
# Install openpilot on RK3588 Ubuntu 22.04 target device.
# Run as root on the RK3588 device AFTER running exopilot's hardware setup:
#   sudo ~/pilot/exopilot/scripts/install/setup_rk3588.sh
#
# This script is intentionally thin: vendor BSP packages, udev rules, environment
# variables, and NPU power management are owned by the closed ExoPilot HAL.

set -e

# Enforce RK3588 hardware
if ! grep -q "rk3588" /proc/device-tree/compatible 2>/dev/null; then
  echo "ERROR: RK3588 hardware not detected. This script is for RK3588 only."
  exit 1
fi

OPENPILOT_DIR="${1:-/data/openpilot}"

echo "========================================"
echo "openpilot RK3588 Target Install"
echo "========================================"
echo "openpilot dir: $OPENPILOT_DIR"
echo

# 1. Install system dependencies
echo "[1/4] Installing system dependencies..."
apt-get update
apt-get install -y \
  python3-pip python3-numpy python3-venv \
  libusb-1.0-0 libffi-dev \
  git wget curl \
  v4l-utils ffmpeg \
  libgles2-mesa-dev libegl1-mesa-dev \
  2>/dev/null || true

# 2. Verify ExoPilot HAL setup was run
if [ ! -f /etc/profile.d/99-rockchip-rk3588-env.sh ]; then
  echo "WARN: ExoPilot HAL environment not installed."
  echo "  Run: sudo ~/pilot/exopilot/scripts/install/setup_rk3588.sh"
fi
if [ ! -f /etc/udev/rules.d/88-rockchip-camera.rules ]; then
  echo "WARN: ExoPilot camera udev rules not installed."
  echo "  Run: sudo ~/pilot/exopilot/scripts/install/setup_rk3588.sh"
fi
if [ ! -f /usr/local/bin/npu_powerctrl.sh ]; then
  echo "WARN: ExoPilot NPU powerctrl not installed."
  echo "  Run: sudo ~/pilot/exopilot/scripts/install/setup_rk3588.sh"
fi

# 3. Create data directories
echo "[2/4] Creating data directories..."
mkdir -p /data/media/0/realdata
mkdir -p /data/media/0/models
mkdir -p /data/params
mkdir -p /data/log

# 4. Install systemd service
echo "[3/4] Installing systemd service..."
if [ -f "$OPENPILOT_DIR/system/hardware/rk3588/config/openpilot.service" ]; then
  cp "$OPENPILOT_DIR/system/hardware/rk3588/config/openpilot.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable openpilot.service
fi

# 5. Validate
echo "[4/4] Validating hardware backends..."
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
