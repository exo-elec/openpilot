# EOP Dev PC Development Guide

**Workflow**: x86_64 dev PC → CARLA testing → ARM (RK3588) deployment

---

## Overview

EOP uses a dual-backend inference system that works transparently on both:
- **x86_64 dev PC**: ONNX Runtime (CPU or CUDA)
- **ARM edge hardware**: RKNN NPU (RK3588)

The `InferenceClient.inference_backend()` API automatically selects the best available backend.

---

## Setup

### 1. Python environment

```bash
cd /home/vcar/pilot/openpilot
# Python 3.12 venv (pre-created)
.venv/bin/python --version   # 3.12.x
```

### 2. Install Python dependencies

```bash
uv pip install onnxruntime scipy --python .venv/bin/python
```

### 3. Model files

Models are **not committed to git** (large binaries). Place them in `models/`:

```
models/
  onnx/
    driving_vision.onnx    # 45 MB — dragonpilot 0.10.0 pre-build
    driving_policy.onnx    # 14 MB — dragonpilot 0.10.0 pre-build
  rknn/                    # Populated on ARM hardware or via convert_models_to_rknn.py
  hef/                     # Hailo HEF files (optional)
```

The driving ONNX models were extracted from dragonpilot `pre-build` branch:
```bash
cd /path/to/dragonpilot
git show pre-build:selfdrive/modeld/models/driving_vision.onnx > \
    /path/to/openpilot/models/onnx/driving_vision.onnx
git show pre-build:selfdrive/modeld/models/driving_policy.onnx > \
    /path/to/openpilot/models/onnx/driving_policy.onnx
```

---

## Running daemons on dev PC

### Import smoke test (all 22 daemons)

```bash
OPENPILOT_STUB_PARAMS_PYX=1 PYTHONPATH=. .venv/bin/python -m pytest \
    selfdrive/test/test_daemon_imports.py --override-ini="addopts=" -v
```

### Test ONNX inference (driving vision model)

```bash
.venv/bin/python -c "
from openpilot.system.inferenced import InferenceClient
from openpilot.system.inferenced.compute import HAL, HALConfig, ModelConfig
import numpy as np

HAL(HALConfig()).initialize()
client = InferenceClient('test')
backend = client.inference_backend()
print('Backend:', backend.backend_type.name)

backend.load_model(ModelConfig(name='driving_vision', path=''))
result = backend.infer('driving_vision', {
    'img': np.zeros((1,12,128,256), dtype=np.uint8),
    'big_img': np.zeros((1,12,128,256), dtype=np.uint8),
})
print('Output shape:', result.outputs['outputs'].shape)
print('Inference time:', round(result.inference_time_ms, 1), 'ms')
"
```

### Force ONNX backend (bypass mock RKNN)

```bash
export EOP_BACKEND=onnx
```

---

## Simulation options

### Option A — MetaDrive (low-resource PC, no GPU required)

MetaDrive is a lightweight pure-Python simulator. Use it on any x86 PC
(integrated GPU or CPU only). Ideal for this dev PC.

```bash
# Terminal 1 — openpilot daemons
tools/sim/launch_openpilot.sh

# Terminal 2 — MetaDrive bridge
.venv/bin/python tools/sim/run_bridge.py --simulator metadrive
```

No server needed — MetaDrive runs in-process.

---

### Option B — CARLA (dedicated GPU required)

CARLA 0.9.16 requires a **discrete NVIDIA or AMD GPU** for Vulkan rendering.
Intel integrated graphics (HD/Iris/Xe) are not sufficient — CARLA exits on init.

**Minimum:** NVIDIA GTX 1060 6GB or equivalent with NVIDIA Docker support.

**Setup (on GPU machine):**

```bash
# 1. Install Docker + NVIDIA container toolkit
sudo apt-get install docker.io nvidia-container-toolkit
sudo systemctl restart docker

# 2. Pull image (~29GB)
sudo docker pull carlasim/carla:0.9.16

# 3. Install CARLA Python client (extract from image)
sudo docker create --name carla_whl carlasim/carla:0.9.16
sudo docker cp carla_whl:/workspace/PythonAPI/carla/dist/carla-0.9.16-cp312-cp312-manylinux_2_31_x86_64.whl /tmp/
sudo docker rm carla_whl
uv pip install /tmp/carla-0.9.16-cp312-cp312-manylinux_2_31_x86_64.whl \
    --python .venv/bin/python

# 4. Launch (3 terminals)
sudo tools/sim/start_carla.sh 0.9.16          # Terminal 1: CARLA server
tools/sim/launch_openpilot.sh                  # Terminal 2: openpilot daemons
.venv/bin/python tools/sim/run_bridge.py \    # Terminal 3: bridge
    --simulator carla --dual_camera --stereo_camera
```

---

### Known dev PC limitations (both simulators)

| Component | Dev PC behavior |
|---|---|
| `modeld` | Runs with ONNX backend (~47ms/frame CPU); no CL frame prep (numpy fallback) |
| `monod` | Starts but skips models if `models/onnx/yolo_640.onnx` missing |
| `stereod` | ACL backend unavailable; falls back to CPU numpy SGM |
| `inferenced` | ONNX initialized; RKNN/ACL/Hailo skipped |
| `v4l2d` | Blocked in sim launch script; simulator bridge provides camera feed |

---

## Inference backend priority

```
inference_backend() selection:
  1. RKNN NPU  (ARM hardware only; skipped when _use_mock=True)
  2. ONNX Runtime  (x86 dev PC — loads from models/onnx/)
  3. Mock RKNN  (last resort — random outputs, framework smoke only)

Override: EOP_BACKEND=onnx  forces ONNX on any platform
```

---

## Testing on Dev PC

### The Cython `.so` problem

EOP commits **ARM aarch64** `.so` files (e.g. `common/params_pyx.so`) for RK3588 deployment. These cannot load on x86_64 dev PC:

```
ImportError: .../params_pyx.so: cannot open shared object file: No such file or directory
```

This affects **any test that imports `conftest.py`** (which pulls in `common.params` → `params_pyx`).

### What works without rebuilding

Tests that don't touch `common.params` or cereal messaging:

```bash
# InferenceD HAL tests — pure Python, no Cython deps
python3 -m pytest system/inferenced/tests/test_hal.py -v
python3 -m pytest system/inferenced/tests/test_performance.py -v
python3 -m pytest system/inferenced/tests/test_ipc_communication.py -v

# Daemon integration tests
python3 -m pytest selfdrive/gridd/test_gridd_integration.py -v
python3 -m pytest selfdrive/recordd/test_recordd_integration.py -v
```

### Running with stub params (bypass conftest)

For tests that need params but you don't want to rebuild:

```bash
# Skip the root conftest.py
python3 -m pytest selfdrive/controls/tests/test_long_mpc_personality.py \
    --confcutdir=selfdrive/controls/tests/ -v
```

### Full test suite: temporary x86_64 rebuild

To run tests that require `params_pyx`, `msgq`, etc.:

```bash
# 1. Save ARM .so files (don't lose them!)
mkdir -p /tmp/arm_so_backup
find . -name "*.so" -not -path "./.venv/*" -not -path "./third_party/*" \
    | xargs -I{} cp {} /tmp/arm_so_backup/

# 2. Build x86_64 versions
# Requires: scons, python3-dev, g++
python3 -m SCons -j$(nproc)

# 3. Run tests
python3 -m pytest selfdrive/modeld/ system/manager/ -v --tb=short

# 4. Restore ARM originals before committing!
git checkout -- "*.so"
# Or restore from backup:
# cp /tmp/arm_so_backup/*.so common/
```

### Docker (upstream approach)

Upstream openpilot tests in Ubuntu 24.04 Docker where `scons` rebuilds all `.so` for x86_64:

```bash
# Build Docker image with all deps
docker build -f Dockerfile.openpilot_base -t openpilot-base .

# Run tests inside container
docker run --rm -v $PWD:/tmp/openpilot -w /tmp/openpilot \
    openpilot-base /bin/bash -c "scons -j$(nproc) && pytest selfdrive/ -v"
```

**Trade-off**: Docker is clean but slow. Direct rebuild is faster for iterative dev.

### ARM-only tests (skip on dev PC)

| Test | Why skipped on dev PC |
|------|----------------------|
| `test_modeld_integration.py` (RKNN path) | RKNNLite is ARM-only |
| `test_stereod_integration.py` (ACL path) | `libarm_compute.so` not on x86_64 |
| `test_ipc_communication.py` (shm/ACL) | cereal shared memory + ACL libs missing |
| Any test importing `common.params` | `params_pyx.so` is ARM aarch64 |

---

## Note on model formats

| Format | Runtime | Used by |
|---|---|---|
| `.rknn` | RKNNLite (ARM only) | Production deployment |
| `.onnx` | ONNX Runtime | Dev PC + CARLA testing |
| `.hef` | HailRT (Hailo-8) | Optional Hailo NPU |
| `.pkl` (tinygrad) | tinygrad | **Not used** — EOP does not use tinygrad |

---

**Last updated:** 2026-05-30
