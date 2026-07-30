# ExoPilot Platform Constraints

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |

---


## Hardware Architecture (SoC - No PCIe)

```
┌─────────────────────────────────────────────────────────┐
│                       RK3588 SoC                        │
├─────────────────────────────────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐    │
│  │ 4×A76   │  │ 4×A55   │  │  NPU   │  │ Mali    │    │
│  │ (Big)   │  │ (Little)│  │ 6 TOPS │  │  GPU    │    │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘    │
│       │            │            │            │          │
│       └────────────┴────────────┴────────────┘          │
│                    Shared Memory                        │
│                      (8-16 GB)                          │
└─────────────────────────────────────────────────────────┘
                          │
                    No PCIe Slot
                          │
              ❌ No discrete GPU (NVIDIA)
              ❌ No PCIe accelerators
              ✅ Everything integrated on SoC
```

## Critical Constraint: NO CPU FALLBACK

### CPU Allocation (100% utilized)

| Core | Processes | Load |
|------|-----------|------|
| A76-0 | `controlsd` (100Hz) + `modeld` | ~80% |
| A76-1 | `gridd` (20Hz) + `pathd` | ~70% |
| A76-2 | `v4l2d` + `imud` + `socketd` | ~60% |
| A76-3 | System tasks + spare | ~50% |
| A55-0-3 | `waked`, `voiced`, `mapd`, UI | ~40% |

**Result:** No spare CPU cycles for GPU/NPU fallback.

### Why CPU Fallback is Impossible

| Operation | GPU/NPU | CPU (Estimated) | Deadline | CPU Feasible? |
|-----------|---------|-----------------|----------|---------------|
| SGM Stereo | 30ms (Mali) | ~200ms | 50ms | ❌ No |
| YOLO inference | 15ms (NPU) | ~150ms | 50ms | ❌ No |
| PP-LiteSeg | 20ms (NPU) | ~200ms | 50ms | ❌ No |
| 3D Reprojection | 10ms (GPU) | ~80ms | 50ms | ❌ No |

**Math:** CPU fallback = 4× deadline violation = unsafe

## Hardware Accelerators (SoC Integrated Only)

### 1. NPU (Neural Processing Unit)

**Specs:**
- RK3588: 6 TOPS total (3 cores × 2 TOPS)
- Precision: INT8/FP16
- API: RKNN Toolkit

**Usage:**
```python
# NPU via RKNN (not TensorRT - no NVIDIA!)
from openpilot.selfdrive.modeld.runners.rknn_runner import RKNNRunner

model = RKNNRunner("/data/models/yolo.rknn", npu_core=1)
output = model.run(input_image)
```

### 2. GPU (Mali - OpenCL, not CUDA!)

**Specs:**
- RK3588: Mali-G610 MP4
- API: OpenCL 2.1
- ❌ No CUDA (no NVIDIA GPU)
- ❌ No Tensor Cores

**Usage:**
```python
# OpenCL on Mali GPU (not CUDA!)
from openpilot.selfdrive.inferenced.backends.gpu_backend import GPUBackend

backend = GPUBackend()  # OpenCL context on Mali
result = backend.infer('sgm', {'left': left, 'right': right})
```

### 3. RGA (2D Graphics Accelerator)

**Specs:**
- RK3588: RGA3
- Operations: crop, resize, format convert, rotate
- API: librga

**Usage:**
```python
from openpilot.selfdrive.inferenced.backends.rga_backend import RGABackend

rga = RGABackend()  # Hardware 2D accelerator
result = rga.infer('convert', {
    'image': nv12_data,
    'operation': 'convert',
    'src_format': 'nv12',
    'dst_format': 'rgb8'
})
```

## Fault Policy Summary

### Critical Path (stereod → gridd → controlsd)

| Component | Hardware | Fault Action | Rationale |
|-----------|----------|--------------|-----------|
| SGM Stereo | Mali GPU | `IMMEDIATE_DISABLE` | No CPU fallback possible |
| Model inference | NPU Core 0 | `IMMEDIATE_DISABLE` | Core driving model |
| Lazy BEV | CPU A76 | `IMMEDIATE_DISABLE` | Required for driving |

### Enhancement Path (pointcloudd → surfaced)

| Component | Hardware | Fault Action | Rationale |
|-----------|----------|--------------|-----------|
| 3D Reconstruction | Mali GPU | Skip frame / Retry | Non-critical |
| Semantic filter | CPU | Continue degraded | Can disable |
| SQSC lookup | CPU (eMMC) | Skip enhancement | Gridd continues |

## Common Misconceptions

### ❌ "We can fall back to CPU if GPU fails"
**Reality:** CPU is fully loaded. Fallback = deadline miss = unsafe.

### ❌ "We can use CUDA"
**Reality:** Mali GPU uses OpenCL, not CUDA. No NVIDIA hardware.

### ❌ "We can add a PCIe GPU"
**Reality:** ExoPilot hardware has no PCIe slot. SoC only.

### ❌ "CPU has 8 cores, we can use some"
**Reality:** 
- 4×A76: Already allocated to critical tasks
- 4×A55: Too slow for computer vision (in-order, low cache)
- All cores needed for real-time control

## Development Guidelines

### 1. Always Use Hardware Acceleration
```python
# ✅ Good: Use NPU
yolo_result = npu_backend.infer('yolo', image)

# ❌ Bad: CPU fallback (too slow)
# yolo_result = yolo_cpu_model(image)  # NEVER DO THIS
```

### 2. Handle Hardware Init Failure
```python
def initialize(self):
    if not self.gpu_backend.initialize():
        if self.is_critical:
            self.fault("gpu_init_failed")  # Stop safely
        else:
            self.disable_feature()  # Continue without
```

### 3. Never Assume CPU is Available
```python
# ❌ Bad: Implicit CPU fallback
result = gpu_op() or cpu_op()  # Don't do this

# ✅ Good: Explicit fault on failure
result = gpu_op()
if result is None:
    self.fault("gpu_failed")  # Safe stop
```

## Testing on PC

When developing on PC (x86 + NVIDIA), remember:

| Feature | PC (Development) | Target (ExoPilot) |
|---------|------------------|-------------------|
| GPU API | CUDA | OpenCL |
| NPU API | TensorRT | RKNN |
| 2D Accel | None (CPU) | RGA |
| CPU | Intel/AMD (fast) | ARM A76/A55 |

**Action:** Always test on actual ExoPilot hardware before deployment.

## Summary

```
┌─────────────────────────────────────────────────────────┐
│                    Golden Rules                         │
├─────────────────────────────────────────────────────────┤
│ 1. SoC only - No PCIe, no discrete GPU                  │
│ 2. No CPU fallback - 100% allocated                     │
│ 3. OpenCL on Mali - No CUDA                             │
│ 4. RKNN on NPU - No TensorRT                            │
│ 5. Fault on failure - Safe stop, not degradation        │
└─────────────────────────────────────────────────────────┘
```
