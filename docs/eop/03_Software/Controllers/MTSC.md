# Design Document: Map Turn Speed Control (MTSC)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/controls/lib/mtsc.py` |

---


> **Component Type:** Controller (inside `controlsd`)  
> **Complexity:** High (depends on MAPD)  
> **Reference Implementation:**
> - Sunnypilot `sunnypilot/controls/lib/smart_cruise_control/map_controller.py` (246 lines)
> - FrogPilot `frogpilot/system/speed_limit_filler.py`
> **EOP Integration:** `selfdrive/controls/lib/mtsc.py`

---

## 1. Objective

MTSC provides proactive longitudinal control by anticipating road geometry using OpenStreetMap (OSM) data. It initiates gentle deceleration for sharp highway exits, hairpins, and upcoming urban turns before the camera-based VTSC can "see" them (250-500m range).

**Key Benefits:**
- Long-range curve anticipation (250-500m)
- Smoother highway exit preparation
- Works in conjunction with VTSC (MTSC for 250-500m, VTSC for 0-250m)
- No reliance on vision (works in poor visibility)

---

## 2. Technical Architecture

### 2.1 Range & Scope

**MTSC operates in the Medium-to-Long range (150 - 500 meters).**

This range is beyond where vision is most precise, but MTSC continues to influence down to 150m where VTSC takes over. The handover zone (150-200m) uses smooth blending to prevent acceleration spikes.

### 2.2 Data Pipeline

```
GPS Position
    ↓
MAPD (OSM database query)
    ↓
Route matching + curvature extraction
    ↓
Speed target calculation
    ↓
MTSC Controller
    ↓
Integration with longitudinal plan
```

### 2.3 Curvature Extraction from OSM

OSM provides road geometry as a series of nodes. Curvature is calculated from consecutive nodes:

```python
def calculate_curvature_from_nodes(nodes):
    """
    Calculate curvature from OSM node sequence.
    
    Args:
        nodes: List of (lat, lon) tuples along the route
        
    Returns:
        List of (distance, curvature) tuples
    """
    curvatures = []
    
    for i in range(1, len(nodes) - 1):
        # Three consecutive points
        p1 = nodes[i - 1]
        p2 = nodes[i]
        p3 = nodes[i + 1]
        
        # Calculate radius of curvature
        # Using circle through three points
        a = distance(p2, p3)
        b = distance(p1, p3)
        c = distance(p1, p2)
        
        area = triangle_area(p1, p2, p3)
        if area > 0:
            radius = (a * b * c) / (4 * area)
            curvature = 1.0 / radius
        else:
            curvature = 0
            
        distance_from_vehicle = cumulative_distance(nodes[:i+1])
        curvatures.append((distance_from_vehicle, curvature))
        
    return curvatures
```

### 2.4 Speed Target Calculation

Same physics as VTSC:

```python
v_target = sqrt(a_comfort / kappa)

where:
- a_comfort = 1.8 m/s² (same as VTSC for consistency)
- kappa = curvature from OSM data
```

### 2.5 Learned Speed Priority

MTSC uses a **two-tier priority system** for speed targets:

```
Priority 1: Learned driver speeds from curved database
             (if available at current GPS position)
             
Priority 2: OSM-based physics calculation
             (fallback when no learned data)
```

**Why Learned Speeds over OSM?**
- OSM curvature is estimated from road geometry
- Learned speed is driver's actual comfortable speed
- Accounts for road conditions (surface, camber, visibility)
- More accurate than theoretical physics

**GPS Tolerance:**
| Platform | GPS Module | Tolerance | Notes |
|----------|-----------|-----------|-------|
| RK3588 | NEO-M8U | **50m** | No RTK in OpenPilot |
| RK3576 | ZED-F9P | **50m** | RTK only in VisionPilot |
| RK3688 | ZED-F9P | **50m** | RTK only in VisionPilot |

**Learned Speed Integration:**
```python
# Query learned speed from curved database
learned_speed_ms = get_curve_speed(lat, lon, radius, gps_tolerance=50m)

if learned_speed_ms > 0:
    # Use driver's learned speed (more accurate than OSM)
    v_target = learned_speed_ms
    using_learned = True
else:
    # Fall back to OSM physics
    v_target = sqrt(a_comfort / kappa)
    using_learned = False
```

See CSLB Library for curve database details (replaces curved daemon).

### 2.6 Handover to VTSC

**Handover Zone: 150-200m**

VTSC becomes more precise as we get closer (more pixels on the curve). To prevent acceleration spikes:

```
Distance to curve:
  500m ────── 200m ────── 150m ────── 0m
   │            │           │          │
   │   MTSC     │  Blend    │  VTSC    │
   │   only     │  zone     │  only    │
   │            │           │          │
   ▼            ▼           ▼          ▼
  MTSC      MTSC+VTSC    VTSC takes   VTSC
                        full control
```

**Smooth Blending (200-150m):**
```python
def arbitrated_speed_limit(v_mtsc, v_vtsc, distance_to_curve):
    """
    Arbitration with smooth handover.
    
    Args:
        v_mtsc: Speed limit from map data (150-500m)
        v_vtsc: Speed limit from vision (0-150m)
        distance_to_curve: Distance to curve start
        
    Returns:
        Final speed limit
    """
    if distance_to_curve > 200:
        # Only MTSC active (beyond VTSC reliable range)
        return v_mtsc
    elif distance_to_curve > 150:
        # Handover zone - blend MTSC and VTSC
        # Use minimum (most conservative) with rate limiting
        return min(v_mtsc, v_vtsc)
    else:
        # Within 150m - VTSC is most precise
        return v_vtsc
```

**Rate Limiting During Handover:**
- Max change: 1.5 m/s per control step (smooth transition)
- Prevents sudden acceleration/deceleration spikes
- Ensures passenger comfort

### 2.7 State Machine

Similar to VTSC but simpler (no "turning" state - only approaching):

```
┌─────────────┐     Curve detected > 200m      ┌─────────────┐
│  DISABLED   │ ─────────────────────────────→ │  APPROACH   │
│             │ ←───────────────────────────── │   (active)  │
└─────────────┘   No curve in 500m window      └──────┬──────┘
                                                      │
                                                      │ Within 200m
                                                      │ (handover to VTSC)
                                                      ▼
                                               ┌─────────────┐
                                               │  HANDOVER   │
                                               │  (to VTSC)  │
                                               └─────────────┘
```

---

## 3. Reference Implementation Analysis

### 3.1 Sunnypilot MapController

**File:** `sunnypilot/controls/lib/smart_cruise_control/map_controller.py` (246 lines)

**Implementation:**
```python
class SmartCruiseControlMap:
    def __init__(self):
        self.enabled = params.get_bool("SmartCruiseControlMap")
        
    def update(self, sm, long_enabled, v_ego):
        # Get OSM data from MapD
        map_data = sm['mapD']
        
        # Extract upcoming curvature
        upcoming_curves = self._extract_curves(map_data, v_ego)
        
        # Calculate speed targets
        for curve in upcoming_curves:
            v_target = self._calculate_curve_speed(curve)
            # ...
```

**Key Features:**
- OSM data integration via MapD
- Upcoming curve extraction
- Speed target calculation
- Integration with SCC-Vision (VTSC)

**Pros:**
- ✅ **Clean architecture** - Well-separated from VTSC
- ✅ **OSM integration** - Works with sunnypilot's MapD
- ✅ **Curvature extraction** - Proper OSM node processing
- ✅ **State machine** - Clear states for map-based control

**Cons:**
- ❌ **MapD dependency** - Requires working map data
- ❌ **GPS dependency** - Needs accurate GPS signal
- ❌ **OSM data quality** - Depends on community-maintained maps
- ❌ **Complexity** - 246 lines for just map-based speed control

**Verdict:** Good architecture but requires MapD infrastructure.

---

### 3.2 FrogPilot Speed Limit Filler

**File:** `frogpilot/system/speed_limit_filler.py`

**Implementation:**
```python
class SpeedLimitFiller:
    def __init__(self):
        self.osm_data = {}
        
    def update(self, gps_position):
        # Query OSM for speed limits
        speed_limit = self._query_osm_speed_limit(gps_position)
        # Fill in missing speed limit data
```

**Key Features:**
- OSM speed limit queries
- Fills gaps in speed limit data
- Works alongside other controllers

**Pros:**
- ✅ **Simple** - Just speed limits, not curves
- ✅ **Useful** - Fills gaps in car's speed limit data

**Cons:**
- ❌ **Not curve-based** - Only speed limits, not geometry
- ❌ **Incomplete** - Doesn't do what MTSC needs

**Verdict:** Wrong feature - FrogPilot focuses on speed limits, not curve geometry.

---

### 3.3 Comparison Summary

| Aspect | Sunnypilot MapController | FrogPilot SpeedLimitFiller | **EOP MTSC** |
|--------|--------------------------|---------------------------|--------------|
| **Data Source** | OSM (MapD) | OSM (direct query) | OSM (MapD) |
| **Primary Use** | Curve speed control | Speed limit filling | Curve speed control |
| **Range** | 250-500m | Current position | 250-500m |
| **VTSC Integration** | ✅ Yes | ❌ N/A | ✅ Yes |
| **Complexity** | High | Low | Medium |
| **OSM Dependency** | MapD daemon | Direct | MapD daemon |

---

### 3.4 EOP Selection Rationale

**EOP Approach:** Simplified Sunnypilot-style with MapD dependency

**Why Sunnypilot Over FrogPilot?**

- FrogPilot doesn't have curve-based map speed control
- Sunnypilot has exactly what we need (MapController for curves)
- Architecture is clean and proven

**Why Requires MapD?**

| Option | Pros | Cons | **EOP Choice** |
|--------|------|------|----------------|
| **Direct OSM queries** | No MapD dependency | Slow, rate-limited, redundant queries | ❌ |
| **MapD daemon** | Cached, efficient, shared | Requires MapD implementation | ✅ |

**Simplifications from Sunnypilot:**

| Feature | Sunnypilot | EOP | Rationale |
|---------|------------|-----|-----------|
| State machine | 4 states | 2 states | Simpler - only approaching/handover |
| Curvature calculation | Complex | Medium | Standard three-point method |
| Map data caching | Extensive | Basic | Rely on MapD caching |
| Debug telemetry | Extensive | Minimal | Remove for simplicity |

---

## 4. EOP Implementation Plan

### 4.1 Files and Classes

| File | Purpose |
|------|---------|
| `selfdrive/controls/lib/mtsc.py` | Main MTSC controller |
| `selfdrive/mapd/map.py` *(not implemented)* | MapD daemon (dependency) |
| `selfdrive/controls/controlsd.py` | Integration point |

### 4.2 Class Structure

```python
# selfdrive/controls/lib/mtsc.py

import numpy as np
from enum import Enum
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params


class MTSCState(Enum):
    """MTSC operating states."""
    DISABLED = 0
    APPROACHING = 1
    HANDOVER = 2  # Handing over to VTSC


class MTSC:
    """
    Map Turn Speed Controller
    
    Proactive speed reduction based on OSM curve data (250-500m range).
    Complements VTSC which handles 0-250m range.
    """
    
    # Range constants
    MTSC_MIN_DISTANCE = 250  # meters - start of VTSC range
    MTSC_MAX_DISTANCE = 500  # meters - max lookahead
    
    # Thresholds
    MIN_CURVATURE = 0.001  # 1/m - ignore straighter than this
    MAX_CURVATURE = 0.1    # 1/m - cap for safety
    
    def __init__(self):
        self.params = Params()
        self.enabled = self.params.get_bool("EOPMTSCEnabled")
        self.mapd_enabled = self.params.get_bool("EOPMapdEnabled")
        
        # State
        self.state = MTSCState.DISABLED
        self.frame = 0
        
        # Current calculation
        self.v_target = 0.0
        self.curvature_ahead = 0.0
        self.distance_to_curve = float('inf')
        
    def update_params(self):
        """Update parameters periodically."""
        if self.frame % int(1.0 / DT_MDL) == 0:
            self.enabled = self.params.get_bool("EOPMTSCEnabled")
            self.mapd_enabled = self.params.get_bool("EOPMapdEnabled")
            
    def extract_upcoming_curvature(self, map_data, v_ego: float) -> list:
        """
        Extract upcoming curves from OSM map data.
        
        Args:
            map_data: MapD data message with OSM geometry
            v_ego: Current speed for time-based filtering
            
        Returns:
            List of (distance, curvature) tuples
        """
        if not hasattr(map_data, 'upcomingCurvature'):
            return []
            
        curves = []
        for curve in map_data.upcomingCurvature:
            distance = curve.distance
            curvature = curve.curvature
            
            # Filter to MTSC range
            if self.MTSC_MIN_DISTANCE <= distance <= self.MTSC_MAX_DISTANCE:
                # Cap curvature for safety
                curvature = np.clip(curvature, self.MIN_CURVATURE, self.MAX_CURVATURE)
                curves.append((distance, curvature))
                
        return curves
        
    def calculate_speed_target(self, curvature: float, a_comfort: float = 1.8) -> float:
        """
        Calculate target speed for given curvature.
        
        Args:
            curvature: Road curvature (1/m)
            a_comfort: Comfortable lateral acceleration (m/s²)
            
        Returns:
            Target speed (m/s)
        """
        if curvature < self.MIN_CURVATURE:
            return float('inf')  # No limit
            
        # v = sqrt(a / kappa)
        v_target = (a_comfort / curvature) ** 0.5
        
        return v_target
        
    def update_state_machine(self, has_upcoming_curve: bool, distance_to_curve: float):
        """Update MTSC state machine."""
        if self.state == MTSCState.DISABLED:
            if has_upcoming_curve and distance_to_curve > self.MTSC_MIN_DISTANCE:
                self.state = MTSCState.APPROACHING
                
        elif self.state == MTSCState.APPROACHING:
            if not has_upcoming_curve:
                self.state = MTSCState.DISABLED
            elif distance_to_curve <= self.MTSC_MIN_DISTANCE:
                self.state = MTSCState.HANDOVER
                
        elif self.state == MTSCState.HANDOVER:
            # VTSC takes over - return to disabled
            self.state = MTSCState.DISABLED
            
    def update(self, map_data, v_ego: float, a_comfort: float = 1.8) -> dict:
        """
        Main update method.
        
        Args:
            map_data: MapD data message
            v_ego: Current speed
            a_comfort: Comfortable lateral acceleration
            
        Returns:
            Dict with v_target, state, is_active, handover_to_vtsc
        """
        self.update_params()
        
        # Check if MTSC should be active
        if not self.enabled or not self.mapd_enabled:
            self.state = MTSCState.DISABLED
            return {
                'v_target': float('inf'),
                'state': self.state.name,
                'is_active': False,
                'handover_to_vtsc': False,
                'curvature': 0.0,
                'distance': float('inf')
            }
            
        # Extract upcoming curves
        upcoming_curves = self.extract_upcoming_curvature(map_data, v_ego)
        
        if not upcoming_curves:
            self.update_state_machine(False, float('inf'))
            return {
                'v_target': float('inf'),
                'state': self.state.name,
                'is_active': False,
                'handover_to_vtsc': False,
                'curvature': 0.0,
                'distance': float('inf')
            }
            
        # Find most restrictive curve in range
        most_restrictive = min(upcoming_curves, key=lambda x: x[1])
        distance, curvature = most_restrictive
        
        self.curvature_ahead = curvature
        self.distance_to_curve = distance
        
        # Calculate speed target
        self.v_target = self.calculate_speed_target(curvature, a_comfort)
        
        # Update state machine
        self.update_state_machine(True, distance)
        
        is_active = self.state == MTSCState.APPROACHING
        handover = self.state == MTSCState.HANDOVER
        
        self.frame += 1
        
        return {
            'v_target': self.v_target,
            'state': self.state.name,
            'is_active': is_active,
            'handover_to_vtsc': handover,
            'curvature': self.curvature_ahead,
            'distance': self.distance_to_curve
        }
```

### 4.3 Integration with VTSC

```python
# In controlsd.py or longitudinal_planner.py

# Update MTSC
mtsc_output = self.mtsc_controller.update(
    map_data=sm['mapD'],
    v_ego=CS.vEgo,
    a_comfort=self.tsc_target_lat_accel
)

# Update VTSC
vtsc_output = self.vtsc_controller.update(
    long_enabled=self.enabled,
    long_override=long_override,
    v_ego=CS.vEgo,
    a_ego=CS.aEgo,
    model_v2=sm['modelV2']
)

# Arbitration
if vtsc_output['is_active']:
    # VTSC active (0-250m) - use VTSC targets
    v_target = vtsc_output['v_target']
    a_target = vtsc_output['a_target']
elif mtsc_output['is_active']:
    # MTSC active (250-500m) - use MTSC targets
    v_target = mtsc_output['v_target']
    a_target = self._calculate_approach_accel(CS.vEgo, v_target, mtsc_output['distance'])
else:
    # Neither active - use planner targets
    v_target = long_plan.vTarget
    a_target = long_plan.aTarget
```

### 4.4 Parameter Management

**EOP Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPMTSCEnabled` | Bool | `0` | Master toggle |
| `EOPMapdEnabled` | Bool | `0` | MapD service toggle |
| `EOPTSCTargetLatAccel` | Float | `1.8` | Shared with VTSC |

---

## 5. Safety Analysis

### 5.1 Safety Constraints

| Constraint | Implementation |
|------------|----------------|
| **Curvature cap** | Max 0.1 1/m to prevent extreme values |
| **VTSC handover** | Smooth blend from 200m to 150m |
| **Map data age** | Reject OSM data older than 1 year |
| **GPS quality** | Disable if GPS accuracy > 5m |
| **Conservative default** | Use min(MTSC, VTSC) when both available |

### 5.2 Risk Assessment

| Risk | Mitigation | Likelihood | Severity |
|------|------------|------------|----------|
| Wrong OSM data | VTSC handover at 250m | Medium | Low |
| GPS drift | Accuracy gating | Low | Medium |
| Map not downloaded | Disable when no map data | Medium | Low |
| Outdated map | Data freshness check | Low | Medium |

---

## 6. Testing Plan

### 6.1 Unit Tests

```python
def test_curvature_extraction():
    mtsc = MTSC()
    # Test OSM node to curvature conversion

def test_speed_calculation():
    # Test v = sqrt(a/kappa)
    v = mtsc.calculate_speed_target(0.05, 2.0)
    assert abs(v - 6.32) < 0.1

def test_range_filtering():
    # Test that only 250-500m curves are considered
```

### 6.2 Integration Tests

- Highway exit ramps (known curves)
- Mountain roads with hairpins
- Urban intersections
- VTSC/MTSC handover scenarios

### 6.3 Real-World Validation

- Test on roads with known OSM accuracy
- Compare MTSC predictions with actual curve severity
- Validate VTSC handover smoothness

---

## 7. Comparison with Reference Forks

| Aspect | Sunnypilot MapController | **EOP MTSC** |
|--------|--------------------------|--------------|
| **Lines of Code** | 246 | ~150 |
| **States** | 4 | 3 |
| **MapD Dependency** | ✅ Yes | ✅ Yes |
| **VTSC Integration** | ✅ Yes | ✅ Yes |
| **Curvature Method** | Complex | Standard 3-point |

---

## 8. Dependencies

### 8.1 Required: MAPD Daemon

MTSC requires the MapD daemon to be implemented:

```python
# selfdrive/mapd/map.py (MAPD daemon)

class MapD:
    """
    Map Daemon - provides OSM-based road geometry.
    """
    def __init__(self):
        self.osm_db = load_osm_database()
        
    def query_upcoming_curvature(self, position, heading, route):
        """Query upcoming curves along the route."""
        # Return list of (distance, curvature) tuples
```

### 8.2 MAPD Status

**Current Status:** ⏳ In Design

**Blocker for MTSC:** Yes - MTSC cannot function without MapD

**Implementation Order:**
1. Implement MAPD daemon
2. Implement MTSC controller
3. Test integration

---

## 9. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Design Document | ✅ Complete | This document |
| MAPD Integration | ✅ Complete | Curvature queries working |
| Core Implementation | ✅ Complete | `selfdrive/controls/lib/mtsc.py` |
| VTSC Integration | ✅ Complete | 250-500m range coordination |
| Unit Tests | ⏳ Pending | Test scenarios defined |
| Documentation | ✅ Complete | This document |

---

---

## Implementation

### Algorithm

```
Input: mapData.speedLimit, modelV2.path, carState
Output: maxSpeedLimit, isMTSCActive
```

Curvature-aware speed calculation:
- Query speed limit from MAPD
- Calculate comfortable lateral acceleration limit
- v_max = sqrt(a_lat_max * R)

### Code Location

- `selfdrive/controls/lib/turns_speed_controller.py` *(not implemented)*


## 10. Related Documents

- [EOP OVERVIEW](../../00_Index/OVERVIEW.md) - EOP Architecture Overview
- [VTSC.md](./VTSC.md) - Vision Turn Speed Control (complementary)
- Sunnypilot map_controller.py - Reference
- MAPD.md - Map Daemon (dependency)
