# Design Document: LonNudge (Enhanced Longitudinal Controller)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/pathd/lon_nudge.py` |

---


> **Full Name:** Enhanced Longitudinal Controller  
> **Component Type:** Module inside `pathd` (daemon)  
> **Complexity:** High  
> **Tier:** Enhanced Vision (requires stereo pipeline)  
> **Reference Implementation:** None - EOP original design  
> **EOP Integration:** `selfdrive/pathd/lon_nudge.py` (PathD module) (module within PathD)

---

## 1. Objective

LonNudge provides stereo-enhanced longitudinal control that optimizes speed and comfort using 3D road surface analysis. It adjusts speed based on surface quality, compensates for road slope using stereo-derived elevation, and provides more accurate lead vehicle tracking.

**Key Benefits:**
- **Surface Quality Speed Adjustment:** Slow down for rough/bumpy roads automatically
- **Road Slope Compensation:** Use stereo-derived gradient for hill-aware control
- **3D Lead Tracking:** More accurate distance and relative speed using depth
- **Vertical Comfort Control:** Anticipate road undulations for smoother ride

---

## 2. Why LonNudge is Enhanced Vision

| Requirement | LonNudge Needs |
|-------------|------------|
| **Policy Trajectory** | ✅ **Required** - from modeld (drivingModelData.velocity) |
| **Stereo cameras** | ✅ **Required** - ground plane + elevation analysis |
| **Road/Wide road cameras** | ❌ No - policy model handles this |
| **GPS** | ❌ No - stereo elevation is primary |
| **OSM/Maps** | ❌ No - real-time surface analysis |

**Architecture Note:**
LonNudge does **NOT** directly use Road/Wide road cameras. Instead:
1. Road + Wide road cameras → **modeld** → policy speeds (drivingModelData.velocity)
2. Stereo cameras → **GRIDD** → ground plane + elevation + surface quality
3. LonNudge **adjusts** policy speeds using stereo surface/elevation data

**Conclusion:** LonNudge requires stereo depth to adjust the policy speeds - it's an Enhanced Vision feature.

---

## 3. Technical Architecture

### 3.1 System Context

```
┌─────────────────────────────────────────────────────────────────┐
│                    LonNudge SYSTEM CONTEXT                          │
└─────────────────────────────────────────────────────────────────┘

INPUTS TO LonNudge:
================

modeld (driving_vision RKNN)
    └── drivingModelData
        ├── velocity[33]       (Policy speed targets)
        ├── acceleration[33]   (Policy accel targets)
        └── meta               (Stop/go signals)
                ↓
GRIDD (stereo processing)
    ├── stereoGround.elevation_profile[33]  (Road height profile)
    ├── stereoGround.roughness_score        (Surface quality)
    ├── stereoGround.slope_grade            (Current grade %)
    └── gridObjects[32]                     (3D lead positions)
            ↓
    ┌─────────────────────────────────────┐
    │         LonNudge Module                 │
    │    (selfdrive/pathd/lon_nudge.py)        │
    │                                     │
    │  ┌─────────────────────────────┐    │
    │  │  SPEED ADJUSTMENT:          │    │
    │  │  policy_speed + stereo_adj  │    │
    │  │  = enhanced_speeds          │    │
    │  └─────────────────────────────┘    │
    │                                     │
    │  Policy says: "Drive 60 km/h"       │
    │  Stereo sees: "Rough road ahead"    │
    │  LonNudge adjusts: "Reduce to 50 km/h"  │
    └─────────────────────────────────────┘
            ↓
    enhancedTrajectory (with speed adjustments)
            ↓
    longitudinal_planner / controlsd
```

### 3.2 Input Data

**Primary Input (Policy Speeds):**
| Source | Field | Type | Description |
|--------|-------|------|-------------|
| **modeld** | `velocity.x[33]` | float | Policy speed targets from driving_vision |
| **modeld** | `acceleration.x[33]` | float | Policy acceleration targets |
| **modeld** | `meta` | struct | Policy stop/go predictions |

**Enhancement Input (Stereo Data):**
| Source | Field | Type | Description |
|--------|-------|------|-------------|
| **GRIDD** | `elevation_profile[33]` | float[33] | Road height at 3m intervals (0-99m) |
| **GRIDD** | `roughness_score` | float | Surface quality metric (0.0-1.0) |
| **GRIDD** | `slope_grade` | float | Current road grade (percent) |
| **GRIDD** | `gridObjects[].vx` | float | 3D lead relative velocity |

**Key Point:** LonNudge **adjusts** the policy speeds using stereo data. It doesn't generate speed profiles from scratch.

### 3.3 Core Algorithms

#### 3.3.1 Surface Roughness Analysis

```python
def analyze_surface_quality(stereo_ground, grid_objects):
    """
    Calculate surface roughness from stereo depth residuals.
    
    Returns:
        roughness_score: 0.0 (smooth) to 1.0 (very rough)
        speed_reduction: Recommended speed reduction factor
    """
    # Get ground plane parameters
    plane = stereo_ground.plane  # (a, b, c, d)
    
    # Calculate residuals (deviations from ideal plane)
    residuals = []
    for point in stereo_ground.ground_points:
        # Distance from point to plane
        distance = abs(plane.a * point.x + plane.b * point.y + 
                      plane.c * point.z + plane.d)
        distance /= np.sqrt(plane.a**2 + plane.b**2 + plane.c**2)
        residuals.append(distance)
    
    # Surface roughness = standard deviation of residuals
    roughness = np.std(residuals)
    
    # Normalize to 0-1 score
    # Typical: smooth asphalt < 0.02m, rough gravel > 0.1m
    roughness_score = np.clip(roughness / 0.1, 0.0, 1.0)
    
    # Calculate speed reduction
    # Conservative: reduce speed by up to 30% for rough surfaces
    speed_reduction = roughness_score * 0.30
    
    return roughness_score, speed_reduction
```

#### 3.3.2 Elevation Profile Analysis

```python
def analyze_elevation_profile(elevation_profile, v_ego):
    """
    Analyze road elevation for slope compensation and comfort.
    
    Returns:
        slope_grade: Current road grade (percent)
        upcoming_hills: List of (distance, grade) for lookahead
        comfort_speed: Recommended speed for vertical comfort
    """
    # Calculate current slope from first few points
    dx = 3.0  # 3m spacing
    dz = elevation_profile[1] - elevation_profile[0]
    slope_grade = (dz / dx) * 100  # Convert to percent
    
    # Analyze upcoming hills
    upcoming_hills = []
    for i in range(1, len(elevation_profile) - 1):
        local_slope = (elevation_profile[i+1] - elevation_profile[i]) / dx
        local_grade = local_slope * 100
        
        if abs(local_grade) > 3.0:  # Significant grade (>3%)
            distance = i * dx
            upcoming_hills.append((distance, local_grade))
    
    # Calculate comfort speed based on vertical acceleration
    # Limit vertical acceleration to 0.3 m/s² for comfort
    max_vertical_accel = 0.3
    
    comfort_speeds = []
    for distance, grade in upcoming_hills:
        # Vertical acceleration = v² * (grade/100) / radius approximation
        # Simplified: limit speed based on grade change rate
        max_speed = np.sqrt(max_vertical_accel * distance / (abs(grade) / 100 + 0.01))
        comfort_speeds.append(max_speed)
    
    comfort_speed = min(comfort_speeds) if comfort_speeds else v_ego
    
    return slope_grade, upcoming_hills, comfort_speed
```

#### 3.3.3 3D Lead Vehicle Tracking

```python
def track_lead_vehicle_3d(grid_objects, v_ego, dt):
    """
    Track lead vehicle using 3D stereo data for improved accuracy.
    
    Returns:
        lead_distance: Accurate 3D distance to lead
        lead_relative_v: Relative velocity (more accurate than radar)
        lead_acceleration: Estimated lead acceleration
    """
    # Find lead vehicle in gridObjects
    lead = None
    for obj in grid_objects:
        if obj.type in [LEAD_CAR, LEAD_TRUCK]:
            if obj.x > 0 and abs(obj.y) < 2.0:  # In front, in lane
                if lead is None or obj.x < lead.x:
                    lead = obj
    
    if lead is None:
        return None, None, None
    
    # 3D distance (more accurate than 2D projection)
    lead_distance = np.sqrt(lead.x**2 + lead.y**2 + lead.z**2)
    
    # Relative velocity from stereo tracking
    lead_relative_v = lead.vx
    
    # Calculate acceleration using history
    lead_acceleration = 0.0
    if hasattr(self, 'lead_history'):
        if len(self.lead_history) >= 2:
            dv = lead_relative_v - self.lead_history[-1][1]
            dt_hist = self.lead_history[-1][0] - self.lead_history[-2][0]
            if dt_hist > 0:
                lead_acceleration = dv / dt_hist
        
        # Update history
        self.lead_history.append((t, lead_relative_v))
        self.lead_history = self.lead_history[-10:]  # Keep last 10
    else:
        self.lead_history = [(t, lead_relative_v)]
    
    return lead_distance, lead_relative_v, lead_acceleration
```

#### 3.3.4 Speed Target Fusion

```python
def calculate_enhanced_speed_target(self, v_ego, stereo_ground, 
                                    grid_objects, model_v2, t):
    """
    Calculate stereo-enhanced speed target.
    
    Combines:
    - Base speed from longitudinal planner
    - Surface quality reduction
    - Elevation comfort limits
    - 3D lead vehicle following
    """
    # Start with base speed (from planner or model)
    v_target = model_v2.velocity.x[0] if model_v2 else v_ego
    
    # Apply surface quality reduction
    roughness_score, surface_reduction = analyze_surface_quality(
        stereo_ground, grid_objects
    )
    v_surface = v_target * (1.0 - surface_reduction)
    
    # Apply elevation comfort limits
    _, _, v_comfort = analyze_elevation_profile(
        stereo_ground.elevation_profile, v_ego
    )
    
    # Apply 3D lead following
    lead_dist, lead_rel_v, lead_accel = track_lead_vehicle_3d(
        grid_objects, v_ego, dt
    )
    
    if lead_dist is not None:
        # Time-to-collision based lead following
        ttc = lead_dist / abs(lead_rel_v) if lead_rel_v < 0 else float('inf')
        
        if ttc < 3.0:  # Less than 3 seconds
            # Reduce speed to maintain safe following distance
            safe_distance = 2.0 * v_ego  # 2-second rule
            v_lead_follow = v_ego + lead_rel_v * (lead_dist / safe_distance)
            v_lead_follow = max(v_lead_follow, 0.0)
        else:
            v_lead_follow = v_target
    else:
        v_lead_follow = v_target
    
    # Final speed = minimum of all constraints
    v_enhanced = min(v_surface, v_comfort, v_lead_follow)
    
    # Calculate acceleration for smooth transition
    a_enhanced = (v_enhanced - v_ego) / 2.0  # 2-second smoothing
    a_enhanced = np.clip(a_enhanced, -2.0, 1.0)  # Comfort limits
    
    return v_enhanced, a_enhanced, {
        'surface_reduction': surface_reduction,
        'comfort_limited': v_comfort < v_target,
        'lead_following': lead_dist is not None,
        'roughness_score': roughness_score
    }
```

### 3.4 State Machine

```
┌─────────────────────────────────────────────────────────────────┐
│                     LonNudge STATE MACHINE                          │
└─────────────────────────────────────────────────────────────────┘

    ┌──────────────┐
    │   DISABLED   │ ← LonNudge disabled or no stereo data
    └──────┬───────┘
           │ Stereo available & enabled
           ▼
    ┌──────────────┐     Surface rough > 0.3
    │   CRUISING   │──────────────────┐
    │   (normal)   │                  │
    └──────┬───────┘                  ▼
           │                 ┌──────────────┐
           │ Lead detected   │   ROUGH      │
           ▼                 │   SURFACE    │
    ┌──────────────┐         │   (slow)     │
    │    LEAD      │         └──────┬───────┘
    │  FOLLOWING   │←───────────────┘ Surface smooth
    │              │
    └──────┬───────┘
           │ Lead cleared
           ▼
    ┌──────────────┐
    │   CRUISING   │ (cycle)
    └──────────────┘
```

### 3.5 Comfort Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| **Max Vertical Acceleration** | 0.3 m/s² | For ride comfort |
| **Max Speed Reduction** | 30% | For rough surfaces |
| **Smoothing Time Constant** | 2.0s | For speed transitions |
| **Lead Following TTC** | 2.0s | Target time-to-collision |
| **Grade Compensation** | ±5% | Hill slope limits |

---

## 4. EOP Implementation Plan

### 4.1 Files and Classes

| File | Purpose |
|------|---------|
| `selfdrive/pathd/lon_nudge.py` (PathD module) | LonNudge module class |
| `selfdrive/pathd/pathd.py` | Integration with PathD main loop |
| `common/params_keys.h` | `EOPLLonNudgeEnabled` parameter |

### 4.2 Class Structure

```python
# selfdrive/pathd/lon_nudge.py

import numpy as np
from typing import Tuple, Optional, Dict


class LonNudge:
    """
    Enhanced Longitudinal Controller - stereo-based speed optimization.
    
    Runs inside PathD as a trajectory enhancement module.
    """
    
    # Comfort limits
    MAX_VERTICAL_ACCEL = 0.3  # m/s²
    MAX_SPEED_REDUCTION = 0.30  # 30%
    SMOOTHING_TC = 2.0  # seconds
    TARGET_TTC = 2.0  # seconds
    MAX_GRADE = 5.0  # percent
    
    # State definitions
    STATE_DISABLED = 0
    STATE_CRUISING = 1
    STATE_ROUGH_SURFACE = 2
    STATE_LEAD_FOLLOWING = 3
    
    def __init__(self):
        self.enabled = False
        self.state = self.STATE_DISABLED
        
        # Speed smoothing
        self.current_v_target = 0.0
        self.alpha = 0.1  # EMA smoothing
        
        # Lead tracking history
        self.lead_history = []
        
        # Surface quality EMA
        self.roughness_ema = 0.0
    
    def update(self, stereo_ground, grid_objects, model_v2,
               v_ego: float, a_ego: float, t: float) -> Tuple[Optional[float], 
                                                               Optional[float],
                                                               Optional[Dict]]:
        """
        Main update method called by PathD at 20Hz.
        
        Args:
            stereo_ground: GRIDD stereo ground data
            grid_objects: List of 3D objects
            model_v2: Vision model output
            v_ego: Current speed (m/s)
            a_ego: Current acceleration (m/s²)
            t: Current time
            
        Returns:
            (v_target, a_target, metadata) or (None, None, None) if disabled
        """
        if not self.enabled or stereo_ground is None:
            return None, None, None
        
        # Calculate enhanced speed target
        v_target, a_target, metadata = self._calculate_speed_target(
            v_ego, stereo_ground, grid_objects, model_v2, t
        )
        
        # Apply smoothing
        v_target = self._smooth_speed(v_target)
        
        # Update state machine
        self._update_state_machine(metadata, t)
        
        return v_target, a_target, metadata
    
    def _calculate_speed_target(self, v_ego, stereo_ground, 
                                grid_objects, model_v2, t):
        """Calculate stereo-enhanced speed target."""
        # Implementation from 3.3.4
        pass
    
    def _smooth_speed(self, v_target):
        """Apply exponential moving average smoothing."""
        self.current_v_target += self.alpha * (v_target - self.current_v_target)
        return self.current_v_target
    
    def _update_state_machine(self, metadata, t):
        """Update LonNudge state machine."""
        # State transitions based on conditions
        pass
```

### 4.3 Integration in PathD

```python
# selfdrive/pathd/pathd.py

from openpilot.selfdrive.pathd.lon_nudge import LonNudge

class PathD:
    def __init__(self):
        # ... existing init ...
        self.lon_nudge = LonNudge()
        
    def update(self, sm):
        # ... existing processing ...
        
        # Run LonNudge for stereo-enhanced longitudinal control
        elon_v, elon_a, elon_meta = self.lon_nudge.update(
            stereo_ground=sm['gridd'].stereoGround,
            grid_objects=sm['gridd'].gridObjects,
            model_v2=sm['modelV2'],
            v_ego=sm['carState'].vEgo,
            a_ego=sm['carState'].aEgo,
            t=sm.logMonoTime['carState'] * 1e-9
        )
        
        # Merge LonNudge speed into enhanced trajectory
        if elon_v is not None:
            # Modify speed targets in trajectory
            for i in range(len(self.enhanced_trajectory.v)):
                # Blend LonNudge speed with base trajectory
                blend_factor = 0.3  # 30% LonNudge influence
                self.enhanced_trajectory.v[i] = (
                    (1 - blend_factor) * self.enhanced_trajectory.v[i] +
                    blend_factor * elon_v
                )
        
        # ... publish enhancedTrajectory ...
```

### 4.4 Parameter Management

**EOP Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPLLonNudgeEnabled` | Bool | `0` | Master toggle for LonNudge |
| `EOPLLonNudgeMaxVAccel` | Float | `0.3` | Max vertical acceleration (m/s²) |
| `EOPLLonNudgeMaxReduction` | Float | `0.30` | Max speed reduction (ratio) |
| `EOPLLonNudgeSmoothingTC` | Float | `2.0` | Speed smoothing time constant (s) |

---

## 5. Safety Analysis

### 5.1 Safety Mechanisms

| Mechanism | Implementation |
|-----------|----------------|
| **Max Reduction Limit** | 30% prevents excessive slowing |
| **Smooth Transitions** | 2-second smoothing prevents jerk |
| **Vertical Acceleration Cap** | 0.3 m/s² ride comfort limit |
| **Lead Following Override** | Driver gas overrides LonNudge |
| **Fallback to Model** | If stereo fails, use modelV2 speeds |

### 5.2 Risk Assessment

| Risk | Mitigation | Likelihood | Severity |
|------|------------|------------|----------|
| Excessive slowing | 30% max reduction | Low | Low |
| False rough surface detection | EMA smoothing + hysteresis | Low | Low |
| Uncomfortable ride | Vertical accel limits | Very Low | Low |
| Lead detection false positive | 3D tracking validation | Low | Medium |

---

## 6. Testing Plan

### 6.1 Unit Tests

```python
def test_surface_roughness():
    lon_nudge = LonNudge()
    # Test with known surface profiles
    
def test_elevation_analysis():
    lon_nudge = LonNudge()
    # Test hill detection and speed limits
    
def test_lead_tracking():
    lon_nudge = LonNudge()
    # Test 3D lead tracking accuracy
```

### 6.2 Integration Tests

- Rough road surface detection (gravel, potholes)
- Hill climbing/descending grade compensation
- Lead vehicle 3D tracking vs radar
- Vertical comfort on undulating roads

### 6.3 Real-World Validation

- Minimum 500km testing
- Various road surfaces (asphalt, concrete, gravel)
- Mountain roads with significant grades
- Highway speed stability validation

---

## 7. Comparison with Core Vision Features

| Aspect | Core DLON | Enhanced LonNudge |
|--------|-----------|---------------|
| **Input** | modelV2, radarState | stereoGround + gridObjects |
| **Mode Switching** | Chill/Experimental | N/A (enhances both) |
| **Surface Quality** | ❌ No | ✅ Yes (stereo) |
| **Grade Compensation** | ❌ No | ✅ Yes (stereo elevation) |
| **3D Lead Tracking** | ❌ No (2D radar) | ✅ Yes (3D stereo) |
| **Requires Stereo** | ❌ No | ✅ Yes |

---

## 8. Integration Points

### 8.1 Data Flow

```
GRIDD (stereoGround, gridObjects)
    ↓
LonNudge Module (inside PathD)
    ↓
enhancedTrajectory.v_target, a_target
    ↓
longitudinal_planner / controlsd
    ↓
CarController (throttle/brake)
```

### 8.2 Dependencies

**Required:**
- GRIDD with stereo processing
- stereoGround elevation_profile
- gridObjects with 3D velocities

**Optional:**
- modelV2 for base speed reference
- radarState for lead validation

---

## 9. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Design Document | ✅ Complete | This document |
| Stereo Integration | ✅ Complete | `stereoGround.drivableLimitM`, `bikeHazard`, `occupancyDetected` |
| Core Implementation | ✅ Complete | `selfdrive/pathd/lon_nudge.py` — dist ramp + TTC lead + bike factor |
| Unit Tests | ⏳ Pending | Test scenarios defined |
| Integration Test | ✅ Complete | PathD integration — speed_delta applied via `ACCEL_RESPONSE_TIME` |
| Documentation | ✅ Complete | This document |

---

---

## Implementation

### Function

Enhanced longitudinal control combining:
- Deep learning (DLON)
- MPC-based control
- Multiple constraints (speed limits, curves, leads)

### Algorithm

```python
# Combined output
accel = min(DLON_output, MPC_output, 
            MTSC_limit, MSLC_limit, AEB_limit)
```

### Code Location

- `selfdrive/controls/lib/longitudinal_planner.py`
- `selfdrive/controls/lib/long_mpc.py` *(not implemented)*


## 10. Related Documents

- [EOP OVERVIEW](../../00_Index/OVERVIEW.md) - EOP Architecture Overview
- PATHD.md - Path Daemon integration
- GRIDD.md - Grid Daemon data source
- DLON.md - Core Vision longitudinal switching
- [LAT_NUDGE.md](./LAT_NUDGE.md) - Enhanced Lateral Controller (companion module)
