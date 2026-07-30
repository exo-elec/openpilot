# StereoD - Stereo Depth Daemon

GPU/CPU stereo depth estimation using ACL-based SGM (Semi-Global Matching).

## Overview

StereoD provides real-time stereo depth computation:
- **SGM Algorithm**: Semi-Global Matching via ARM Compute Library (ACL)
- **Hardware**: Mali G610 (RK3588) with CPU fallback
- **Performance**: ~30ms for 640x480 disparity at 20Hz (GPU), ~100ms (CPU)
- **Fault Policy**: GPU failure → IMMEDIATE_DISABLE (no CPU fallback in production)

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  stereo_left│────→│             │     │  stereoDepth│
│  (VisionIPC)│     │   StereoD   │────→│  (disparity)│
├─────────────┤     │             │     ├─────────────┤
│ stereo_right│────→│  ┌───────┐  │     │stereoDetections│
│  (VisionIPC)│     │  │GPU SGM│  │     │  (2D YOLO)  │
└─────────────┘     │  │(OpenCL)│  │     ├─────────────┤
                    │  └───────┘  │     │stereoSegments│
                    │             │     │  (PP-LiteSeg)│
                    └─────────────┘     └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ stereoStatus│
                    │  (health)   │
                    └─────────────┘
```

## Files

| File | Description |
|------|-------------|
| `stereod.py` | Main daemon with fault handling and performance monitoring |
| `sgm.py` | Unified SGM implementation (ACL OpenCL/NEON) |
| `test_sgm.py` | Unit tests for SGM implementations |
| `__init__.py` | Package exports |

## SGM Algorithm

The Semi-Global Matching implementation includes:

1. **Census Transform**: 5x5 window, 24-bit descriptor per pixel
2. **Cost Volume**: Hamming distance between census descriptors
3. **Aggregation**: 4-path or simplified 2-path aggregation
4. **WTA**: Winner-takes-all with sub-pixel refinement
5. **Post-processing**: 3x3 median filter

### ACL Operations

The SGM implementation uses ARM Compute Library operations:
- `NEGEMM` / `CLGEMM`: Matrix operations for cost computation
- `NEConvolutionLayer` / `CLConvolutionLayer`: Feature extraction
- `NEActivationLayer` / `CLActivationLayer`: ReLU activations
- CPU fallback via NEON when GPU unavailable

## Usage

```python
from openpilot.selfdrive.stereod import StereoD

# Run daemon
stereo = StereoD()
stereo.run()
```

### Direct SGM Usage

```python
from openpilot.selfdrive.stereod.sgm import SGM, SGMConfig

# Configure SGM
config = SGMConfig(
    target_width=640,
    target_height=480,
    max_disparity=64,
    p1=10,
    p2=120
)

# Compute disparity (auto GPU/CPU selection)
with SGM(config, target="auto") as sgm:
    result = sgm.compute(left_image, right_image)
    disparity = result.disparity
    confidence = result.confidence
```

## Inputs

- `stereo_left` (VisionIPC stream 4) - Left camera NV12
- `stereo_right` (VisionIPC stream 5) - Right camera NV12

## Outputs

- `stereoDepth` - Disparity map (float32), confidence map
- `stereoDetections` - 2D YOLO detections
- `stereoSegments` - PP-LiteSeg segmentation masks
- `stereoStatus` - System health and performance metrics

## Configuration

```python
# Enable stereo pipeline
params.put_bool("EOPStereoEnabled", True)

# SGM parameters (in sgm.py)
SGMConfig(
    target_width=640,    # Image width
    target_height=480,   # Image height
    max_disparity=64,    # Disparity range
    p1=10,               # Small penalty
    p2=120,              # Large penalty
    use_8_path=False,    # 4-path aggregation
    enable_median_filter=True,
    max_runtime_ms=50.0  # Timeout
)
```

## Hardware Support

| Platform | GPU | Baseline | Resolution | Latency |
|----------|-----|----------|------------|---------|
| RK3588 | Mali G610 | 80mm | 640x480 | ~30ms |

## Fault Handling

**Critical**: StereoD is safety-critical for ADAS.

- **GPU SGM failure** → `IMMEDIATE_DISABLE` (3 consecutive failures)
- **No CPU fallback** in production (too slow, ~500ms)
- **YOLO/PP-LiteSeg failure** → Continue without them (non-critical)
- **Safe stop** rather than degraded performance

### Fault States

| Fault Reason | Description | Recovery |
|--------------|-------------|----------|
| `gpu_unavailable` | ACL/GPU not found | No (hardware) |
| `sgm_consecutive_failures` | 3+ SGM failures | No (safety) |
| `sgm_timeout` | SGM exceeded 50ms | No (real-time) |

## Performance Monitoring

StereoD tracks and logs:
- Frame count and drop rate
- SGM latency (avg, max, stddev)
- Consecutive failures
- GPU utilization (if available)

Stats logged every 10 seconds:
```
Performance: frames=200, dropped=0, sgm_avg=32.5ms, sgm_max=38.2ms, fault=False
```

## Testing

```bash
# Run unit tests
python -m unittest selfdrive.stereod.test_sgm -v

# Compare GPU vs CPU
python -c "
from openpilot.selfdrive.stereod.sgm import compare_gpu_cpu  # If available
import numpy as np
left = np.random.randint(0, 256, (240, 320), dtype=np.uint8)
right = np.roll(left, 10, axis=1)
print(compare_gpu_cpu(left, right))
"
```

## Dependencies

- `arm_compute` - ARM Compute Library (ACL) via submodule
- `numpy` - Array operations
- `opencv-python` - Image processing
- `cereal` - Messaging
- `msgq` - VisionIPC

## Status

| Feature | Status |
|---------|--------|
| GPU SGM (ACL OpenCL) | ✅ Ready |
| CPU SGM (ACL NEON) | ✅ Ready |
| Fault Detection | ✅ Ready |
| Performance Monitoring | ✅ Ready |
| 80mm Baseline (RK3588) | ✅ Ready |
| Unit Tests | ✅ Ready |
