# GPU Parallel Architecture

This document explains how the GPU/Compute resources are shared between different daemons in VisionPilot.

## Overview

VisionPilot uses a **multi-process architecture** where different daemons run concurrently and share GPU/compute resources efficiently.

```
┌─────────────────────────────────────────────────────────────────┐
│                             RK3588                              │
│                         (Mali G610 GPU)                         │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │   modeld    │  │   stereod   │  │     ui      │             │
│  │  (NPU+GPU)  │  │   (GPU)     │  │   (GPU)     │             │
│  │             │  │             │  │             │             │
│  │ • RKNN NPU  │  │ • ACL SGM   │  │ • OpenGL    │             │
│  │ • OpenCL    │  │   (OpenCL)  │  │ • Camera    │
│  │   transform │  │             │  │   rendering │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│         │                │                │                     │
│         └────────────────┴────────────────┘                     │
│                          │                                      │
│                    ┌─────────┐                                  │
│                    │  GPU    │                                  │
│                    │ Driver  │                                  │
│                    └─────────┘                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Process Architecture

### 1. modeld (Neural Network Inference)
**Hardware**: NPU Core 0 + GPU (OpenCL)

```python
# Pipeline
VisionIPC (YUV) → OpenCL transform → RKNN inference → modelV2 msg
```

- **NPU**: Runs driving_vision.rknn + driving_policy.rknn
- **GPU**: Preprocessing (YUV → planar, affine transforms via OpenCL)
- **Frequency**: 20Hz
- **Latency**: ~15ms (NPU) + ~5ms (GPU preprocessing)

### 2. stereod (Stereo Depth)
**Hardware**: GPU (OpenCL via ACL)

```python
# Pipeline
VisionIPC (stereo_left/right) → ACL SGM → disparity map → stereoDepth msg
```

- **ACL GPU**: SGM cost computation, aggregation, WTA
- **ACL CPU**: Census transform (fallback if GPU busy)
- **Frequency**: 20Hz
- **Latency**: ~30ms (GPU) / ~100ms (CPU fallback)

### 3. ui (User Interface)
**Hardware**: GPU (OpenGL/GLES)

```python
# Pipeline
VisionIPC → OpenGL texture → shader → display
```

- **OpenGL**: Camera view rendering, overlays
- **OpenCL**: Not used directly (EGL interop possible)
- **Frequency**: 60Hz (display refresh)
- **Latency**: ~16ms per frame

## Resource Sharing

### Mali GPU Architecture

The Mali G610/G52 uses a **unified shader core** design:

```
┌─────────────────────────────────────┐
│         Mali G610 GPU               │
├─────────────────────────────────────┤
│  ┌─────────┐ ┌─────────┐ ┌───────┐ │
│  │ Shader  │ │ Shader  │ │ Shader│ │ ... (up to 10 cores on G610)
│  │ Core 0  │ │ Core 1  │ │ Core 2│ │
│  │         │ │         │ │       │ │
│  │• OpenCL │ │• OpenCL │ │• GL   │ │
│  │• OpenGL │ │• OpenGL │ │• GL   │ │
│  └─────────┘ └─────────┘ └───────┘ │
│                                     │
│  ┌─────────────────────────────┐    │
│  │    Shared L2 Cache          │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
```

### Concurrent Execution

Multiple processes can use the GPU simultaneously:

1. **OpenCL kernels** from modeld (transforms) and stereod (SGM)
2. **OpenGL shaders** from ui (rendering)
3. **NPU** runs independently (separate hardware)

### Synchronization

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  modeld  │────→│  msgq    │←────│    ui    │
│  (20Hz)  │     │ (shared) │     │  (60Hz)  │
└──────────┘     └──────────┘     └──────────┘
      │                               │
      │         ┌──────────┐          │
      └────────→│  GPU     │←─────────┘
                │  Driver  │
                │ (queues) │
                └──────────┘
```

- **No explicit locking** - GPU driver handles queueing
- **Async execution** - CPU submits work, GPU processes in parallel
- **Backpressure** - If GPU overloaded, frame drops occur

## ACL Backend Selection

The unified ACL backend automatically selects GPU vs CPU:

```python
# system/inferenced/arm/acl.py

class ACLCPUBackend (unified removed):
    def select_backend(self, config):
        # Selection logic:
        if config.priority == "power":
            return cpu_backend  # Save power
        
        if not gpu_backend.is_available():
            return cpu_backend  # Fallback
        
        if total_size < config.gpu_min_size:
            return cpu_backend  # Small ops → CPU
        
        if total_size > config.cpu_max_size:
            return gpu_backend  # Large ops → GPU
        
        return gpu_backend  # Default: GPU
```

### SGM Backend Selection

```python
# selfdrive/stereod/sgm.py

sgm = SGM(config, target="auto")  # Auto-select
# or
sgm = SGM(config, target="gpu")   # Force GPU
# or  
sgm = SGM(config, target="cpu")   # Force CPU
```

## Performance Considerations

### GPU Load Balancing

| Scenario | GPU Usage | Action |
|----------|-----------|--------|
| modeld + ui only | ~30% | Normal |
| modeld + stereod + ui | ~70% | Normal |
| All + heavy load | >90% | May drop frames |

### Frame Drop Policy

```python
# stereod.py
if result.inference_time_ms > MAX_SGM_LATENCY_MS:
    cloudlog.warning(f"SGM timeout: {result.inference_time_ms:.1f}ms")
    # Continue with best effort, don't block

# modeld.py
if vipc_dropped_frames > 0:
    prepare_only = True  # Skip inference, catch up
```

### Priority

1. **modeld** (highest) - Safety-critical driving model
2. **ui** (high) - User experience, must be responsive
3. **stereod** (medium) - Enhances perception, can drop frames

## Debugging GPU Usage

```bash
# Check GPU utilization
sudo cat /sys/class/misc/mali0/device/clk

# Check NPU utilization
sudo cat /sys/kernel/debug/rknpu/load

# Monitor processes
top -p $(pgrep -d',' modeld|stereod|ui)

# GPU frequency scaling
cat /sys/class/misc/mali0/device/clock
```

## Configuration

### Force CPU for SGM (debugging)
```python
# In params
params.put_bool("EOPStereoForceCPU", True)
```

### Disable stereo (if GPU overloaded)
```python
params.put_bool("EOPStereoEnabled", False)
```

### GPU Governor
```bash
# Performance mode (higher power)
echo performance > /sys/class/misc/mali0/device/governor

# Conservative mode (save power)
echo conservative > /sys/class/misc/mali0/device/governor
```

## Summary

- **Multi-process**: Each daemon runs independently
- **GPU sharing**: Mali driver handles concurrent OpenCL/OpenGL
- **Auto-selection**: ACL chooses GPU/CPU based on workload
- **Graceful degradation**: Frame drops instead of blocking
- **NPU independent**: RKNN runs on separate NPU hardware
