#!/usr/bin/env bash
# openpilot launcher for RK3588 (ExoPilot 01M)
# Usage: ./launch_openpilot.sh [mode]
#   mode: full (default) | model | controls | camera
#
# Supports RK3588/RK3576 hardware; refuses to start device I/O on a PC.

set -e

# --- Hardware check & platform detection ---
PLATFORM=""
for board_soc in rk3588 rk3576; do
  if grep -q "$board_soc" /proc/device-tree/compatible 2>/dev/null; then
    PLATFORM="$board_soc"
    break
  fi
done
if [[ -z "$PLATFORM" ]]; then
  echo "[openpilot] ERROR: supported Rockchip hardware not detected."
  exit 1
fi

# Source platform-specific environment if present
if [ -f /etc/profile.d/99-rockchip-${PLATFORM}-env.sh ]; then
  source /etc/profile.d/99-rockchip-${PLATFORM}-env.sh
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- Board data folders ---
# On a board openpilot keeps its settings in /data/params (one file per param,
# shared by the UI and every daemon), logs/stats/recordings under /data, and
# device keys under /persist (system/hardware/rockchip_base.py,
# exopilot/hardware/paths.py). An image that does not ship them makes the UI
# abort at its first Params() call. Create them once, owned by the run user.
ensure_board_dir() {
  local dir="$1" owner
  owner="$(id -un):$(id -gn)"
  if [ -d "$dir" ] && [ -w "$dir" ]; then
    return 0
  fi
  echo "[openpilot] Creating ${dir} (owner ${owner})..."
  if [ "$(id -u)" -eq 0 ]; then
    mkdir -p "$dir"
  elif sudo -n true 2>/dev/null; then
    sudo mkdir -p "$dir" && sudo chown "$owner" "$dir"
  elif [ -t 0 ]; then
    sudo mkdir -p "$dir" && sudo chown "$owner" "$dir"
  else
    echo "[openpilot] ERROR: ${dir} is missing or not writable, and sudo needs a password."
    echo "[openpilot] Run once: sudo mkdir -p ${dir} && sudo chown ${owner} ${dir}"
    exit 1
  fi
}
for d in /data /persist; do
  ensure_board_dir "$d"
done

# Use the uv-managed venv (Python 3.12) — required by openpilot (>=3.11)
PYTHON="${SCRIPT_DIR}/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "[openpilot] ERROR: venv not found at ${SCRIPT_DIR}/.venv"
  echo "[openpilot] Run: uv sync  (from ${SCRIPT_DIR})"
  exit 1
fi

# Python path includes the repo root and the pinned tinygrad submodule.
export PYTHONPATH="${SCRIPT_DIR}:${SCRIPT_DIR}/tinygrad_repo:${PYTHONPATH}"

MODE="${1:-full}"

# On ARM64, PyQt5 must be built against the board's system Qt because PyPI
# does not publish a compatible Linux/aarch64 wheel. The setup script installs
# the Qt headers and tools; this helper builds the binding once into the venv.
UI_BACKEND=$("$PYTHON" -c 'from common.build_profile import UI_BACKEND; print(UI_BACKEND)')
if [[ "$MODE" == "full" && "$UI_BACKEND" == "pyqt5" ]]; then
  "${SCRIPT_DIR}/scripts/ensure_pyqt5.sh" "$PYTHON"
fi

echo "[openpilot] Platform: ${PLATFORM}; UI: ${UI_BACKEND}"

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
