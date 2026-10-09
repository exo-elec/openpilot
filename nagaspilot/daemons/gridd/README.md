# `selfdrive/gridd`

GridD — Perception Fusion Daemon (Vision Layer)

## Overview

GridD fuses multi-source perception data into a BEV (Bird's Eye View) occupancy grid for path planning.

**Input Sources:**
- `stereoDepth` (from stereod): XYZ point cloud from SGBM stereo
- `monoDetections` (from monod): YOLO detections from road/wide cameras (RKNN NPU)
- `modelV2` (from modeld): Path prediction and lead detection
- `sideDetections` (from sided): Advisory BEV objects from side cameras (**not fused into main grid**)

**Output:**
- `gridObjects` (20 Hz): BEV occupancy grid
- `stereoGround`: Ground plane estimation for pathd
- `stereoObjects`: Fused detections (mono YOLO + modeld leads + stereo depth)

## Architecture

```
stereod ──[stereoDepth]────┐
monod ────[monoDetections]─┼──▶ gridd ──[gridObjects]──▶ pathd
modeld ───[modelV2/leads]──┤      ↑
sided ────[sideDetections]─┘ (advisory)
                              (HAL geometry-aware fusion)
```

## NPU Allocation (RK3588)

| Core | Models | TOPS | Priority |
|------|--------|------|----------|
| **Core 0** | driving_vision | 2.0 | CRITICAL - Exclusive |
| **Core 1** | YOLOv8 side/rear (sided, reard) | — | Detection |
| **Core 2** | policy + YOLOv8 road (monod) | — | Policy priority scheduling |

gridd runs no NPU model: its road segmentation comes from the camera-tier
card through inferenced. See `docs/eop/05_Features/CAMERA_ACCEL_TIER.md`.

## Modules

| Module | Purpose | Input | Output |
|--------|---------|-------|--------|
| `gridd.py` | Main daemon orchestration | stereoDepth, monoDetections, modelV2 | `gridObjects` message |
| `lazy_bev.py` | BEV grid fusion | XYZ + detections | Occupancy grid |
| `multi_camera_fusion.py` | Camera geometry fusion | Multi-cam detections | Unified coordinates |

## Road segmentation (camera-tier card)

The road mask is the card's drivable-area map (TwinLiteNet+ Large, `seg_road`, 10 Hz,
`segd/card_segmenter.py`): drivable area plus the lane lines on it.

No fallback: if the card is missing, failed or stale, gridd runs without a
road mask and sets `gridStatus.segmentationDegraded`. That is not a
`gridStatus.fault`, so openpilot stays engaged. PP-LiteSeg on RKNN, which used
to cover this, was removed on 2026-09-27.

## Object Type Classification

### YOLO-nano (monod on Core 2, stereod on Core 1)
- **Output**: Object types (car, truck, bike, person, bus)
- **Rate**: 20Hz
- **Fusion**: Detections fused with stereo depth in gridd

## Input

- **stereoDepth** (from stereod, 20Hz): XYZ point cloud
- **monoDetections** (from monod, 20Hz): YOLO classifications  
- **modelV2** (from modeld, 20Hz): Path prediction and leads

## Output

- **`gridObjects`** (20 Hz): BEV occupancy grid with:
  - Resolution and dimensions
  - Occupancy probability layer
  - Object type layer
  - Grid origin and yaw

## Configuration

```python
# BEV grid parameters (from lazy_bev.py)
RESOLUTION_M = 0.5  # 0.5m per cell
GRID_WIDTH = 40     # 20m lateral (±10m)
GRID_HEIGHT = 200   # 100m forward
DECAY_TIME_S = 0.5  # 500ms occupancy decay

# Card road segmentation (gridd.py)
CARD_SEG_EVERY_N = 2    # 10 Hz
CARD_MASK_MAX_AGE = 4   # frames; older = degraded

# ROAD_CLASS_IDS for drivable area extraction
ROAD_CLASS_IDS = [0, 1]  # road + sidewalk
```

## Performance

Target: 20 Hz on RK3588 A76 big cores

| Component | Time | Location |
|-----------|------|----------|
| SGBM depth | ~15ms | stereod (CPU) |
| Road segmentation | card | camera-tier card via inferenced |
| BEV fusion | ~5ms | gridd (CPU) |

**Total latency**: ~32ms per frame (meets 20Hz budget)

## Changes from Legacy

**Removed (2026-09-27):**
- SceneSeg and PP-LiteSeg on RKNN
- **Freed 1.0 TOPS** on NPU Core 1 for side/rear YOLO detection

**Current Pipeline:**
```
stereod: SGBM + YOLO (GPU + NPU Core 1)
segd: TwinLiteNet+ Large on camera-tier card
  ↓
gridd: Road mask + occupancy grid fusion (CPU)
  ↓  
pathd: Path planning
```

## See Also

- `selfdrive/stereod/` - Stereo depth and segmentation
- `selfdrive/monod/` - Multi-camera YOLO
- `selfdrive/pathd/` - Path planning, consumes grid
- `selfdrive/modeld/` - Driving model
