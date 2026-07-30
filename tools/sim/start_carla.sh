#!/bin/bash
set -e

# CARLA Simulator Launcher for EOP/openpilot
# Supports CARLA 0.9.15, 0.9.16, and latest (0.10.x UE5)
#
# ⚠️  REQUIRES a discrete GPU (NVIDIA GTX 1060+ or AMD RX 580+) with Vulkan.
#     Intel integrated graphics (HD/Iris/Xe) are NOT supported — CARLA will crash.
#     For low-resource machines, use MetaDrive: run_bridge.py --simulator metadrive
#
# PREREQUISITES (NVIDIA):
#   sudo apt-get install nvidia-container-toolkit
#   sudo systemctl restart docker
#
# CARLA Python client (run once):
#   sudo docker create --name carla_whl carlasim/carla:0.9.16
#   sudo docker cp carla_whl:/workspace/PythonAPI/carla/dist/carla-0.9.16-cp312-cp312-manylinux_2_31_x86_64.whl /tmp/
#   sudo docker rm carla_whl
#   uv pip install /tmp/carla-0.9.16-cp312-cp312-manylinux_2_31_x86_64.whl --python .venv/bin/python
#
# Usage:
#   ./start_carla.sh              # Auto-detect best version
#   ./start_carla.sh 0.9.16       # Specific version
#   QUALITY=Low ./start_carla.sh  # Low quality for CI/testing
#   GPU=1 ./start_carla.sh        # Force GPU 1 (for dual-GPU setups)

CARLA_VERSION="${1:-${CARLA_VERSION:-0.9.16}}"
QUALITY="${QUALITY:-Low}"           # Low | Medium | High | Epic
RENDER_MODE="${RENDER_MODE:-}"     # Empty for display, "-RenderOffScreen" for headless
GPU_FLAG="${GPU_FLAG:-}"           # Empty for default, or "-gpu 0" / "-gpu 1"

# For dual RTX 3090 setups: default to GPU 0 for CARLA
if [[ -n "${GPU}" ]]; then
  GPU_FLAG="-gpu ${GPU}"
fi

# If running on a server without display, auto-enable offscreen
if [[ -z "$DISPLAY" ]]; then
  echo "No DISPLAY detected, forcing -RenderOffScreen"
  RENDER_MODE="-RenderOffScreen"
fi

# Docker image
carla_image="carlasim/carla:${CARLA_VERSION}"

echo "Starting CARLA ${CARLA_VERSION} (Quality: ${QUALITY})"
echo "  Render: ${RENDER_MODE:-display}"
echo "  GPU: ${GPU:-auto}"

# Stop any existing container
docker kill carla_sim 2>/dev/null || true
docker rm -f carla_sim 2>/dev/null || true

# Build docker run flags
DOCKER_ARGS=(
  --name carla_sim
  --rm
  --gpus all
  --net=host
  -e DISPLAY="${DISPLAY}"
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw
)

# Mount host NVIDIA Vulkan ICD so the container uses GPU rendering, not LavaPipe.
# Without this, UE4 falls back to software Vulkan which is unusably slow.
if [ -f /usr/share/vulkan/icd.d/nvidia_icd.json ]; then
  DOCKER_ARGS+=(
    -v /usr/share/vulkan/icd.d/nvidia_icd.json:/usr/share/vulkan/icd.d/nvidia_icd.json:ro
  )
fi
# Also mount EGL for GPU offscreen rendering
if [ -f /usr/share/glvnd/egl_vendor.d/10_nvidia.json ]; then
  DOCKER_ARGS+=(
    -v /usr/share/glvnd/egl_vendor.d/10_nvidia.json:/usr/share/glvnd/egl_vendor.d/10_nvidia.json:ro
  )
fi

# Run CARLA
docker run "${DOCKER_ARGS[@]}" \
  "${carla_image}" \
  /bin/bash ./CarlaUE4.sh \
    -quality-level="${QUALITY}" \
    -nosound \
    -benchmark \
    -fps=20 \
    ${RENDER_MODE} \
    ${GPU_FLAG}
