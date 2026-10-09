# Perception fusion: grids, costmap and object filtering

Design concept, 2026-09-28. Implemented on `dev/EOP10`: the grid and cost
layer (B1, B2), segd's `drivableBev` and demand-driven card feed (B3), gridd's
fusion (B4) and the advisory off-road object filter (B6). `task.md` tracks
the steps. Written after an audit of how every EOP model output is consumed
and a study of Autoware Universe's perception/planning packages, then fitted
to the models and hardware EOP actually has.

## 1. Why

The camera-tier card segments every camera, but almost none of it is used:
segd spends 18 of 20 card jobs/s on cameras with no consumer, and gridd's own
10 Hz road mask never reaches the costmap. The costmap itself does not reach
pathd intact (axis-swapped radar stamps, stamps never published, a cost-layer
dtype and shape pathd cannot decode). The user's decision (2026-09-28) is to use
the card output, not trim it. That needs the grid pipeline repaired and a design
that stays repaired.

## 2. Autoware as method reference, EOP names kept

Autoware Universe (`autoware_universe` main, read 2026-09-28) is where the
*methods* come from. **Names stay EOP's**: `LazyBEV`, `gridObjects`,
`drivableArea`, `FusionCostmap`, `BEVCostmap`, `OccupancyGridView`,
`CellState`. Code comments cite the Autoware source ("after Autoware X").

| Method | Autoware source | Where in EOP |
|---|---|---|
| One grid per source, fused separately | `probabilistic_occupancy_grid_map` | segd publishes one drivable BEV per camera; gridd fuses |
| Log-odds fusion weighted by per-source reliability | `synchronized_grid_map_fusion` (`fusion_method: log-odds`, `input_ogm_reliabilities`) | gridd drivable fusion |
| Unobserved cells decay back to unknown (`tau` 0.75 s) | `OccupancyGridMapLOBFUpdater` | gridd drivable fusion (this is the "expiry") |
| Costmap = max of separately built layers | `costmap_generator` (`points`, `objects`, ... `combined`) | `FusionCostmap`: stereo, radar, drivable |
| Drop/down-weight objects off the drivable area | `object_lanelet_filter`, `occupancy_grid_based_validator` | radar_zones, advisory (B6) |
| Radar adds velocity, camera adds class | `radar_fusion_to_detected_object` | `radar_zones._associate_camera_objects` (unchanged) |

Not adopted: ROS nodes, strict sensor sync, Lanelet2 maps, LiDAR stages
(ground segmentation etc.). Known gap, pre-existing: stereo ground points are
marked occupied.

## 3. Our models and hardware, and where each piece runs

| Model / sensor | Runs on | Output today | Role in this design |
|---|---|---|---|
| Driving model (Bukapilot KA2 split / Chestnut on eGPU) | RKNN core 0 / eGPU | `modelV2` (device frame) | Lane lines, road edges, leads. Unchanged. |
| YOLOv8 per camera | RKNN cores 1-2 | `monoDetections`, `sideDetections`, `rearDetections` (+left) | Objects. Unchanged; validated by the drivable filter (B6). |
| TwinLiteNet+ Large, drivable + lane | Hailo-8 or DX-M1M | 540x960 OTHER/DRIVABLE/LANE per job | **Per-camera drivable BEV (B3)** -- the new use. |
| Stereo SGM | ACL (GPU/CPU) | `stereoDepth` | Obstacle points in gridd. Unchanged. |
| 77 GHz UART radar | -- | `radar3d` | Obstacle layer + ACC (radard). |
| BLE corner radars | -- | `radar2d` | Obstacle layer + BSD/RCTA/LCA (radar_zones). |

### Card budget: feed it what the drive needs

Not a round-robin. segd is the card's only client, at the 20 Hz foundation
rate with at most one job per tick (20 jobs/s), and it picks the camera by
what the drive needs right now (`segd/schedule.py`, pure policy, no cereal):

| context | wants (Hz) |
|---|---|
| always, going forward | road 10 |
| wide | 4 below 12 m/s (urban, junctions), else 2 |
| tele (02M) | 4 above 15 m/s (far road), else off |
| side camera | 6 while its blinker is on or a low-speed turn toward it; 4 while its blind-spot flag or a side-camera detection is on that side; else **off** |
| rear camera | 6 in reverse; 4 while a rear detection is present; else **off** |
| in reverse | road 2, wide off, rear first |

Demands are granted in priority order from the 20 jobs/s budget (reverse
rear, blinker side, road, object sides/rear, wide, tele), so a full
lane-change never starves the road camera. A credit scheduler
then runs the granted camera holding the most credit (each earns its Hz per
tick; exact long-run rates, never two jobs in a tick), and **skips the tick
when none has a full credit**: an idle car costs the card nothing (3 W part, -40..85 C).
A camera that is off is not stale evidence: its cells decay to unknown
(section 5) and nothing reads them, which is the honest state.

Why: side and rear maps only matter with something to check (a lane change,
reverse, an object to filter, B6); road and wide feed the cost layer that
pathd reads. Feeding every camera equally spends the card on views nobody
is using. Every camera at 20 Hz would also need inferenced results by job
id (today one job in flight per client) -- not needed for this schedule.

### CPU

Pixel-to-ground projection uses a per-camera lookup table (mask pixel -> grid
cell), rebuilt only when that camera's calibration changes. Each frame is then
one downsample plus a `bincount`: a few milliseconds per job on the A76 cores.

## 4. Contracts (EOP BEV convention)

Already what the `gridObjects` and `DrivableArea` schemas and surfaced use:

- **Frame:** x forward, y left (REP-103). modelV2 (y right) is converted at
  ingestion only, as radard does.
- **Layout:** array `(height, width)`, rows = forward, cols = lateral;
  `originX` = lateral origin (right edge), `originY` = forward origin (rear
  edge); cols increase to the left.
- **Two fixes to this convention:** `LazyBEV` cols follow stereo X (right)
  -- flip at ingestion; pathd `BEVCostmap.world_to_grid` swaps
  `origin_x`/`origin_y`.
- **Cost layer** (`gridObjects` layer `"cost"`, schema encoding `cost` =
  uint16): gridd's classes 0 lane, 10 road, 25 uncertain, 50 unknown,
  100 obstacle. pathd scores `cost / 100`, not by the grid's own max.
- **Safety cap:** segmentation alone gives at most 50, below pathd's
  obstacle thresholds (60/70). Only stereo and radar reach 100.
- **Per-camera drivable BEV** (segd -> gridd): same layout, uint8 values from
  the card classes: 0 unknown, 1 other, 2 drivable, 3 lane.

## 5. Fusion (gridd)

1. **Drivable fusion.** Each camera BEV is fused once, on arrival, as
   log-odds weighted by camera (road 1.0, wide/tele 0.8, side/rear 0.6).
   Unobserved cells decay to unknown (`tau` 0.75 s). The grid is shifted by
   ego motion (`livePose`).
2. **`FusionCostmap`** builds the cost layer: stereo occupancy and measured
   BLE radar footprints -> 100; else drivable/lane -> 10/0, low confidence
   -> 25, unknown or off-road -> 50. Radar stamps go into the array that is
   published (today: after the copy, x/y swapped).
3. **Degrade, don't fault.** No card: no drivable evidence, cost 50 there,
   `gridStatus.segmentationDegraded`; never `fault`.

## 6. Object filter (B6, advisory only)

`controls/lib/object_drivable_area_filter.py`, applied in radar_zones after
camera association and before zone partition. gridd publishes the fused
probability as gridObjects layer `drivable`; if fresh, known cells under an
object's footprint (class size; at least 60 % with evidence) average below
0.3, its confidence is halved. Never deleted; nothing on unknown cells, a
layer older than 0.5 s, or outside the grid; never AEB (radar-only); the
car's own blind-spot flag is untouched; BLE radar keeps kinematics.

## 7. Coverage

Decided 2026-09-28: `gridObjects` covers x -20..+55 m, y -15..+15 m at
0.5 m (`height` 150, `width` 60, `originX` -15, `originY` -20). pathd
reads forward cells only.

## 8. Ownership

| Concern | Owner |
|---|---|
| Card segmentation -> per-camera drivable BEV | segd |
| Drivable fusion, `FusionCostmap`, `gridObjects` | gridd |
| Reading `gridObjects` (`OccupancyGridView`), tracker | pathd |
| Camera <-> BLE association, BSD/RCTA/LCA, drivable filter | controlsd `radar_zones` |
| Camera calibration (tf tree) | exopilot `hal.calibration` via `system/hardware` |
| Card access | inferenced |

## 9. Naming (refactor)

Keep: `LazyBEV`, `FusionCostmap`, `gridObjects`, `drivableArea`,
`BEVCostmap`, `OccupancyGridView`, `CellState`, `CardSegmenter`.

| Change | Why |
|---|---|
| gridd layer `semantic_costmap` -> `cost` | pathd reads `cost` |
| drop `FusionCostmapGenerator` / `FusionCostmapConfig` aliases | one name each: `FusionCostmap`, `CostmapConfig` |
| delete `gridd/costmap.py` (`CostmapGenerator`) | unused; clashes with `FusionCostmap` |
| gridd `seg_mask` / `road_mask` -> `drivable_mask` | it is the card's drivable map |
| new `gridd/drivable_fusion.py` `DrivableFusion` | per-camera log-odds fusion |
| new service `drivableBev` (segd, one entry per camera) | matches `drivableArea` naming |
| remove gridd `occ_grid` (unused `OccupancyGrid`) | dead |
