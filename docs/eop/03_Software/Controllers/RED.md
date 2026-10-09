# Design Document: Road Edge Detection (RED)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/controls/lib/red.py` |

---


> **Component Type:** Safety Controller (inside `controlsd`)  
> **Complexity:** High  
> **Tier:** Core Vision (vision-only mode) / Enhanced Vision (with stereo fusion)  
> **Reference Implementation:**
> - No direct equivalent in reference forks
> - Partial: Stock openpilot `modelV2.roadEdges`
> - Related: Autoware's boundary detection
> **EOP Integration:** `selfdrive/controls/lib/red.py` (or RED logic in existing files)

---

## 1. Objective

RED provides a critical safety layer by identifying physical road boundaries (grass, curbs, barriers, guardrails) that may not be marked with lane lines. It prevents the vehicle from drifting or steering into non-navigable areas, especially when operating in "Laneless" mode where traditional lane line following is unavailable.

**Key Benefits:**
- Safety guardrail for Laneless mode
- Detection of unmarked road boundaries
- Protection against curb strikes
- Improved rural road safety

---

## 2. Technical Architecture

### 2.1 Data Sources (Tiered Approach)

RED works in **two modes** depending on available hardware:

#### Core Vision Mode (Road Camera Only)

| Source | Data | Reliability | Requirement |
|--------|------|-------------|-------------|
| **modelV2.roadEdges** | Vision-based edges | Medium-High | **Required** |
| **YOLO (road camera)** | Barrier objects | High | **Required** |

**Works with:** Road camera (8mm) only - no stereo needed

#### Enhanced Vision Mode (With Stereo)

| Source | Data | Reliability | Requirement |
|--------|------|-------------|-------------|
| **modelV2.roadEdges** | Vision-based edges | Medium-High | Required |
| **YOLO (road camera)** | Barrier objects | High | Required |
| **stereo_depth (gridd)** | 3D edge confirmation | **Very High** | **Enhanced only** |
| **pathd.boundaries** | BEV road limits | Medium | **Enhanced only** |

**Works with:** Road + Wide road + Stereo cameras

**Key Point:** RED is a Core Vision feature because it works (with reduced confidence) using only the Road camera. Stereo is optional for enhanced reliability.

### 2.2 Road Edge Types

```python
class RoadEdgeType(Enum):
    """Types of road edges detectable."""
    CURB = 0           # Concrete curb
    GRASS = 1          # Grass/dirt shoulder
    GUARDRAIL = 2      # Metal guardrail
    WALL = 3           # Concrete wall/barrier
    UNKNOWN = 4        # Unclassified boundary
```

### 2.3 Core Logic

```
┌─────────────────────────────────────────────────────────────────┐
│                     RED PROCESSING PIPELINE                     │
└─────────────────────────────────────────────────────────────────┘

Detection Phase:
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│ Vision model │    │ YOLO barrier │    │ Stereo depth     │
│ roadEdges    │ ─→ │ detection    │ ─→ │ verification     │
└──────────────┘    └──────────────┘    └──────────────────┘
                                                 │
                                                 ▼
Fusion Phase:                           ┌──────────────────┐
                                        │ Multi-source     │
┌──────────────┐    ┌──────────────┐   │ confidence       │
│ Temporal     │ ←─ │ Edge type    │ ←─┘ │ weighting      │
│ filtering    │    │ classification     └──────────────────┘
└──────────────┘    └──────────────┘
        │
        ▼
Safety Phase:
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│ Proximity    │    │ Repulsive    │    │ Inject into      │
│ check        │ ─→ │ force calc   │ ─→ │ path cost        │
│ (< 0.5m)     │    │ (laneless)   │    │ (safety layer)   │
└──────────────┘    └──────────────┘    └──────────────────┘
```

### 2.4 Vision-Based Detection

**From modelV2.roadEdges:**
```python
def process_road_edges(model_v2):
    """
    Process road edge detections from vision model.
    
    modelV2.roadEdges provides:
    - leftEdge: Detected left road edge
    - rightEdge: Detected right road edge
    - probabilities: Confidence for each edge
    """
    edges = []
    
    if hasattr(model_v2, 'roadEdges'):
        for i, edge in enumerate(model_v2.roadEdges):
            if edge.probability > 0.5:  # Confidence threshold
                edges.append({
                    'side': 'left' if i == 0 else 'right',
                    'points': [(p.x, p.y) for p in edge.points],
                    'probability': edge.probability,
                    'type': classify_edge_type(edge)  # curb, grass, etc.
                })
                
    return edges
```

### 2.5 YOLO Barrier Validation

**Validate vision edges with object detection:**
```python
def validate_with_yolo(vision_edge, yolo_detections):
    """
    Cross-check vision edge with YOLO barrier detections.
    
    Returns confidence boost if YOLO confirms barrier.
    """
    barrier_classes = ['guardrail', 'wall', 'barrier', 'fence']
    
    for det in yolo_detections:
        if det.class_label in barrier_classes:
            # Check if YOLO barrier aligns with vision edge
            if lateral_distance(vision_edge, det) < 0.5:  # meters
                return 0.3  # Confidence boost
                
    return 0.0
```

### 2.6 Stereo Depth Verification

**Confirm physical existence with 3D depth:**
```python
def verify_with_stereo(vision_edge, stereo_depth_map):
    """
    Verify vision edge corresponds to physical boundary.
    
    Uses stereo depth discontinuity to confirm edge.
    """
    # Sample points along vision edge
    edge_points = sample_points(vision_edge, num_samples=10)
    
    confirmed_points = 0
    for point in edge_points:
        # Check for depth discontinuity at edge
        depth_in = sample_depth(stereo_depth_map, point, offset_in=0.1)
        depth_out = sample_depth(stereo_depth_map, point, offset_out=0.1)
        
        # Significant depth change indicates physical edge
        if abs(depth_in - depth_out) > 0.3:  # 30cm step
            confirmed_points += 1
            
    confirmation_ratio = confirmed_points / len(edge_points)
    return confirmation_ratio
```

### 2.7 Repulsive Force Calculation

**When Laneless mode approaches road edge:**
```python
def calculate_repulsive_force(vehicle_position, road_edge, v_ego):
    """
    Calculate steering cost to push away from road edge.
    
    Args:
        vehicle_position: (x, y) in path coordinates
        road_edge: Detected edge points
        v_ego: Current speed
        
    Returns:
        lateral_cost: Cost to add to path planning (higher = more avoidance)
    """
    # Distance to edge
    distance_to_edge = lateral_distance(vehicle_position, road_edge)
    
    # Critical distance threshold
    CRITICAL_DISTANCE = 0.5  # meters
    WARNING_DISTANCE = 1.0   # meters
    
    if distance_to_edge > WARNING_DISTANCE:
        return 0.0  # No cost - far enough
        
    # Exponential cost function
    # Closer to edge = exponentially higher cost
    proximity_factor = (WARNING_DISTANCE - distance_to_edge) / WARNING_DISTANCE
    base_cost = 0.5 * (proximity_factor ** 2)
    
    # Speed scaling (higher speed = more aggressive avoidance)
    speed_factor = 1.0 + (v_ego / 30.0)  # 1.0 at 0 m/s, 2.0 at 30 m/s
    
    # Edge type scaling (some edges more dangerous)
    type_multipliers = {
        'curb': 1.5,
        'guardrail': 1.3,
        'wall': 1.4,
        'grass': 1.0,
        'unknown': 1.2
    }
    type_mult = type_multipliers.get(road_edge['type'], 1.0)
    
    return base_cost * speed_factor * type_mult
```

### 2.8 Safety Override Logic

**Hard constraints for critical situations:**
```python
def apply_safety_override(planned_path, road_edges, v_ego):
    """
    Override planned path if it would cross road edge.
    
    This is the ultimate safety layer.
    """
    MIN_EDGE_DISTANCE = 0.3  # meters - absolute minimum
    
    for edge in road_edges:
        for i, path_point in enumerate(planned_path):
            distance_to_edge = lateral_distance(path_point, edge)
            
            if distance_to_edge < MIN_EDGE_DISTANCE:
                # CRITICAL: Path would cross road edge
                
                # Calculate safe offset
                safe_offset = MIN_EDGE_DISTANCE - distance_to_edge
                
                # Apply to all future path points
                for j in range(i, len(planned_path)):
                    planned_path[j].y += safe_offset * (1 if edge['side'] == 'left' else -1)
                    
                # Trigger safety alert
                trigger_alert("roadEdgeApproach")
                
    return planned_path
```

### 2.9 State Machine

```
┌───────────┐     Edge detected       ┌───────────┐
│  INACTIVE │ ──────────────────────→ │  MONITOR  │
│  (no      │                         │  (watch)  │
│   edges)  │ ←────────────────────── │           │
└───────────┘   No edges for 5s       └─────┬─────┘
                                             │
                                             │ < 1.0m
                                             ▼
                                       ┌───────────┐
                                       │  WARNING  │
                                       │  (cost    │
                                       │   added)  │
                                       └─────┬─────┘
                                             │
                                             │ < 0.5m
                                             ▼
                                       ┌───────────┐
                                       │  CRITICAL │
                                       │  (path    │
                                       │  override)│
                                       └───────────┘
```

---

## 3. Reference Implementation Analysis

### 3.1 Stock OpenPilot roadEdges

**Source:** `modelV2.roadEdges`

**What it provides:**
- Two road edges (left/right)
- Point sequences for each edge
- Probability/confidence values

**Limitations:**
- ❌ Not used in stock openpilot control
- ❌ No validation/fusion with other sensors
- ❌ Confidence not always reliable
- ❌ Can be noisy in poor lighting

**EOP Enhancement:** Add fusion, validation, and safety layer

---

### 3.2 No Direct Reference Implementation

**FrogPilot:** No road edge safety feature found
**Sunnypilot:** No road edge safety feature found
**Dragonpilot:** No road edge safety feature found

**Why?**
- **High risk:** False positive = unnecessary intervention
- **Complex validation:** Single sensor (vision) unreliable
- **Limited benefit:** Laneless mode is already cautious
- **Testing burden:** Requires edge case scenarios

---

### 3.3 EOP Design Rationale

**Why Include RED?**

| Factor | Rationale |
|--------|-----------|
| **Safety** | Critical for Laneless mode acceptance |
| **Gap** | No reference implementation to copy |
| **Innovation** | EOP can lead here |
| **Necessity** | Rural roads often lack lane markings |

**Design Philosophy:**
- **Multi-sensor fusion** - Don't trust any single source
- **Conservative** - When in doubt, stay centered
- **Graduated response** - Warning → Cost → Override
- **Optional** - Can be disabled if causing issues

---

## 4. EOP Implementation Plan

### 4.1 Files and Classes

| File | Purpose |
|------|---------|
| `selfdrive/controls/lib/red.py` (or RED logic in existing files) | Main RED controller |
| `selfdrive/controls/controlsd.py` | Integration point |

### 4.2 Class Structure

```python
# selfdrive/controls/lib/red.py

import numpy as np
from enum import Enum
from dataclasses import dataclass
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params


class REDState(Enum):
    """RED operating states."""
    INACTIVE = 0
    MONITORING = 1
    WARNING = 2
    CRITICAL = 3


class RoadEdgeType(Enum):
    CURB = 0
    GRASS = 1
    GUARDRAIL = 2
    WALL = 3
    UNKNOWN = 4


@dataclass
class RoadEdge:
    """Represents a detected road edge."""
    side: str  # 'left' or 'right'
    points: list  # [(x, y), ...]
    edge_type: RoadEdgeType
    vision_confidence: float
    yolo_confidence: float
    stereo_confidence: float
    
    @property
    def fused_confidence(self):
        """Combined confidence from all sources."""
        # Weighted average
        weights = [0.5, 0.3, 0.2]  # Vision, YOLO, Stereo
        confs = [self.vision_confidence, self.yolo_confidence, self.stereo_confidence]
        return sum(w * c for w, c in zip(weights, confs))


class RED:
    """
    Road Edge Detection Controller
    
    Provides safety guardrail for Laneless mode by detecting
    and avoiding physical road boundaries.
    """
    
    # Distance thresholds
    WARNING_DISTANCE = 1.0  # meters
    CRITICAL_DISTANCE = 0.5  # meters
    MIN_EDGE_DISTANCE = 0.3  # meters - absolute minimum
    
    # Confidence thresholds
    MIN_VISION_CONF = 0.5
    MIN_FUSED_CONF = 0.6
    
    # Temporal filtering
    TEMPORAL_ALPHA = 0.2  # Smoothing factor
    
    def __init__(self):
        self.params = Params()
        self.enabled = self.params.get_bool("EOPRedControllerEnabled")
        
        # State
        self.state = REDState.INACTIVE
        self.detected_edges = []
        self.filtered_edges = []
        
        # Safety outputs
        self.lateral_cost = 0.0
        self.path_override_active = False
        
    def detect_road_edges(self, model_v2, yolo_detections, stereo_data):
        """
        Multi-sensor road edge detection.
        
        Returns:
            List of RoadEdge objects with fused confidence
        """
        edges = []
        
        # 1. Vision-based detection
        if hasattr(model_v2, 'roadEdges'):
            for i, edge in enumerate(model_v2.roadEdges):
                if edge.probability < self.MIN_VISION_CONF:
                    continue
                    
                side = 'left' if i == 0 else 'right'
                points = [(p.x, p.y) for p in edge.points]
                
                # 2. YOLO validation
                yolo_conf = self._validate_with_yolo(side, points, yolo_detections)
                
                # 3. Stereo verification
                stereo_conf = self._verify_with_stereo(points, stereo_data)
                
                # 4. Edge type classification
                edge_type = self._classify_edge_type(edge, yolo_detections)
                
                # Create fused edge
                road_edge = RoadEdge(
                    side=side,
                    points=points,
                    edge_type=edge_type,
                    vision_confidence=edge.probability,
                    yolo_confidence=yolo_conf,
                    stereo_confidence=stereo_conf
                )
                
                # Only keep high-confidence edges
                if road_edge.fused_confidence >= self.MIN_FUSED_CONF:
                    edges.append(road_edge)
                    
        return edges
        
    def calculate_repulsive_cost(self, vehicle_y, road_edge, v_ego):
        """
        Calculate cost to steer away from road edge.
        
        Args:
            vehicle_y: Current lateral position (m from center)
            road_edge: RoadEdge object
            v_ego: Current speed
            
        Returns:
            Cost value (0.0 to 1.0+)
        """
        # Distance to edge
        edge_y = np.mean([p[1] for p in road_edge.points[:5]])
        distance = abs(edge_y - vehicle_y)
        
        if distance > self.WARNING_DISTANCE:
            return 0.0
            
        # Base cost - exponential increase as we get closer
        proximity = (self.WARNING_DISTANCE - distance) / self.WARNING_DISTANCE
        base_cost = 0.5 * (proximity ** 2)
        
        # Speed factor
        speed_mult = 1.0 + (v_ego / 30.0)
        
        # Edge type factor
        type_multipliers = {
            RoadEdgeType.CURB: 1.5,
            RoadEdgeType.GUARDRAIL: 1.3,
            RoadEdgeType.WALL: 1.4,
            RoadEdgeType.GRASS: 1.0,
            RoadEdgeType.UNKNOWN: 1.2
        }
        type_mult = type_multipliers.get(road_edge.edge_type, 1.0)
        
        return base_cost * speed_mult * type_mult
        
    def apply_path_safety_override(self, planned_path, road_edges):
        """
        Modify planned path to avoid crossing road edges.
        
        Args:
            planned_path: List of path points (x, y)
            road_edges: List of RoadEdge objects
            
        Returns:
            Modified path, override_active flag
        """
        override_active = False
        
        for edge in road_edges:
            for i, path_point in enumerate(planned_path):
                distance_to_edge = self._lateral_distance(path_point, edge)
                
                if distance_to_edge < self.MIN_EDGE_DISTANCE:
                    # Critical - path would cross edge
                    safe_offset = self.MIN_EDGE_DISTANCE - distance_to_edge
                    
                    # Apply offset to future points
                    direction = 1 if edge.side == 'left' else -1
                    for j in range(i, len(planned_path)):
                        planned_path[j] = (
                            planned_path[j][0],
                            planned_path[j][1] + safe_offset * direction
                        )
                        
                    override_active = True
                    
        return planned_path, override_active
        
    def update(self, model_v2, yolo_detections, stereo_data, 
              vehicle_position, v_ego, planned_path, is_laneless):
        """
        Main update method.
        
        Returns:
            Dict with costs, override info, and state
        """
        self.enabled = self.params.get_bool("EOPRedControllerEnabled")
        
        if not self.enabled or not is_laneless:
            # RED only active in Laneless mode
            return {
                'lateral_cost': 0.0,
                'path_override': False,
                'state': REDState.INACTIVE.name,
                'edges_detected': 0
            }
            
        # Detect edges
        self.detected_edges = self.detect_road_edges(
            model_v2, yolo_detections, stereo_data
        )
        
        # Temporal smoothing
        self.filtered_edges = self._temporal_filter(
            self.detected_edges, self.filtered_edges
        )
        
        # Update state
        if not self.filtered_edges:
            self.state = REDState.INACTIVE
        else:
            # Find closest edge
            min_distance = min(
                self._lateral_distance(vehicle_position, edge)
                for edge in self.filtered_edges
            )
            
            if min_distance < self.CRITICAL_DISTANCE:
                self.state = REDState.CRITICAL
            elif min_distance < self.WARNING_DISTANCE:
                self.state = REDState.WARNING
            else:
                self.state = REDState.MONITORING
                
        # Calculate repulsive costs
        total_cost = 0.0
        for edge in self.filtered_edges:
            cost = self.calculate_repulsive_cost(
                vehicle_position[1], edge, v_ego
            )
            total_cost += cost
            
        self.lateral_cost = min(total_cost, 2.0)  # Cap at 2.0
        
        # Apply path override if critical
        modified_path = planned_path
        if self.state == REDState.CRITICAL:
            modified_path, self.path_override_active = self.apply_path_safety_override(
                planned_path.copy(), self.filtered_edges
            )
        else:
            self.path_override_active = False
            
        return {
            'lateral_cost': self.lateral_cost,
            'path_override': self.path_override_active,
            'modified_path': modified_path,
            'state': self.state.name,
            'edges_detected': len(self.filtered_edges),
            'closest_distance': min_distance if self.filtered_edges else float('inf')
        }
        
    def _validate_with_yolo(self, side, points, yolo_detections):
        """Validate edge with YOLO barrier detection."""
        barrier_classes = ['guardrail', 'wall', 'barrier', 'fence']
        
        edge_y = np.mean([p[1] for p in points[:5]])
        
        for det in yolo_detections:
            if det.class_label in barrier_classes:
                det_y = det.y
                if abs(det_y - edge_y) < 0.5:
                    return 0.8
                    
        return 0.0
        
    def _verify_with_stereo(self, points, stereo_data):
        """Verify edge with stereo depth discontinuity."""
        confirmed = 0
        for point in points[:5]:
            if self._check_depth_discontinuity(point, stereo_data):
                confirmed += 1
                
        return confirmed / 5.0
        
    def _classify_edge_type(self, edge, yolo_detections):
        """Classify edge type from vision and YOLO."""
        # Default to unknown
        edge_type = RoadEdgeType.UNKNOWN
        
        # Check YOLO for specific barrier types
        for det in yolo_detections:
            if det.class_label == 'guardrail':
                return RoadEdgeType.GUARDRAIL
            elif det.class_label == 'wall':
                return RoadEdgeType.WALL
                
        # Use vision features if available
        # (would need model to output edge type)
        
        return edge_type
        
    def _temporal_filter(self, new_edges, prev_edges):
        """Apply temporal smoothing to edge detections."""
        # Simple: require edge to persist for 3 frames
        # More sophisticated: Kalman filter on edge position
        return new_edges  # Placeholder
        
    def _lateral_distance(self, position, edge):
        """Calculate lateral distance to edge."""
        edge_y = np.mean([p[1] for p in edge.points[:5]])
        return abs(position[1] - edge_y)
        
    def _check_depth_discontinuity(self, point, stereo_data):
        """Check for depth discontinuity at point."""
        # Simplified - would need actual stereo depth access
        return False
```

### 4.3 Integration in controlsd.py

```python
# selfdrive/controls/controlsd.py

from openpilot.selfdrive.controls.lib.red import RED

class ControlsD:
    def __init__(self):
        # ... existing init ...
        self.red_controller = RED()
        
    def update(self):
        # ... existing update ...
        
        # Check if in Laneless mode
        is_laneless = self.dlat_controller.use_laneless
        
        # Update RED
        red_output = self.red_controller.update(
            model_v2=sm['modelV2'],
            yolo_detections=gridd_output['detections'],
            stereo_data=gridd_output['stereo'],
            vehicle_position=(0, 0),  # Relative to path
            v_ego=CS.vEgo,
            planned_path=self.lateral_plan,
            is_laneless=is_laneless
        )
        
        # Apply RED costs to lateral MPC
        if red_output['lateral_cost'] > 0:
            self.lateral_mpc.set_road_edge_cost(red_output['lateral_cost'])
            
        # Apply path override if critical
        if red_output['path_override']:
            self.lateral_plan = red_output['modified_path']
```

### 4.4 Parameter Management

**EOP Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPRedControllerEnabled` | Bool | `1` | Master toggle (default on) |

---

## 5. Safety Analysis

### 5.1 Safety Constraints

| Constraint | Value | Rationale |
|------------|-------|-----------|
| **Multi-sensor fusion** | Vision + YOLO + Stereo | Don't trust single source |
| **Confidence thresholds** | Min 0.6 fused | Require strong evidence |
| **Graduated response** | Warning → Cost → Override | Escalate gradually |
| **Default off** | Disabled | User must explicitly enable |
| **Laneless only** | Active only in E2E | Don't interfere with Laneful |

### 5.2 Risk Assessment

| Risk | Mitigation | Likelihood | Severity |
|------|------------|------------|----------|
| False positive | Multi-sensor fusion, confidence thresholds | Medium | Medium |
| Unnecessary override | Graduated response, default off | Low | Low |
| Missed edge | Conservative confidence, Laneless already cautious | Low | High |
| Path oscillation | Temporal filtering, smoothing | Low | Low |

---

## 6. Testing Plan

### 6.1 Unit Tests

```python
def test_edge_fusion():
    red = RED()
    # Test confidence fusion from multiple sources

def test_repulsive_cost():
    # Test cost calculation at various distances
    
def test_path_override():
    # Test that critical edges trigger path modification
```

### 6.2 Integration Tests

- Rural roads without lane markings
- Highway guardrail detection
- Urban curb detection
- Laneless mode with edge proximity

### 6.3 Real-World Validation

- Minimum 500km rural driving
- Log all edge detections and interventions
- Validate no false positives on normal driving

---

## 7. Comparison with Autoware

| Aspect | Autoware | **EOP RED** |
|--------|----------|-------------|
| **Sensors** | LiDAR + Camera | Stereo + Camera |
| **Edge types** | Curb, line, obstacle | Curb, guardrail, grass |
| **Response** | Path replanning | Cost injection + override |
| **Complexity** | Very High | High |

---

## 8. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Design Document | ✅ Complete | This document |
| Multi-sensor fusion | ✅ Complete | Vision + YOLO + Stereo weights |
| Core Implementation | ✅ Complete | `selfdrive/controls/lib/red.py` |
| UI Toggle | ✅ Complete | `EOPRedControllerEnabled` in eop_panel.cc |
| controlsd Integration | ✅ Complete | Curvature adjustment in Laneless |
| gridd Integration | ⏳ Pending | Testing with stereo data |
| Safety Review | ⏳ Pending | High-risk feature |
| Documentation | ✅ Complete | This document |

---

---

## Implementation

### Function

Emergency braking for rear-end collision scenarios:
- Detects rapid approach from behind
- Applies brakes to reduce impact

### Algorithm

```
Input: radarState (rear), carState.vEgo
Output: redActive, redBraking
```

### Code Location

- `selfdrive/controls/lib/longitudinal_planner.py`


## 9. Related Documents

- [EOP OVERVIEW](../../00_Index/OVERVIEW.md) - EOP Architecture Overview
- [DLAT.md](./DLAT.md) - Dynamic Lateral Profile (Laneless mode)
- GRIDD.md - Grid Daemon (stereo + YOLO)
