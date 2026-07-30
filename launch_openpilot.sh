#!/bin/bash
# openpilot launcher for RK3588 (ExoPilot 01M)
# Usage: ./launch_openpilot.sh [mode]
#   mode: full (default) | model | controls | camera
#
# WARNING: This script is for RK3588 hardware only. It will fail on PC/x86_64.

set -e

# --- Hardware check & platform detection ---
if ! grep -q "rk3588" /proc/device-tree/compatible 2>/dev/null; then
  echo "[openpilot] ERROR: RK3588 hardware not detected."
  echo "[openpilot] This launcher is for ExoPilot 01M (RK3588) only."
  exit 1
fi
PLATFORM="rk3588"

# Source platform-specific environment if present
if [ -f /etc/profile.d/99-rockchip-${PLATFORM}-env.sh ]; then
  source /etc/profile.d/99-rockchip-${PLATFORM}-env.sh
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Use the uv-managed venv (Python 3.12) — required by openpilot (>=3.11)
PYTHON="${SCRIPT_DIR}/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "[openpilot] ERROR: venv not found at ${SCRIPT_DIR}/.venv"
  echo "[openpilot] Run: uv sync  (from ${SCRIPT_DIR})"
  exit 1
fi

# Python path includes repo root
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"

MODE="${1:-full}"

echo "[openpilot] Platform: ExoPilot 01M (RK3588)"

# --- Camera module loading ---
echo "[openpilot] Loading camera modules..."
modprobe gc4653 2>/dev/null || true
modprobe ov03c10 2>/dev/null || true
sleep 0.5

# --- Run mode ---
case "$MODE" in
  full)
    echo "[openpilot] Starting full system..."
    exec "$PYTHON" system/manager/manager.py
    ;;

  model)
    echo "[openpilot] Starting modeld only..."
    exec "$PYTHON" selfdrive/modeld/modeld.py
    ;;

  controls)
    echo "[openpilot] Starting controlsd + plannerd..."
    exec "$PYTHON" selfdrive/controls/controlsd.py
    ;;

  camera)
    echo "[openpilot] Starting camera pipeline..."
    exec "$PYTHON" system/v4l2d/v4l2d.py
    ;;

  *)
    echo "Usage: $0 [full|model|controls|camera]"
    exit 1
    ;;
esac
