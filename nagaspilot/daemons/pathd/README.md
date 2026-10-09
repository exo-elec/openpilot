# Pathd Module Overview

`selfdrive/pathd` hosts EnhancedOpenPilot's trajectory planning pipeline. The folder contains the main path daemon (`pathd.py`) with internal tracking and prediction modules.

## Architecture (Consolidated)

**Pipeline:**
```
v4l2d ──► gridd ──► pathd ──► enhancedTrajectory
(stereo)   (grid)    (track.py + predict.py internally)
                    └──► controlsd
```

## Core Components

| Component | Role | Key Inputs | Key Outputs | Integration Status |
|-----------|------|------------|-------------|-------------------|
| `pathd.py` (`StarD`) | Trajectory planning with safety filtering | `gridObjects`, `drivingModelData`, `carState` | `enhancedTrajectory` | ✅ Active |
| `track.py` | BEV grid cluster tracker (internal) | Occupancy grid from gridd | Tracked clusters with stable IDs | ✅ Integrated |
| `predict.py` | Constant-velocity trajectory projection (internal) | Tracked clusters | 3-second motion predictions | ✅ Integrated |
| `blindspot.py` | Lane change safety check | Tracked objects, predictions | `BlindspotCheckResult` | ⚠️ Not integrated |
| `lane_change.py` | Lane change gating logic | Car state, tracks | `LaneChangeGateResult` | ⚠️ Not integrated |
| `lateral_offset.py` | Lateral nudge for fast-closing objects | Tracks, ego velocity | `OffsetResult` | ⚠️ Not integrated |
| `path_corridor_fusion.py` | Multi-source boundary fusion | modelV2, ground_objects | Boundaries, confidence | ⚠️ Not integrated |

### Message Flow

```
v4l2d (VisionIPC STEREO_LEFT/RIGHT)
    │
    ▼
gridd ──► gridObjects (BEV occupancy grid, 20Hz)
    │
    ▼
pathd (StarD)
    ├─ Internal: track.py ──► Cluster tracking on BEV grid
    ├─ Internal: predict.py ──► 3s motion prediction
    ├─ Safety filter: Override policy trajectory if collision predicted
    │   ├─ Layer 1: Stereo boundaries (from stereoGround)
    │   ├─ Layer 2: Grid collision check
    │   └─ Layer 3: Lane line warning (fallback)
    │
    ▼
enhancedTrajectory ──► controlsd
    │
    ▼
Emergency braking clamp (critical: ≤-3.0 m/s²)
```

## Key Differences from Previous Architecture

1. **No external trackd**: Tracking now happens internally in `pathd` using `track.py` on the BEV grid from `gridd`
2. **No trackedObjects topic**: `pathd` consumes `gridObjects` directly, not `trackedObjects`
3. **No predictd**: Prediction is done internally via `predict.py` with constant-velocity model
4. **Simplified pipeline**: `stereod → gridd → pathd` instead of `stereod → trackd → pathd`

## Removed Components

| Component | Original | Replacement | Status |
|-----------|----------|-------------|--------|
| `trackd` daemon | `selfdrive/trackd/` | `pathd/track.py` (internal) | ✅ Deleted |
| `predictd` daemon | `selfdrive/trackd/` | `pathd/predict.py` (internal) | ✅ Absorbed |
| `groundd` daemon | Planned | N/A | ❌ Never implemented |

## Pathd Responsibilities

### Emergency Collision Avoidance (Longitudinal)
- Detects obstacles in BEV occupancy grid
- Multi-layer safety validation:
  1. Grid obstacle collision check
  2. Trajectory intersection detection
  3. Time-to-collision (TTC) computation
- Distance-based emergency braking override

### Trajectory Generation
- Base trajectory from `drivingModelData.action` (policy model)
  - `desiredCurvature` → lateral path
  - `desiredAcceleration` → speed profile
- Safety adjustments:
  - Speed reduction for grid obstacles
  - Emergency braking for critical TTC

## Configuration & Params

| Param | Purpose | Default |
|-------|---------|---------|
| `EPSOCEnabled` | Smart Offset Controller lateral bias | Disabled (handled by controlsd) |

## Safety Hierarchy

Pathd performs multi-layer validation (most to least critical):

1. **Grid obstacles** (`gridd`) - CRITICAL: BEV occupancy collision
2. **Trajectory intersection** (internal) - CRITICAL: Predicted path collision

### Trajectory Alert Levels

| Level | Trigger | Action |
|-------|---------|--------|
| Critical | Grid collision detected | Emergency braking |
| Warning | TTC < 3s | Speed reduction |
| None | Safe path | Policy trajectory |

## Implementation Notes

- **Internal tracking**: `ObjectTracker` class in `track.py` clusters BEV grid cells using connected components
- **Simple prediction**: `predict_clusters()` in `predict.py` uses constant-velocity 3-second horizon
- **No neural tracking**: Replaced SORT tracker with geometric clustering on occupancy grid
- **Policy-first**: Only overrides trajectory for safety-critical situations

## Known Issues & Solutions

### Issue 1: Lateral Offset Controller Not Integrated ⚠️

**Problem**: `lateral_offset.py` has a complete implementation but is never used in `pathd.py`.

**Location**: 
- `lateral_offset.py`: Complete `LateralOffsetController` class
- `pathd.py:1034`: `lateral_adjustments = [0.0] * num_points` (hardcoded)

**Solution** (apply to `pathd.py`):
```python
# In StarD.__init__:
from openpilot.selfdrive.pathd.lateral_offset import LateralOffsetController
self._lateral_offset_ctrl = LateralOffsetController()

# In StarD.update():
offset_result = self._lateral_offset_ctrl.compute(
    tracked_objects, ego_velocity, -1.8, 1.8)
lateral_adjustments = self._lateral_offset_ctrl.offsets_for_trajectory(
    offset_result.offset_m, path_x)
```

**Impact**: Enables subtle lateral nudges when fast-closing objects detected.

---

### Issue 2: Lane Change Safety Not Integrated ⚠️

**Problem**: `lane_change.py` and `blindspot.py` are implemented but never called.

**Location**:
- `lane_change.py`: `evaluate_lane_change_gate()` - not called
- `blindspot.py`: `check_lane_change_safety()` - only called from `lane_change.py`

**Solution** (apply to `pathd.py`):
```python
# In StarD.update() after turn signal detection:
if self.lane_change_direction and self.lane_change_timer > 0.5:
    from openpilot.selfdrive.pathd.lane_change import evaluate_lane_change_gate
    
    gate_result = evaluate_lane_change_gate(
        self.lane_change_direction,
        car_state,
        tracked_objects,
        predicted_objects,
        ego_velocity
    )
    
    if not gate_result.safe:
        # Block lane change, keep emergency braking ready
        trajectory_alert_level = "warning"
        trajectory_safety_reason = f"lane_change_blocked_{self.lane_change_direction}"
```

**Impact**: Enables safe lane change gating with blindspot monitoring.

---

### Issue 3: Path Corridor Fusion Not Integrated ⚠️

**Problem**: `path_corridor_fusion.py` provides multi-source boundary fusion but is not used.

**Location**:
- `path_corridor_fusion.py`: Complete fusion logic
- `pathd.py:617-618`: Hardcoded stereo boundaries

**Solution** (apply to `pathd.py`):
```python
# In StarD.__init__:
from openpilot.selfdrive.pathd.path_corridor_fusion import fuse_corridor_boundaries

# In StarD.update(), replace hardcoded boundaries:
left_boundary, right_boundary, source, confidence = fuse_corridor_boundaries(
    model_v2, ground_objects)

# Use fused boundaries in _check_stereo_boundaries:
# (rename method to _check_corridor_boundaries)
```

**Impact**: Uses vision model as fallback when stereo boundaries unavailable.

---

### Issue 4: Unused Method

**Problem**: `is_high_confidence_detection()` defined but never called.

**Location**: `pathd.py:480-504`

**Solution**: Either:
- **Option A**: Integrate into threat filtering (replace `obj.prob < 0.5` check)
- **Option B**: Remove method to reduce maintenance burden

---

### Issue 5: Stale Comments Fixed ✅

**Fixed**: `blindspot.py:28`
```python
# OLD: "tracked_objects: live track list from trackd"
# NEW: "tracked_objects: live track list from internal tracker"
```

## Performance

Target: 20 Hz on RK3588 A76
- Grid processing: ~5ms
- Cluster tracking: ~3ms
- Prediction: ~1ms
- Safety checks: ~2ms

Total: ~11ms per frame (well within 50ms budget)

## Integration Priority

| Priority | Module | Effort | Impact |
|----------|--------|--------|--------|
| P1 | `path_corridor_fusion.py` | Low | High (robustness) |
| P2 | `lateral_offset.py` | Low | Medium (comfort) |
| P3 | `lane_change.py` + `blindspot.py` | Medium | Medium (safety) |
| P4 | Remove dead code | Low | Low (maintenance) |

## See Also

- `selfdrive/gridd/` - BEV occupancy grid generation
- `system/v4l2d/` - Camera capture (merged camera capture)
- `selfdrive/pathd/track.py` - Internal cluster tracker
- `selfdrive/pathd/predict.py` - Trajectory prediction
- `cereal/log.capnp` - `EnhancedTrajectory` message schema
