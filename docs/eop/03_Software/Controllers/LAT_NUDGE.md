# Design Document: LatNudge (Enhanced Lateral Controller)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/pathd/lat_nudge.py` |

---


> **Full Name:** Enhanced Lateral Controller  
> **Component Type:** Module inside `pathd` (daemon)  
> **Complexity:** High  
> **Tier:** Enhanced Vision (requires stereo pipeline)  
> **Reference Implementation:** None - EOP original design  
> **EOP Integration:** `selfdrive/pathd/lat_nudge.py` (PathD module) (module within PathD)

---

## 1. Objective

LatNudge provides stereo-enhanced lateral control that goes beyond vision-based lane keeping. It uses 3D depth perception to perform lateral obstacle avoidance, respect road boundaries from stereo data, and maintain optimal lane position using spatial awareness.

**Key Benefits:**
- **Lateral Obstacle Avoidance:** Steer around detected objects using actual 3D positions
- **Road Boundary Awareness:** Use stereo-derived road edges when lane lines are absent
- **Lane Position Optimization:** Center using 3D lane boundaries rather than 2D projections
- **Comfortable Lane Changes:** Validate lateral path with stereo before committing

---

## 2. Why LatNudge is Enhanced Vision

| Requirement | LatNudge Needs |
|-------------|------------|
| **Policy Trajectory** | ✅ **Required** - from modeld (drivingModelData) |
| **Stereo cameras** | ✅ **Required** - 3D obstacle/boundary detection |
| **Road/Wide road cameras** | ❌ No - policy model handles this |
| **GPS** | ❌ No - vision only |
| **OSM/Maps** | ❌ No - real-time stereo only |

**Architecture Note:**
LatNudge does **NOT** directly use Road/Wide road cameras. Instead:
1. Road + Wide road cameras → **modeld** → policy trajectory (drivingModelData)
2. Stereo cameras → **GRIDD** → 3D obstacles + boundaries
3. LatNudge **blends** stereo enhancements INTO the policy trajectory

**Conclusion:** LatNudge requires stereo depth to enhance the policy trajectory - it's an Enhanced Vision feature.

---

## 3. Technical Architecture

### 3.1 System Context

```
┌─────────────────────────────────────────────────────────────────┐
│                    LatNudge SYSTEM CONTEXT                          │
└─────────────────────────────────────────────────────────────────┘

INPUTS TO LatNudge:
================

modeld (driving_vision RKNN)
    └── drivingModelData
        ├── predictedPath[33]    (x, y, z trajectory from policy)
        └── laneLines[4]         (2D lane predictions)
                ↓
GRIDD (stereo processing)
    ├── stereoGround.leftBoundary[7]   (3D points at 0-30m)
    ├── stereoGround.rightBoundary[7]  (3D points at 0-30m)
    └── gridObjects[32]                (3D obstacle positions)
            ↓
    ┌─────────────────────────────────────┐
    │         LatNudge Module                 │
    │    (selfdrive/pathd/lat_nudge.py)        │
    │                                     │
    │  ┌─────────────────────────────┐    │
    │  │  BLENDING OPERATION:        │    │
    │  │  policy_path + stereo_adj   │    │
    │  │  = enhanced_trajectory      │    │
    │  └─────────────────────────────┘    │
    │                                     │
    │  Policy says: "Go straight"         │
    │  Stereo sees: "Obstacle on left"    │
    │  LatNudge blends: "Nudge right 0.3m"    │
    └─────────────────────────────────────┘
            ↓
    enhancedTrajectory (with lateral adjustments)
            ↓
    controlsd (lateral controller)
```

### 3.2 Input Data

**Primary Input (Policy Trajectory):**
| Source | Field | Type | Description |
|--------|-------|------|-------------|
| **modeld** | `predictedPath[33]` | Path | Policy trajectory from driving_vision RKNN |
| **modeld** | `laneLines[4]` | Line[4] | 2D lane predictions |
| **modeld** | `drivingModelData` | struct | Complete policy output |

**Enhancement Input (Stereo Data):**
| Source | Field | Type | Description |
|--------|-------|------|-------------|
| **GRIDD** | `leftBoundary[7]` | float3[7] | 3D road edge points at [0,5,10,15,20,25,30]m |
| **GRIDD** | `rightBoundary[7]` | float3[7] | 3D road edge points at [0,5,10,15,20,25,30]m |
| **GRIDD** | `gridObjects[32]` | Object[32] | 3D obstacles with positions/velocities |
| **GRIDD** | `drivableLimitM` | float | Maximum drivable distance |

**Key Point:** LatNudge **modifies** the policy trajectory using stereo data. It doesn't generate trajectories from scratch.

### 3.3 Core Algorithms

#### 3.3.1 Lateral Space Calculation

```python
def calculate_lateral_space(stereo_ground, grid_objects, ego_position):
    """
    Calculate available lateral space at each distance.
    
    Returns:
        space_profile: Array of (distance, left_limit, right_limit, width)
    """
    space_profile = []
    
    for distance in [0, 5, 10, 15, 20, 25, 30]:  # meters
        # Get stereo boundaries at this distance
        left_edge = stereo_ground.leftBoundary[distance // 5]
        right_edge = stereo_ground.rightBoundary[distance // 5]
        
        # Default limits from stereo road edges
        left_limit = left_edge.y
        right_limit = right_edge.y
        
        # Adjust for obstacles
        for obj in grid_objects:
            if abs(obj.x - distance) < 2.0:  # Within 2m longitudinal
                # Object occupies lateral space
                obj_left = obj.y + obj.width / 2
                obj_right = obj.y - obj.width / 2
                
                # Shrink available space
                if obj_left < 0:  # Object on left side
                    left_limit = max(left_limit, obj_left + SAFETY_MARGIN)
                if obj_right > 0:  # Object on right side
                    right_limit = min(right_limit, obj_right - SAFETY_MARGIN)
        
        width = right_limit - left_limit
        space_profile.append((distance, left_limit, right_limit, width))
    
    return space_profile
```

#### 3.3.2 Lateral Obstacle Avoidance

```python
def plan_avoidance_trajectory(space_profile, desired_path, v_ego):
    """
    Plan lateral offset to avoid obstacles while staying on path.
    
    Returns:
        y_offsets: Array of lateral offsets at each distance
    """
    y_offsets = []
    
    for i, (distance, left_limit, right_limit, width) in enumerate(space_profile):
        desired_y = desired_path.y[i]
        
        # Check if desired path is safe
        if left_limit <= desired_y <= right_limit:
            # Path is clear, minimal adjustment
            y_offset = desired_y * 0.1  # Slight centering tendency
        else:
            # Path blocked, need to steer around
            if desired_y < left_limit:
                # Would hit left obstacle, steer right
                y_offset = left_limit + SAFETY_MARGIN
            else:
                # Would hit right obstacle, steer left
                y_offset = right_limit - SAFETY_MARGIN
            
            # Clamp to vehicle dynamics limits
            max_offset = calculate_max_offset(v_ego, distance)
            y_offset = np.clip(y_offset, -max_offset, max_offset)
        
        y_offsets.append(y_offset)
    
    return smooth_offsets(y_offsets)  # Apply low-pass filter
```

#### 3.3.3 Lane Centering with 3D Boundaries

```python
def calculate_optimal_lane_position(stereo_ground, model_lane_lines, v_ego):
    """
    Calculate optimal lane position using both stereo and model data.
    
    Weights:
    - High confidence: Use stereo boundaries
    - Low confidence: Fall back to model lane lines
    """
    # Check stereo boundary confidence
    stereo_confidence = calculate_stereo_confidence(stereo_ground)
    
    if stereo_confidence > 0.7:
        # Use stereo boundaries for centering
        left_edge = stereo_ground.leftBoundary[0].y
        right_edge = stereo_ground.rightBoundary[0].y
        center = (left_edge + right_edge) / 2
        
        # Add bias for larger vehicles (truck nudge - SOC integration)
        center += calculate_truck_nudge(grid_objects)
    else:
        # Fall back to model lane lines
        center = (model_lane_lines[1].y[0] + model_lane_lines[2].y[0]) / 2
    
    return center
```

### 3.4 State Machine

```
┌─────────────────────────────────────────────────────────────────┐
│                     LatNudge STATE MACHINE                          │
└─────────────────────────────────────────────────────────────────┘

    ┌──────────────┐
    │   DISABLED   │ ← LatNudge disabled or no stereo data
    └──────┬───────┘
           │ Stereo available & enabled
           ▼
    ┌──────────────┐
    │   CENTERING  │ ← Normal lane centering mode
    └──────┬───────┘
           │ Obstacle detected in path
           ▼
    ┌──────────────┐
    │  AVOIDING    │ ← Lateral obstacle avoidance active
    └──────┬───────┘
           │ Obstacle cleared or path safe
           ▼
    ┌──────────────┐
    │   RETURNING  │ ← Transition back to center
    └──────┬───────┘
           │ Centered in lane
           ▼
    ┌──────────────┐
    │   CENTERING  │ (cycle complete)
    └──────────────┘
```

### 3.5 Safety Constraints

| Constraint | Value | Description |
|------------|-------|-------------|
| **Max Lateral Offset** | 0.8m | Don't cross into adjacent lane |
| **Max Lateral Jerk** | 2.0 m/s³ | Comfortable steering rate |
| **Min Clearance** | 0.5m | Safety margin from obstacles |
| **Max Avoidance Speed** | 80 km/h | Disable above highway speeds |
| **Stereo Confidence Min** | 0.5 | Fall back to model if low |

---

## 4. EOP Implementation Plan

### 4.1 Files and Classes

| File | Purpose |
|------|---------|
| `selfdrive/pathd/lat_nudge.py` (PathD module) | LatNudge module class |
| `selfdrive/pathd/pathd.py` | Integration with PathD main loop |
| `common/params_keys.h` | `EOPLLatNudgeEnabled` parameter |

### 4.2 Class Structure

```python
# selfdrive/pathd/lat_nudge.py

import numpy as np
from typing import List, Tuple, Optional


class LatNudge:
    """
    Enhanced Lateral Controller - stereo-based lateral control.
    
    Runs inside PathD as a trajectory enhancement module.
    """
    
    # Safety limits
    MAX_LATERAL_OFFSET = 0.8  # meters
    MAX_LATERAL_JERK = 2.0    # m/s³
    SAFETY_MARGIN = 0.5       # meters from obstacles
    MAX_AVOIDANCE_SPEED = 80 / 3.6  # 80 km/h in m/s
    
    # State definitions
    STATE_DISABLED = 0
    STATE_CENTERING = 1
    STATE_AVOIDING = 2
    STATE_RETURNING = 3
    
    def __init__(self):
        self.enabled = False
        self.state = self.STATE_DISABLED
        self.current_offset = 0.0
        self.target_offset = 0.0
        
        # Smoothing
        self.offset_filter_alpha = 0.1
        
        # State timing
        self.state_entry_time = 0.0
        self.avoidance_start_dist = 0.0
    
    def update(self, stereo_ground, grid_objects, model_v2, 
               v_ego: float, t: float) -> Optional[np.ndarray]:
        """
        Main update method called by PathD at 20Hz.
        
        Args:
            stereo_ground: GRIDD stereo ground data
            grid_objects: List of 3D objects
            model_v2: Vision model output
            v_ego: Current speed (m/s)
            t: Current time (for state machine)
            
        Returns:
            y_offsets: Array of 7 lateral offsets (0-30m) or None if disabled
        """
        if not self.enabled or v_ego > self.MAX_AVOIDANCE_SPEED:
            return None
        
        # Check stereo data validity
        if stereo_ground is None or not hasattr(stereo_ground, 'leftBoundary'):
            return None
        
        # Calculate lateral space profile
        space_profile = self._calculate_space_profile(
            stereo_ground, grid_objects
        )
        
        # Get desired path from model
        desired_y = self._get_desired_path(model_v2)
        
        # Plan trajectory
        y_offsets = self._plan_trajectory(
            space_profile, desired_y, v_ego
        )
        
        # Apply smoothing
        y_offsets = self._smooth_offsets(y_offsets)
        
        # Update state machine
        self._update_state_machine(space_profile, t)
        
        return y_offsets
    
    def _calculate_space_profile(self, stereo_ground, grid_objects):
        """Calculate available lateral space at each distance."""
        # Implementation from 3.3.1
        pass
    
    def _get_desired_path(self, model_v2):
        """Extract desired lateral path from model."""
        # Use lane center or predicted path
        pass
    
    def _plan_trajectory(self, space_profile, desired_y, v_ego):
        """Plan avoidance trajectory."""
        # Implementation from 3.3.2
        pass
    
    def _smooth_offsets(self, y_offsets):
        """Apply low-pass filter for smooth transitions."""
        self.current_offset += self.offset_filter_alpha * (
            y_offsets[0] - self.current_offset
        )
        return y_offsets
    
    def _update_state_machine(self, space_profile, t):
        """Update LatNudge state machine."""
        # State transitions
        pass
```

### 4.3 Integration in PathD

```python
# selfdrive/pathd/pathd.py

from openpilot.selfdrive.pathd.lat_nudge import LatNudge

class PathD:
    def __init__(self):
        # ... existing init ...
        self.lat_nudge = LatNudge()
        
    def update(self, sm):
        # ... existing processing ...
        
        # Run LatNudge for stereo-enhanced lateral control
        lat_offsets = self.lat_nudge.update(
            stereo_ground=sm['gridd'].stereoGround,
            grid_objects=sm['gridd'].gridObjects,
            model_v2=sm['modelV2'],
            v_ego=sm['carState'].vEgo,
            t=sm.logMonoTime['carState'] * 1e-9
        )
        
        # Merge LatNudge offsets into enhanced trajectory
        if lat_offsets is not None:
            for i, offset in enumerate(lat_offsets):
                self.enhanced_trajectory.y[i] += offset
        
        # ... publish enhancedTrajectory ...
```

### 4.4 Parameter Management

**EOP Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPLLatNudgeEnabled` | Bool | `0` | Master toggle for LatNudge |
| `EOPLLatNudgeMaxOffset` | Float | `0.8` | Maximum lateral offset (m) |
| `EOPLLatNudgeSafetyMargin` | Float | `0.5` | Safety margin from obstacles (m) |
| `EOPLLatNudgeMaxSpeed` | Float | `80` | Maximum speed for LatNudge (km/h) |

---

## 5. Safety Analysis

### 5.1 Safety Mechanisms

| Mechanism | Implementation |
|-----------|----------------|
| **Offset Clamping** | Max 0.8m prevents lane crossing |
| **Speed Gating** | Disabled above 80 km/h |
| **Confidence Check** | Falls back to model if stereo low confidence |
| **Smooth Transitions** | Low-pass filter prevents sudden steering |
| **Emergency Override** | Driver steering torque disables LatNudge |

### 5.2 Risk Assessment

| Risk | Mitigation | Likelihood | Severity |
|------|------------|------------|----------|
| Excessive offset | Hard limit + smooth filtering | Low | Medium |
| Stereo false positive | Confidence threshold + model fallback | Low | Medium |
| Adjacent lane intrusion | 0.8m max offset (typical lane 3.5m) | Very Low | High |
| Oscillation | State machine hysteresis | Low | Low |

---

## 6. Testing Plan

### 6.1 Unit Tests

```python
def test_space_calculation():
    lat_nudge = LatNudge()
    # Test with known obstacle positions
    
def test_offset_clamping():
    lat_nudge = LatNudge()
    # Verify max offset never exceeded
    
def test_state_transitions():
    lat_nudge = LatNudge()
    # Test centering → avoiding → returning
```

### 6.2 Integration Tests

- Static obstacle avoidance (cones/barrels)
- Dynamic obstacle avoidance (moving vehicles)
- Lane centering with faded markings
- Road edge following (no lane lines)

### 6.3 Real-World Validation

- Minimum 500km testing
- Various road types (highway, urban, rural)
- Different lighting conditions
- Validate no unintended lane departures

---

## 7. Comparison with Core Vision Features

| Aspect | Core DLAT | Enhanced LatNudge |
|--------|-----------|---------------|
| **Input** | modelV2.laneLines | stereoGround + gridObjects |
| **Mode Switching** | Laneful/Laneless | N/A (enhances both) |
| **Obstacle Avoidance** | ❌ No | ✅ Yes (3D) |
| **Road Boundaries** | modelV2.roadEdges | stereoGround boundaries |
| **Lateral Offset** | ❌ No | ✅ Yes (dynamic) |
| **Requires Stereo** | ❌ No | ✅ Yes |

---

## 8. Integration Points

### 8.1 Data Flow

```
GRIDD (stereoGround, gridObjects)
    ↓
LatNudge Module (inside PathD)
    ↓
enhancedTrajectory.y_offsets
    ↓
controlsd (lateral controller)
    ↓
CarController (steering)
```

### 8.2 Dependencies

**Required:**
- GRIDD with stereo processing
- stereoGround boundaries
- gridObjects

**Optional:**
- modelV2 for desired path reference

---

## 9. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Design Document | ✅ Complete | This document |
| Stereo Integration | ✅ Complete | `stereoGround.leftBoundary[7]`, `rightBoundary[7]` |
| Core Implementation | ✅ Complete | `selfdrive/pathd/lat_nudge.py` — 4-state machine, EMA filter |
| Unit Tests | ⏳ Pending | Test scenarios defined |
| Integration Test | ✅ Complete | PathD integration — 7→33pt interpolation, `lateral_adjustments` |
| Documentation | ✅ Complete | This document |

---

---

## Implementation

### Function

Enhanced lateral control combining:
- Deep learning (DLAT)
- Classical control (PID)
- Smooth transitions between modes

### Algorithm

```python
# Combined output
steerAngle = blend(DLAT_output, PID_output, 
                   confidence=modelV2.meta.desireState)
```

### Code Location

- `selfdrive/controls/lib/lateral_planner.py` *(not implemented)*


## 10. Related Documents

- [EOP OVERVIEW](../../00_Index/OVERVIEW.md) - EOP Architecture Overview
- PATHD.md - Path Daemon integration
- GRIDD.md - Grid Daemon data source
- DLAT.md - Core Vision lateral switching
