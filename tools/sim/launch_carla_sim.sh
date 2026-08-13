#!/usr/bin/env bash
# Launch CARLA server + openpilot sim bridge for EOP testing.
#
# Usage:
#   ./launch_carla_sim.sh [scenario] [weather] [platform]
#
# Examples:
#   ./launch_carla_sim.sh
#   ./launch_carla_sim.sh emergency_brake clear_day rk3588
#
# Uses CARLA 0.9.16 via Docker (carlasim/carla:0.9.16) by default.
# Tarball install supported too: set CARLA_DIR=/opt/CARLA_0.9.16

set -euo pipefail

SCENARIO="${1:-}"
WEATHER="${2:-clear_day}"
PLATFORM="${3:-rk3588}"

CARLA_DIR="${CARLA_DIR:-/opt/CARLA_0.9.16}"
CARLA_HOST="${CARLA_HOST:-localhost}"
CARLA_PORT="${CARLA_PORT:-2000}"
QUALITY="${CARLA_QUALITY:-Low}"
BASEDIR="$(cd "$(dirname "$0")/../.." && pwd)"

# ExoPilot 01M (RK3588) has side cameras but no tele camera (that's ExoPilot 02M only)
BRIDGE_ARGS="${BRIDGE_ARGS:-} --side_camera"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

check_carla() {
  # Prefer a local tarball install; fall back to the carlasim docker image
  # (tools/sim/start_carla.sh flow). CARLA_MODE=docker forces docker.
  CARLA_MODE="${CARLA_MODE:-auto}"
  CARLA_DOCKER_VERSION="${CARLA_DOCKER_VERSION:-0.9.16}"
  if [ "$CARLA_MODE" = "auto" ]; then
    if [ -f "$CARLA_DIR/CarlaUE4.sh" ]; then
      CARLA_MODE=tarball
    elif docker image inspect "carlasim/carla:${CARLA_DOCKER_VERSION}" >/dev/null 2>&1 || \
         sudo -n docker image inspect "carlasim/carla:${CARLA_DOCKER_VERSION}" >/dev/null 2>&1; then
      CARLA_MODE=docker
    else
      log_error "No CARLA found: neither $CARLA_DIR/CarlaUE4.sh nor docker image carlasim/carla:${CARLA_DOCKER_VERSION}"
      log_error "Tarball: https://github.com/carla-simulator/carla/releases"
      log_error "Docker:  docker pull carlasim/carla:${CARLA_DOCKER_VERSION}"
      exit 1
    fi
  fi
  log_info "CARLA mode: $CARLA_MODE"
}

check_python_deps() {
  if ! python3 -c "import carla" 2>/dev/null; then
    log_warn "carla Python API not installed"
    log_info "Install: pip install carla==0.9.16"
  fi
}

wait_for_carla() {
  local host="$1"
  local port="$2"
  local max_attempts=60
  local attempt=0
  log_info "Waiting for CARLA server at $host:$port ..."
  while ! python3 -c "import socket; socket.create_connection(('$host', $port), timeout=1)" 2>/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
      log_error "CARLA server did not start within $max_attempts seconds"
      exit 1
    fi
    sleep 1
  done
  log_info "CARLA server is ready"
}

set_params() {
  log_info "Setting simulation parameters ..."
  python3 -c "
import os
os.environ['OPENPILOT_STUB_PARAMS_PYX'] = '1'
from openpilot.common.params import Params
p = Params()
p.put('EOPSimPlatform', '$PLATFORM')
p.put('EOPSimWeather', '$WEATHER')
p.put_bool('EOPSideCamerasEnabled', True)
p.put_bool('EOPTLSCEnabled', True)
"

  if [ -n "$SCENARIO" ]; then
    log_info "Scenario: $SCENARIO"
    python3 -c "
import os
os.environ['OPENPILOT_STUB_PARAMS_PYX'] = '1'
from openpilot.common.params import Params
p = Params()
p.put('EOPSimScenario', '$SCENARIO')
" 2>/dev/null || true
  else
    log_info "No scenario selected (free drive)"
  fi
}

launch_carla() {
  local logfile="/tmp/carla_server.log"
  log_info "Starting CARLA server (quality=$QUALITY) ..."
  log_info "Log: $logfile"

  local render_flag=""
  if [ "$QUALITY" = "Low" ]; then
    render_flag="-RenderOffScreen"
  fi

  if [ "$CARLA_MODE" = "docker" ]; then
    local docker_cmd="docker"
    docker info >/dev/null 2>&1 || docker_cmd="sudo docker"
    $docker_cmd rm -f carla_sim >/dev/null 2>&1 || true

    # Mount host NVIDIA Vulkan ICD so UE4 uses hardware GPU, not software LavaPipe
    local vulkan_mounts=""
    [ -f /usr/share/vulkan/icd.d/nvidia_icd.json ] && \
      vulkan_mounts="-v /usr/share/vulkan/icd.d/nvidia_icd.json:/usr/share/vulkan/icd.d/nvidia_icd.json:ro"
    [ -f /usr/share/glvnd/egl_vendor.d/10_nvidia.json ] && \
      vulkan_mounts="$vulkan_mounts -v /usr/share/glvnd/egl_vendor.d/10_nvidia.json:/usr/share/glvnd/egl_vendor.d/10_nvidia.json:ro"

    # shellcheck disable=SC2086
    $docker_cmd run --name carla_sim --rm -d --gpus all --net=host \
      $vulkan_mounts \
      "carlasim/carla:${CARLA_DOCKER_VERSION}" \
      /bin/bash ./CarlaUE4.sh -quality-level="$QUALITY" $render_flag -nosound > "$logfile" 2>&1
    CARLA_CONTAINER=carla_sim
    log_info "CARLA docker container: $CARLA_CONTAINER"
  else
    cd "$CARLA_DIR"
    # shellcheck disable=SC2086
    ./CarlaUE4.sh -quality-level="$QUALITY" $render_flag -nosound > "$logfile" 2>&1 &
    CARLA_PID=$!
    log_info "CARLA PID: $CARLA_PID"
  fi
}

launch_bridge() {
  log_info "Launching openpilot bridge ..."
  cd "$BASEDIR"
  exec python3 tools/sim/run_bridge.py --simulator carla --host "$CARLA_HOST" --port "$CARLA_PORT" $BRIDGE_ARGS
}

cleanup() {
  log_warn "Shutting down ..."
  if [ -n "${CARLA_CONTAINER:-}" ]; then
    docker kill "$CARLA_CONTAINER" >/dev/null 2>&1 || sudo docker kill "$CARLA_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [ -n "${CARLA_PID:-}" ] && kill -0 "$CARLA_PID" 2>/dev/null; then
    kill -TERM "$CARLA_PID" 2>/dev/null || true
    sleep 2
    kill -KILL "$CARLA_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

main() {
  check_carla
  check_python_deps
  set_params
  launch_carla
  wait_for_carla "$CARLA_HOST" "$CARLA_PORT"
  launch_bridge
}

main "$@"
