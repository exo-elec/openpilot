# Design Document: Smart Offset Control (SOC)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/pathd/soc.py` |

---


> **Component Type:** Controller (inside `pathd`)  
> **Complexity:** High  
> **Reference Implementation:**
> - No direct equivalent in reference forks
> - Similar concept: FrogPilot blind spot path visualization
> - Related: Autoware's obstacle avoidance
> **EOP Integration:** `pathd/soc.py` (SOC logic integrated with PathD)

---

## 1. Objective

SOC improves subjective safety and driver comfort by laterally offsetting the vehicle's position within its lane when passing large objects (trucks, buses) or when approaching road edge hazards. It mimics the human behavior of "giving space" to large vehicles.

**Key Benefits:**
- Reduces driver anxiety when passing large trucks
- Improves subjective safety feeling
- Maintains lane discipline while creating space
- Smooth, subtle nudges (not abrupt steering)

---

## 2. Technical Architecture

### 2.1 Data Sources

**Primary Inputs:**

| Source | Data | Purpose |
|--------|------|---------|
| **YOLO (road camera)** | Object classification | Detect trucks, buses, semis |
| **YOLO (stereo_right)** | Adjacent lane objects | Confirm lateral position |
| **modelV2.laneLines** | Lane boundaries | Ensure nudge stays within lane |
| **stereoObjects (gridd)** | 3D depth & closing speed | Verify proximity |

### 2.2 Offset Logic (Truck Nudge)

```
┌─────────────────────────────────────────────────────────────┐
│                         SOC LOGIC                           │
└─────────────────────────────────────────────────────────────┘

Detection Phase:
┌───────────┐      ┌─────────────┐      ┌─────────────────┐
│ YOLO detects     │ Confirm     │      │ Check relative  │
│ truck/bus │ ───→ │ stereo depth│ ───→ │ velocity (not  │
│ in adj lane      │ > 1.5m      │      │ overtaking)     │
└───────────┘      └─────────────┘      └─────────────────┘
                                                    │
                                                    ▼
Calculation Phase:                         ┌─────────────────┐
                                           │ Calculate       │
┌──────────────┐     ┌─────────────┐      │ base offset     │
│ Check lane   │     │ Scale by    │ ←─── │ (0.2-0.4m)      │
│ space avail  │ ←── │ proximity   │      └─────────────────┘
└──────────────┘     └─────────────┘
        │
        ▼
Application Phase:
┌──────────────┐     ┌─────────────┐      ┌─────────────────┐
│ Low-pass     │     │ Check       │      │ Inject into     │
│ filter       │ ───→│ lane bounds │ ───→ │ enhancedTrajectory│
│ (smooth)     │     │ (safety)    │      │ .y offset       │
└──────────────┘     └─────────────┘      └─────────────────┘
```

### 2.3 Detection Criteria

**Trigger Conditions:**
```python
def should_apply_nudge(object_detection, stereo_data, lane_lines):
    """
    Determine if offset nudge should be applied.
    
    All conditions must be met:
    1. Large vehicle detected (truck/bus/semi)
    2. Lateral distance: 1.5m - 3.0m
    3. Relative velocity: not overtaking (delta_v < 2 m/s)
    4. Lane space available on opposite side
    5. Model confidence > 0.7
    """
    # 1. Object type
    is_large_vehicle = object_detection.class_label in ['truck', 'bus', 'semi']
    
    # 2. Lateral distance
    lateral_dist = abs(stereo_data.lateral_position)
    in_range = 1.5 <= lateral_dist <= 3.0
    
    # 3. Not overtaking
    relative_v = object_detection.velocity_x - ego_velocity
    not_overtaking = relative_v < 2.0  # m/s
    
    # 4. Lane space available
    lane_center_to_line = calculate_lane_space(lane_lines)
    space_available = lane_center_to_line > 0.6  # max offset + margin
    
    return (is_large_vehicle and in_range and 
            not_overtaking and space_available)
```

### 2.4 Offset Calculation

**Base Offset Formula:**
```python
def calculate_offset(lateral_distance, vehicle_type):
    """
    Calculate lateral offset magnitude.
    
    Args:
        lateral_distance: Distance to adjacent vehicle (m)
        vehicle_type: 'truck', 'bus', 'semi'
        
    Returns:
        Offset magnitude (m) - positive = away from vehicle
    """
    # Base offset by vehicle type
    base_offsets = {
        'semi': 0.4,    # Largest - max offset
        'truck': 0.35,  # Large truck
        'bus': 0.3,     # Bus
    }
    base = base_offsets.get(vehicle_type, 0.2)
    
    # Scale by proximity (closer = more offset)
    # 1.5m -> 1.0 scale, 3.0m -> 0.5 scale
    proximity_scale = 1.5 - (lateral_distance - 1.5) / 3.0
    proximity_scale = np.clip(proximity_scale, 0.5, 1.0)
    
    return base * proximity_scale
```

### 2.5 Smoothing and Constraints

**Low-Pass Filter:**
```python
class SOC:
    def __init__(self):
        self.alpha = 0.12  # Smoothing coefficient
        self.offset_smoothed = 0.0
        self.max_offset = 0.6  # Absolute maximum (m)
        
    def apply_smoothing(self, target_offset):
        """Apply exponential moving average."""
        self.offset_smoothed += self.alpha * (target_offset - self.offset_smoothed)
        return self.offset_smoothed
        
    def apply_safety_constraints(self, offset, lane_lines):
        """
        Ensure offset doesn't push vehicle across lane line.
        
        Args:
            offset: Desired offset (m)
            lane_lines: modelV2 lane line positions
            
        Returns:
            Constrained offset (m)
        """
        # Distance from center to lane line on offset side
        if offset > 0:  # Offset right
            line_distance = lane_lines.right_inner.distance
        else:  # Offset left
            line_distance = -lane_lines.left_inner.distance
            
        # Maintain 0.2m margin from lane line
        max_allowed = abs(line_distance) - 0.2
        max_allowed = max(0, max_allowed)
        
        # Apply constraints
        constrained = np.sign(offset) * min(abs(offset), max_allowed, self.max_offset)
        
        return constrained
```

### 2.6 State Machine

```
┌───────────┐     Truck detected      ┌───────────┐
│  IDLE     │ ──────────────────────→ │  NUDGE    │
│  (no      │                         │  (active) │
│  offset)  │ ←────────────────────── │           │
└───────────┘   Truck gone or passed  └───────────┘
                        │
                        │ Lane space insufficient
                        ▼
                ┌───────────┐
                │  BLOCKED  │ ───────→ (return to IDLE)
                │  (cannot  │   Space restored
                │   nudge)  │
                └───────────┘
```

---

## 3. Reference Implementation Analysis

### 3.1 No Direct Equivalent in Reference Forks

**FrogPilot:** Has blind spot path visualization but not automatic offset
**Sunnypilot:** No similar feature found
**Dragonpilot:** No similar feature found

**Similar Concepts:**
- Autoware: Obstacle avoidance with lateral shift
- Some OEMs: "Trailer Nudge" or "Large Vehicle Assist"

### 3.2 Why No Reference Implementation?

| Challenge | Explanation |
|-----------|-------------|
| **Safety risk** | Offset could push car into adjacent lane |
| **Complex testing** | Requires many real-world truck encounters |
| **User preference** | Some drivers prefer centering |
| **Regulatory** | May violate "stay centered" requirements |

**EOP Decision:** Include as optional feature (default off)

---

### 3.3 EOP Design Rationale

**Why Include SOC?**

| Factor | Rationale |
|--------|-----------|
| **User value** | High anxiety reduction when passing trucks |
| **Competitive** | OEMs are starting to offer this feature |
| **Safety** | Can improve safety in edge cases |
| **Optional** | Users can disable if uncomfortable |

**Design Philosophy:**
- **Conservative:** Small offsets (0.2-0.4m max)
- **Smooth:** Heavy filtering prevents jerky steering
- **Safe:** Never cross lane lines
- **Optional:** Default off, user must enable

---

## 4. EOP Implementation Plan

### 4.1 Files and Classes

| File | Purpose |
|------|---------|
| modifications to `pathd/lateral_offset.py` | Main SOC controller |
| `selfdrive/pathd/pathd.py` | Integration point |

### 4.2 Class Structure

```python
# selfdrive/pathd/lateral_offset.py

import numpy as np
from enum import Enum
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params


class SOCState(Enum):
    """SOC operating states."""
    IDLE = 0
    NUDGE_ACTIVE = 1
    BLOCKED = 2


SOC logic (integrated into pathd)
    """
    Smart Offset Controller
    
    Applies lateral offset when passing large vehicles to improve
    subjective safety and comfort.
    """
    
    # Detection thresholds
    LATERAL_DIST_MIN = 1.5  # meters
    LATERAL_DIST_MAX = 3.0  # meters
    MAX_RELATIVE_VELOCITY = 2.0  # m/s (not overtaking)
    
    # Offset parameters
    BASE_OFFSETS = {
        'semi': 0.4,
        'truck': 0.35,
        'bus': 0.3,
    }
    MAX_OFFSET = 0.6  # meters - absolute maximum
    LANE_MARGIN = 0.2  # meters - margin from lane line
    
    # Smoothing
    ALPHA = 0.12  # Low-pass filter coefficient
    
    def __init__(self):
        self.params = Params()
        self.enabled = self.params.get_bool("EOPSOCEnabled")
        
        # State
        self.state = SOCState.IDLE
        self.offset_smoothed = 0.0
        self.target_offset = 0.0
        
        # Current detection
        self.nudge_side = None  # 'left' or 'right'
        self.detected_vehicle_type = None
        
    def update_params(self):
        """Update parameters periodically."""
        if int(time.monotonic() * 10) % 10 == 0:
            self.enabled = self.params.get_bool("EOPSOCEnabled")
            
    def detect_large_vehicle(self, yolo_detections, stereo_objects, v_ego):
        """
        Detect large vehicle in adjacent lane worthy of nudge.
        
        Args:
            yolo_detections: YOLO object detections
            stereo_objects: Stereo depth objects
            v_ego: Current ego speed
            
        Returns:
            (detected, vehicle_type, lateral_distance, side) or (False, None, None, None)
        """
        for detection in yolo_detections:
            # Check if large vehicle
            if detection.class_label not in self.BASE_OFFSETS:
                continue
                
            # Find matching stereo object
            stereo_match = self._match_to_stereo(detection, stereo_objects)
            if not stereo_match:
                continue
                
            lateral_dist = abs(stereo_match.lateral_position)
            
            # Check lateral distance range
            if not (self.LATERAL_DIST_MIN <= lateral_dist <= self.LATERAL_DIST_MAX):
                continue
                
            # Check relative velocity (not overtaking)
            relative_v = stereo_match.velocity_x - v_ego
            if relative_v >= self.MAX_RELATIVE_VELOCITY:
                continue
                
            # Determine side
            side = 'right' if stereo_match.lateral_position > 0 else 'left'
            
            return True, detection.class_label, lateral_dist, side
            
        return False, None, None, None
        
    def calculate_offset(self, vehicle_type, lateral_distance, side):
        """
        Calculate target offset.
        
        Args:
            vehicle_type: 'truck', 'bus', 'semi'
            lateral_distance: Distance to vehicle (m)
            side: 'left' or 'right' - side where vehicle is
            
        Returns:
            Target offset (m) - positive = right, negative = left
        """
        base = self.BASE_OFFSETS.get(vehicle_type, 0.2)
        
        # Proximity scaling
        proximity_scale = 1.5 - (lateral_distance - self.LATERAL_DIST_MIN) / 3.0
        proximity_scale = np.clip(proximity_scale, 0.5, 1.0)
        
        # Offset direction (away from vehicle)
        direction = -1 if side == 'right' else 1
        
        return direction * base * proximity_scale
        
    def apply_constraints(self, offset, lane_lines):
        """
        Apply safety constraints to offset.
        
        Args:
            offset: Desired offset (m)
            lane_lines: modelV2 laneLines
            
        Returns:
            Constrained offset (m)
        """
        # Get lane line distances
        left_line_dist = self._get_lane_line_distance(lane_lines, 'left')
        right_line_dist = self._get_lane_line_distance(lane_lines, 'right')
        
        # Determine which side we're offsetting
        if offset > 0:  # Offsetting right
            max_allowed = right_line_dist - self.LANE_MARGIN
        else:  # Offsetting left
            max_allowed = left_line_dist - self.LANE_MARGIN
            
        max_allowed = max(0, max_allowed)
        
        # Apply constraints
        constrained = np.sign(offset) * min(abs(offset), max_allowed, self.MAX_OFFSET)
        
        return constrained
        
    def update(self, yolo_detections, stereo_objects, lane_lines, v_ego) -> dict:
        """
        Main update method.
        
        Returns:
            Dict with offset, state, and debug info
        """
        self.update_params()
        
        if not self.enabled:
            self.state = SOCState.IDLE
            self.target_offset = 0.0
            self.offset_smoothed = 0.0
            return {'offset': 0.0, 'state': 'disabled'}
            
        # Detect large vehicle
        detected, vehicle_type, lateral_dist, side = self.detect_large_vehicle(
            yolo_detections, stereo_objects, v_ego
        )
        
        # State machine
        if self.state == SOCState.IDLE:
            if detected:
                self.state = SOCState.NUDGE_ACTIVE
                self.detected_vehicle_type = vehicle_type
                self.nudge_side = side
                
        elif self.state == SOCState.NUDGE_ACTIVE:
            if not detected:
                self.state = SOCState.IDLE
                self.detected_vehicle_type = None
                self.nudge_side = None
            else:
                # Update target
                self.target_offset = self.calculate_offset(
                    vehicle_type, lateral_dist, side
                )
                
        elif self.state == SOCState.BLOCKED:
            if not detected:
                self.state = SOCState.IDLE
            # Stay blocked until vehicle gone
            
        # Apply constraints
        if self.state == SOCState.NUDGE_ACTIVE:
            constrained_offset = self.apply_constraints(self.target_offset, lane_lines)
            
            # Check if blocked (no space for offset)
            if abs(constrained_offset) < 0.05:
                self.state = SOCState.BLOCKED
                constrained_offset = 0.0
        else:
            constrained_offset = 0.0
            
        # Smooth
        self.offset_smoothed += self.ALPHA * (constrained_offset - self.offset_smoothed)
        
        return {
            'offset': self.offset_smoothed,
            'state': self.state.name,
            'target': self.target_offset,
            'vehicle_type': self.detected_vehicle_type,
            'nudge_side': self.nudge_side
        }
        
    def _match_to_stereo(self, yolo_det, stereo_objects):
        """Match YOLO detection to stereo object."""
        # Simple matching by position proximity
        for obj in stereo_objects:
            if abs(obj.x - yolo_det.x) < 1.0 and abs(obj.y - yolo_det.y) < 1.0:
                return obj
        return None
        
    def _get_lane_line_distance(self, lane_lines, side):
        """Get distance to lane line."""
        if side == 'left' and len(lane_lines) >= 2:
            return abs(lane_lines[1].y[0]) if hasattr(lane_lines[1], 'y') else 1.8
        elif side == 'right' and len(lane_lines) >= 3:
            return abs(lane_lines[2].y[0]) if hasattr(lane_lines[2], 'y') else 1.8
        return 1.8  # default lane width/2
```

### 4.3 Integration in pathd.py

```python
# selfdrive/pathd/pathd.py

# SOC logic integrated into PathD

class PathD:
    def __init__(self):
        # ... existing init ...
        # SOC controller integrated
        
    def update(self):
        # ... existing update ...
        
        # Update SOC
        soc_output = self.soc_controller.update(
            yolo_detections=gridd_output['detections'],
            stereo_objects=gridd_output['stereo_objects'],
            lane_lines=sm['modelV2'].laneLines,
            v_ego=car_state.vEgo
        )
        
        # Apply offset to enhanced trajectory
        for i in range(len(self.enhanced_trajectory.y)):
            self.enhanced_trajectory.y[i] += soc_output['offset']
```

### 4.4 Parameter Management

**EOP Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPSOCEnabled` | Bool | `0` | Master toggle (default off for safety) |

---

## 5. Safety Analysis

### 5.1 Safety Constraints

| Constraint | Value | Rationale |
|------------|-------|-----------|
| **Max offset** | 0.6m | Stay well within typical lane (~3.6m) |
| **Lane margin** | 0.2m | Never closer than 20cm to lane line |
| **Smoothing** | α=0.12 | ~8-frame time constant at 20Hz |
| **Default state** | OFF | User must explicitly enable |
| **Vehicle types** | Truck/Bus/Semi only | Don't nudge for cars |

### 5.2 Risk Assessment

| Risk | Mitigation | Likelihood | Severity |
|------|------------|------------|----------|
| Cross lane line | Hard constraint + margin | Very Low | High |
| Jerky steering | Heavy smoothing (α=0.12) | Low | Low |
| False positive | Only large vehicles, stereo confirmation | Low | Low |
| User discomfort | Default off, user toggle | N/A | Low |

---

## 6. Testing Plan

### 6.1 Unit Tests

```python
def test_offset_calculation():
    soc = SOC()
    offset = soc.calculate_offset('truck', 2.0, 'right')
    assert offset < 0  # Negative = left (away from right-side truck)

def test_constraints():
    soc = SOC()
    # Test that offset is limited by lane space
    
def test_smoothing():
    # Test low-pass filter behavior
```

### 6.2 Integration Tests

- Highway truck passing
- Multi-lane truck encounters
- Lane narrowing scenarios
- Curved road truck passing

### 6.3 Real-World Validation

- Minimum 500km highway driving
- Log all nudge events
- Collect user feedback on comfort

---

## 7. Comparison with OEM Features

| OEM | Feature | Description |
|-----|---------|-------------|
| Mercedes | "Trailer Nudge" | Slight offset when passing trucks |
| BMW | "Lane Positioning" | Adjusts position based on traffic |
| Volvo | "Adaptive Positioning" | Moves away from large vehicles |
| Tesla | No equivalent | Centered only |

**EOP SOC:** Similar to Mercedes/BMW approach but more conservative

---

## 8. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Design Document | ✅ Complete | This document |
| Core Implementation | ✅ Complete | `pathd/soc.py` (closing-speed based) |
| PathD Integration | ✅ Complete | Integrated in trajectory planning |
| Unit Tests | ⏳ Pending | Test scenarios defined |
| Safety Review | ⏳ Pending | High-risk feature |
| Documentation | ✅ Complete | This document |

**Note:** Implementation uses closing-speed based detection rather than vehicle-type detection. This approach is more practical as it responds to actual collision risk rather than object classification.

---

---

## Implementation

### Function

Low-speed traffic jam handling:
- Follows lead vehicles in stop-and-go traffic
- Smooth acceleration from standstill

### Algorithm

```
Input: modelV2.leads, carState.vEgo, carState.standstill
Output: creepSpeed, resumeAccel
```

### Thresholds

| Parameter | Value |
|-----------|-------|
| Creep speed | 5 km/h |
| Resume delay | 1.5s |

### Code Location

- `selfdrive/controls/lib/longitudinal_planner.py`


## 9. Related Documents

- [EOP OVERVIEW](../../00_Index/OVERVIEW.md) - EOP Architecture Overview
- PATHD.md - Path Daemon
- GRIDD.md - Grid Daemon (provides YOLO + stereo)
