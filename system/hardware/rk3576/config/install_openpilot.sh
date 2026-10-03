#!/usr/bin/env bash
# Install the ExoPilot 02M (RK3576) openpilot service and runtime dependencies.
# Run after ../exopilot/scripts/install/setup_rk3576.sh and `uv sync`.

set -euo pipefail

if [[ "$(id -u)" != 0 ]]; then
  echo "Run as root: sudo $0 [openpilot directory]" >&2
  exit 1
fi

if ! grep -qi rk3576 /proc/device-tree/compatible 2>/dev/null; then
  echo "ERROR: RK3576 hardware not detected; this installer is for ExoPilot 02M." >&2
  exit 1
fi

OPENPILOT_DIR="$(cd "${1:-/data/openpilot}" && pwd)"
echo "Installing openpilot RK3576 runtime from $OPENPILOT_DIR"

apt-get update
apt-get install -y \
  build-essential \
  python3-pip python3-numpy python3-venv \
  libusb-1.0-0 libffi-dev git wget curl \
  v4l-utils ffmpeg libgles2-mesa-dev libegl1-mesa-dev \
  qtbase5-dev qttools5-dev-tools qtwayland5 libqt5opengl5-dev

UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" && -n "${SUDO_USER:-}" ]]; then
  USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
  for candidate in "$USER_HOME/.local/bin/uv" "$USER_HOME/.cargo/bin/uv"; do
    if [[ -x "$candidate" ]]; then
      UV_BIN="$candidate"
      break
    fi
  done
fi
if [[ -z "$UV_BIN" ]]; then
  echo "Install uv for the invoking user or system-wide, then rerun this installer." >&2
  exit 1
fi
(cd "$OPENPILOT_DIR" && "$UV_BIN" sync --locked)

"$OPENPILOT_DIR/scripts/ensure_pyqt5.sh" "$OPENPILOT_DIR/.venv/bin/python"

mkdir -p /data/media/0/realdata /data/media/0/models /data/params /data/log

SERVICE_TEMPLATE="$OPENPILOT_DIR/tools/systemd/openpilot-rk3576.service"
if [[ ! -f "$SERVICE_TEMPLATE" ]]; then
  echo "Missing service template: $SERVICE_TEMPLATE" >&2
  exit 1
fi
sed "s|@@DIR@@|$OPENPILOT_DIR|g" "$SERVICE_TEMPLATE" > /etc/systemd/system/openpilot.service
systemctl daemon-reload
systemctl enable openpilot.service

echo "Install complete. Start with: systemctl start openpilot.service"
