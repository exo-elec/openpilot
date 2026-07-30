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
| **Core 1** | PP-LiteSeg + YOLO (stereod) | 1.5 | Stereo pipeline |
| **Core 2** | policy + YOLO (monod) | 1.05 | Policy priority scheduling |

**Total: 4.55 / 6.0 TOPS (76% utilized)**

## Modules

| Module | Purpose | Input | Output |
|--------|---------|-------|--------|
| `gridd.py` | Main daemon orchestration | stereoDepth, monoDetections, modelV2 | `gridObjects` message |
| `pp_liteseg.py` | Road segmentation (NPU Core 1) | stereo frames | Road mask (19-class Cityscapes) |
| `lazy_bev.py` | BEV grid fusion | XYZ + detections | Occupancy grid |
| `multi_camera_fusion.py` | Camera geometry fusion | Multi-cam detections | Unified coordinates |

## PP-LiteSeg (NPU Core 1)

PP-LiteSeg replaces both SceneSeg and road segmentation:
- **19-class Cityscapes output** (road, sidewalk, person, car, etc.)
- **0.5 TOPS** @ 320×320 (was 1.5 TOPS with SceneSeg + PP-LiteSeg)
- **Saves 1.0 TOPS** for other models (AutoSpeed, Scene3D)

Road classes extracted: 0 (road), 1 (sidewalk), 3 (building edge)

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

# PP-LiteSeg parameters
PPLITESEG_INPUT_SIZE = (320, 320)
PPLITESEG_CORE = "1"  # Runs on Core 1
PPLITESEG_FREQUENCY = 20  # Hz

# ROAD_CLASS_IDS for drivable area extraction
ROAD_CLASS_IDS = [0, 1]  # road + sidewalk
```

## Performance

Target: 20 Hz on RK3588 A76 big cores

| Component | Time | Location |
|-----------|------|----------|
| SGBM depth | ~15ms | stereod (CPU) |
| PP-LiteSeg @ 320×320 | ~12ms | stereod (NPU Core 1) |
| BEV fusion | ~5ms | gridd (CPU) |

**Total latency**: ~32ms per frame (meets 20Hz budget)

## Changes from Legacy

**Removed (Commit 597b43d42):**
- SceneSeg (3-class segmentation) - replaced by PP-LiteSeg (19-class)
- **Saved 1.0 TOPS** on NPU Core 1

**Current Pipeline:**
```
stereod: SGBM + YOLO + PP-LiteSeg (NPU Core 1)
  ↓
gridd: LazyBEV fusion (CPU)
  ↓  
pathd: Path planning
```

## See Also

- `selfdrive/stereod/` - Stereo depth and segmentation
- `selfdrive/monod/` - Multi-camera YOLO
- `selfdrive/pathd/` - Path planning, consumes grid
- `selfdrive/modeld/` - Driving model
