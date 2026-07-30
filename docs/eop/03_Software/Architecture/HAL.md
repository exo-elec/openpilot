# Hardware Abstraction Layer (HAL) - Clean Architecture

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete - Clean Architecture |
| **Code** | `system/hardware/compute.py` *(not implemented)* |
| **Backends** | RKNN, ACL (NEON/OpenCL), RGA, MPP, Hailo |

---

## 1. Objective

Provide a **unified, direct-access** hardware abstraction layer for compute acceleration (NPU, GPU, RGA, MPP) without intermediate daemons.

**Key Principle**: Daemons use HAL directly via library calls, not via msgq services.

---

## 2. Architecture

### 2.1 Clean Separation

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ALGORITHM DAEMONS (selfdrive/)                                         │
│  - modeld, stereod, gridd, monod, recordd                               │
│  - Decide WHAT to compute                                               │
│  - Use HAL directly: from openpilot.system.hardware import get_hal      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼ Library call (no msgq)
┌─────────────────────────────────────────────────────────────────────────┐
│  COMPUTE HAL (system/hardware/compute.py)                               │
│  - Decides HOW to compute (backend selection)                           │
│  - Single file: base + HAL + scheduler + selector                       │
│  - No daemon, no process, pure library                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼ Library call
┌─────────────────────────────────────────────────────────────────────────┐
│  BACKENDS (system/hardware/rockchip/, arm/, hailo/)                     │
│  - Low-level hardware wrappers                                          │
│  - RKNN, ACL (NEON/OpenCL), RGA, MPP, Hailo                             │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Message Flow (Correct)

```
┌─────────────┐     VisionIPC      ┌─────────────┐
│   v4l2d     │───────────────────▶│   modeld    │
│  (capture)  │                    │  (inference)│
└─────────────┘                    └──────┬──────┘
                                          │ Library call
                                          ▼
                                    ┌─────────────┐
                                    │  HAL (NPU)  │
                                    └─────────────┘
                                          │
                                          ▼
                                    ┌─────────────┐
                                    │  modelV2    │
                                    └──────┬──────┘
                                           │ msgq
                                           ▼
                                    ┌─────────────┐
                                    │  controlsd  │
                                    └─────────────┘
```

**NOT this** (old wrong approach):
```
# WRONG - Don't use msgq for HAL
v4l2d ──▶ inferenced ──▶ modeld   ❌ Removed
```

---

## 3. API Usage

### 3.1 Direct HAL Access

```python
from openpilot.system.hardware import get_hal, BackendType

# Initialize HAL (singleton)
hal = get_hal()

# Get specific backend
npu = hal.get_backend(BackendType.NPU)
result = npu.infer('vision_model', {'input': image})
```

### 3.2 Auto-Select Backend

```python
from openpilot.system.hardware import select_for_sgm, select_for_gemm

# SGM: GPU for large images, CPU for small
backend = select_for_sgm(width=1920, height=1080)
result = backend.infer('sgm', {'left': left, 'right': right})

# GEMM: GPU for large matrices, CPU for small
backend = select_for_gemm(matrix_size=512)
result = backend.infer('gemm', {'A': A, 'B': B})
```

### 3.3 Backend Selection Logic

| Operation | Condition | Selected Backend |
|-----------|-----------|------------------|
| SGM Stereo | Image > 640px | GPU (OpenCL) |
| SGM Stereo | Image ≤ 640px | CPU (NEON) |
| GEMM | Matrix > 256×256 | GPU (OpenCL) |
| GEMM | Matrix ≤ 256×256 | CPU (NEON) |
| Resize/Crop | Always | RGA (dedicated HW) |
| Video Codec | Always | MPP (dedicated HW) |
| NN (INT8) | Always | NPU (RKNN) |
| NN (FP32) | Always | GPU (OpenCL) |

---

## 4. File Structure

```
system/hardware/
├── __init__.py              # Exports: HARDWARE, HAL, get_hal, selectors
├── base.py                  # System hardware (platform, power, thermal)
├── compute.py               # Unified compute HAL (single file)
│   ├── BackendType          # Enum: NPU, GPU, CPU, RGA, MPP, HAILO
│   ├── HardwareBackend      # Base class for all backends
│   ├── HAL                  # Main HAL class (singleton)
│   ├── BackendSelector      # Auto-select backend for operation
│   └── select_for_*         # Convenience functions
├── registry.py              # Platform auto-detection
├── rk3588/
│   └── hardware.py          # RK3588 platform hardware
├── rockchip/                # Rockchip compute backends
│   ├── rknn.py              # Low-level RKNN wrapper
│   ├── rknn_backend.py      # HAL backend
│   ├── rga.py               # Low-level RGA wrapper
│   ├── rga_backend.py       # HAL backend
│   ├── mpp.py               # Low-level MPP wrapper
│   └── mpp_backend.py       # HAL backend
├── arm/
│   └── acl.py               # ACL backends (NEON + OpenCL)
└── hailo/
    └── hailo.py             # Hailo backend
```

---

## 5. Backend Responsibilities

| Backend | Class | Responsibility |
|---------|-------|----------------|
| NPU | `RKNNBackend` | Quantized neural network inference |
| GPU | `ACLCPUBackend (GPU placeholder removed)` | OpenCL parallel compute |
| CPU | `ACLCPUBackend` | NEON optimized compute |
| RGA | `RGABackend` | 2D resize/crop/format |
| MPP | `MPPBackend` | Video encode/decode |
| Hailo | `HailoBackend` | Alternative NPU |

---

## 6. Removed Components

| Component | Reason | Replacement |
|-----------|--------|-------------|
| `selfdrive/inferenced/` | Unnecessary daemon | Direct HAL usage |
| `common/hardware/` | Duplicated structure | Merged to `system/hardware/` |
| Multiple compute files | Too fragmented | Single `compute.py` |
| `InferenceClient` | Unnecessary wrapper | Direct `get_hal()` |
| `InferenceScheduler` | Unnecessary complexity | Optional, use directly if needed |

---

## 7. Integration Examples

### modeld - Neural Network

```python
from openpilot.system.hardware import get_hal, BackendType

class ModelD:
    def __init__(self):
        self.hal = get_hal()
        self.npu = self.hal.get_backend(BackendType.NPU)
        
    def run(self, image):
        result = self.npu.infer('driving_vision', {'input': image})
        return result.outputs['output']
```

### stereod - SGM Depth

```python
from openpilot.system.hardware import select_for_sgm

class StereoD:
    def __init__(self):
        # Auto-select based on image size
        self.backend = select_for_sgm(width=1920, height=1080)
        
    def compute(self, left, right):
        result = self.backend.infer('sgm', {
            'left': left, 
            'right': right
        })
        return result.outputs['disparity']
```

### v4l2d - Image Preprocessing

```python
from openpilot.system.hardware import get_hal, BackendType

class V4L2D:
    def __init__(self):
        self.rga = get_hal().get_backend(BackendType.RGA)
        
    def preprocess(self, frame):
        result = self.rga.infer('resize', {
            'image': frame,
            'dst_width': 640,
            'dst_height': 480
        })
        return result.outputs['output']
```

---

## 8. Migration Guide

### From Old InferenceClient

```python
# OLD - Removed
from openpilot.selfdrive.inferenced.client import InferenceClient
client = InferenceClient()
result = client.infer_npu('model', inputs)

# NEW - Direct HAL
from openpilot.system.hardware import get_hal, BackendType
npu = get_hal().get_backend(BackendType.NPU)
result = npu.infer('model', inputs)
```

### From Old Backend Import

```python
# OLD - Removed
from openpilot.selfdrive.inferenced.backends import RKNNBackend

# NEW - From HAL
from openpilot.system.hardware.rockchip import RKNNBackend
```

---

## 9. Testing

```python
# Test HAL initialization
from openpilot.system.hardware import get_hal

hal = get_hal()
print(f"Available backends: {hal.get_available_backends()}")

# Test specific backend
npu = hal.get_backend(BackendType.NPU)
print(f"NPU available: {npu.is_available()}")
```

---

## 10. See Also

- Compute HAL Implementation
- Rockchip Backends
- V4L2D - Camera daemon using HAL
- MODELD - Model daemon using HAL
- STEREOD - Stereo daemon using HAL
