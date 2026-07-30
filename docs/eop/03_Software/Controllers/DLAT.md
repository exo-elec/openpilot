# Design Document: Dynamic Lateral Profile (DLAT)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/controls/lib/dlat.py` |

---


> **Component Type:** Controller (inside `controlsd`)  
> **Complexity:** High  
> **Reference Implementation:**
> - Sunnypilot `sunnypilot/controls/lib/nnlc/` (Neural Network Lateral Control)
> - FrogPilot conditional logic in `frogpilot/controls/frogpilot_planner.py`
> **EOP Integration:** `selfdrive/controls/lib/dlat.py`

---

## 1. Objective

DLAT provides intelligent arbitration between rule-based lane following (Laneful) and End-to-End (E2E) mapless navigation (Laneless). It ensures the vehicle uses the most reliable lateral prediction for the current environment, eliminating "ping-ponging" on degraded lane lines and enabling confident navigation through complex scenarios like intersections and construction zones.

**Key Benefits:**
- Reduces steering oscillation on faded lane markings
- Enables navigation through intersections without map data
- Automatic mode selection based on model confidence
- Improves driving comfort in challenging road conditions

---

## 2. Technical Architecture

### 2.1 Strategic Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Laneful** | Follows `modelV2.laneLines` | Well-marked highways, clear lane lines |
| **Laneless** | Follows `modelV2.predictedPath` | Urban intersections, construction zones, faded markings |
| **Auto** | Heuristic-based switching | Default mode, dynamically selects best approach |

### 2.2 Auto Switching Logic (Heuristic)

The Auto mode uses a probabilistic approach with hysteresis to prevent mode oscillation.

**Lane Line Confidence Evaluation:**
```python
# Extract lane line probabilities
lane_line_probs = modelV2.laneLineProbs  # 4 lane lines

# Calculate overall lane confidence
# Weight inner lines higher than outer lines
weights = [0.1, 0.4, 0.4, 0.1]  # outer left, inner left, inner right, outer right
lane_confidence = sum(p * w for p, w in zip(lane_line_probs, weights))
```

**Hysteresis Thresholds:**

| Transition | Threshold | Duration | Description |
|------------|-----------|----------|-------------|
| Laneful → Laneless | confidence < 0.4 | > 1.0s | Lane lines degraded |
| Laneless → Laneful | confidence > 0.7 | > 2.0s | Lane lines restored |

**Path Deviation Check:**
```python
# If Laneless path deviates > 1.5m from Laneful path
# AND overall model confidence is high
# → Force Laneless (likely lane shift or construction)

laneful_path = modelV2.laneLines[1]  # left inner
laneless_path = modelV2.predictedPath

deviation = calculate_lateral_deviation(laneful_path, laneless_path)
if deviation > 1.5 and model_confidence > 0.8:
    force_laneless = True
```

### 2.3 State Machine

```
┌─────────────────────────────────────────────────────────────┐
│                         AUTO MODE                           │
└─────────────────────────────────────────────────────────────┘

    ┌───────────┐
    │  LANEFUL  │◄───────────────────────────────┐
    │  (active) │                                │
    └─────┬─────┘                                │
          │                                      │
          │ confidence < 0.4                     │
          │ for > 1.0s                           │
          ▼                                      │
    ┌───────────┐     path deviation > 1.5m      │
    │ EVALUATE  │ ──────────────────────────────►│
    │ (temp)    │     AND model_conf > 0.8       │
    └─────┬─────┘                                │
          │                                      │
          │ confidence stays < 0.4               │
          │ for > 2.0s (total)                   │
          ▼                                      │
    ┌───────────┐                                │
    │ LANELESS  │────────────────────────────────┤
    │  (active) │    confidence > 0.7            │
    └───────────┘    for > 2.0s                  │
```

### 2.4 Model Confidence Calculation

```python
def calculate_model_confidence(model_v2):
    """
    Calculate overall model confidence for path prediction.
    
    Combines multiple factors:
    1. Lane line probabilities
    2. Path prediction standard deviation
    3. Model execution time (inference latency)
    4. Road edge detection
    """
    # Lane line confidence (weighted)
    lane_probs = model_v2.laneLineProbs
    lane_confidence = 0.1*lane_probs[0] + 0.4*lane_probs[1] + \
                     0.4*lane_probs[2] + 0.1*lane_probs[3]
    
    # Path prediction confidence (inverse of std dev)
    if hasattr(model_v2, 'predictedPathStd'):
        path_std = np.mean(model_v2.predictedPathStd)
        path_confidence = 1.0 / (1.0 + path_std)
    else:
        path_confidence = 0.5  # default if not available
        
    # Combined confidence
    model_confidence = 0.6 * lane_confidence + 0.4 * path_confidence
    
    return model_confidence
```

---

## 3. Reference Implementation Analysis

### 3.1 Sunnypilot NNLateralControl (NNLC)

**File:** `sunnypilot/controls/lib/nnlc/nnlc.py` (200+ lines)

**Implementation:**
```python
class NNLateralControl:
    def __init__(self):
        self.nnff_model = load_model()
        self.use_nnlc = params.get_bool("NeuralNetworkLateralControl")
        
    def update(self, CS, VM, desired_curvature, params):
        if self.use_nnlc:
            # Use neural network for feedforward
            features = extract_features(CS, VM)
            nn_output = self.nnff_model.predict(features)
            return blend_with_base_controller(nn_output, params)
        return base_controller_output
```

**Key Features:**
- Neural network feedforward enhancement
- Model-based control smoothing
- Integration with existing torque controller
- Optional enable/disable toggle

**Pros:**
- ✅ **Advanced control** - NN provides smoother steering
- ✅ **Modular design** - Can be enabled/disabled
- ✅ **Well-integrated** - Works with existing controllers
- ✅ **Active learning** - Can improve with more data

**Cons:**
- ❌ **Model dependency** - Requires trained NN model
- ❌ **Complexity** - 200+ lines plus model files
- ❌ **Not mode switching** - Enhances control, doesn't select Laneful/Laneless
- ❌ **Hardware dependent** - NPU/GPU for model inference
- ❌ **Hard to debug** - NN decisions not interpretable

**Verdict:** Wrong focus - NNLC enhances control, DLAT needs to select mode.

---

### 3.2 FrogPilot Conditional Experimental Mode

**File:** `frogpilot/controls/lib/conditional_experimental_mode.py` (150+ lines)

**Implementation:**
```python
class ConditionalExperimental:
    def __init__(self):
        self.curves_enabled = params.get_bool("CECurves")
        self.lead_enabled = params.get_bool("CELead")
        
    def update(self, carState, modelV2, radarState):
        # Simple rule-based switching
        if self.curves_enabled and self.detect_curve(modelV2):
            return True  # Use E2E
        if self.lead_enabled and self.detect_slow_lead(radarState):
            return True  # Use E2E
        return False  # Use ACC
```

**Key Features:**
- Rule-based mode selection
- Multiple triggers (curves, leads, intersections)
- User-configurable triggers
- Simple boolean output

**Pros:**
- ✅ **Simple to understand** - Clear if/then logic
- ✅ **User control** - Toggle individual triggers
- ✅ **Immediate response** - No training or calibration
- ✅ **Well-tested** - Popular FrogPilot feature

**Cons:**
- ❌ **Binary output** - No smooth transitions
- ❌ **No confidence** - Doesn't consider model certainty
- ❌ **Limited inputs** - Only specific triggers
- ❌ **Ping-pong risk** - Can oscillate between modes
- ❌ **No hysteresis** - Immediate switching can be jarring

**Verdict:** Good concept but too simplistic for robust mode switching.

---

### 3.3 Comparison Summary

| Aspect | Sunnypilot NNLC | FrogPilot CEM | **EOP DLAT** |
|--------|-----------------|---------------|--------------|
| **Primary Function** | Control enhancement | Mode trigger | Mode selection |
| **Switching Logic** | N/A | Rule-based | Confidence-based |
| **Transitions** | Smooth (blended) | Abrupt | Smooth (hysteresis) |
| **Complexity** | High | Low | Medium |
| **Confidence Check** | No | No | Yes |
| **Hysteresis** | N/A | No | Yes |

---

### 3.4 EOP Selection Rationale

**EOP Approach:** Confidence-based state machine with hysteresis

**Why Confidence-Based Over Rule-Based?**

1. **Robustness**
   - **FrogPilot Problem:** Rules can be triggered falsely (e.g., shadow detected as curve)
   - **EOP Solution:** Confidence threshold requires sustained low confidence
   - **Evidence:** Highway lane fading - confidence drops gradually, rules may trigger instantly

2. **Smooth Transitions**
   - **FrogPilot Problem:** Immediate switching causes steering jerk
   - **EOP Solution:** Time-based hysteresis (1.0s/2.0s) + smoothing factor
   - **Evidence:** Laneless interpolation factor (0→1) over multiple frames

3. **Flexibility**
   - **FrogPilot Problem:** Fixed rules can't adapt to new scenarios
   - **EOP Solution:** Generic confidence calculation from lane line probs
   - **Evidence:** Works for faded lines, construction, intersections without specific rules

**Why State Machine Over Direct Logic?**

| Approach | Pros | Cons | **EOP Choice** |
|----------|------|------|----------------|
| **Direct (FrogPilot)** | Simple, fast | No history, can oscillate | ❌ |
| **State Machine** | Clear states, hysteresis | More code | ✅ |
| **Fuzzy Logic** | Smooth decisions | Complex tuning | ❌ |

**Key Insight:**
- NNLC is the wrong tool (enhances control, doesn't select mode)
- FrogPilot CEM is too simplistic (rules without confidence)
- **EOP Sweet Spot:** Confidence evaluation + state machine + hysteresis

**Evidence from Research:**
- Sunnypilot's DEC (Dynamic Experimental Control) uses similar confidence-based state machines successfully
- FrogPilot's `human_acceleration` proves users prefer smooth transitions
- Stock openpilot's lane line confidence is reliable for this purpose

---

## 4. EOP Implementation Plan

### 4.1 Files and Classes

| File | Purpose |
|------|---------|
| `selfdrive/controls/lib/dlat.py` | Main DLAT controller |
| `selfdrive/controls/controlsd.py` | Integration point |

### 4.2 Class Structure

```python
# selfdrive/controls/lib/dlat.py

import numpy as np
from enum import Enum
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params


class DLatMode(Enum):
    """DLAT operating modes."""
    LANEFUL = "Laneful"
    LANELESS = "Laneless"
    AUTO = "Auto"


class DLatState(Enum):
    """Internal state machine states."""
    LANEFUL = 0
    EVALUATE = 1
    LANELESS = 2


class DLAT:
    """
    Dynamic Lateral Profile Controller
    
    Manages switching between Laneful (lane line based) and Laneless 
    (E2E predicted path) lateral control modes.
    """
    
    # Hysteresis thresholds
    LANEFUL_TO_LANELESS_THRESHOLD = 0.4
    LANELESS_TO_LANEFUL_THRESHOLD = 0.7
    FORCE_LANELESS_DEVIATION = 1.5  # meters
    
    # Timing hysteresis (seconds)
    LANEFUL_TO_LANELESS_TIME = 1.0
    LANELESS_TO_LANEFUL_TIME = 2.0
    
    def __init__(self):
        self.params = Params()
        self.mode = self._get_initial_mode()
        self.state = DLatState.LANEFUL
        
        # Timing for hysteresis
        self.state_timer = 0.0
        self.last_state_change = 0.0
        
        # Current selections
        self.use_laneless = False
        self.lane_confidence = 0.0
        self.model_confidence = 0.0
        self.path_deviation = 0.0
        
        # Smoothing
        self.laneless_factor = 0.0  # 0 = fully laneful, 1 = fully laneless
        self.smoothing_alpha = 0.1  # Low-pass filter coefficient
        
    def _get_initial_mode(self) -> DLatMode:
        """Get initial mode from params."""
        mode_str = self.params.get("EOPDLATMode", encoding='utf-8')
        try:
            return DLatMode(mode_str)
        except ValueError:
            return DLatMode.AUTO
            
    def calculate_lane_confidence(self, model_v2) -> float:
        """
        Calculate overall lane line confidence.
        
        Weights inner lines higher than outer lines.
        """
        if not hasattr(model_v2, 'laneLineProbs') or len(model_v2.laneLineProbs) < 4:
            return 0.0
            
        probs = model_v2.laneLineProbs
        # Weights: [outer_left, inner_left, inner_right, outer_right]
        weights = [0.1, 0.4, 0.4, 0.1]
        
        confidence = sum(p * w for p, w in zip(probs[:4], weights))
        return float(confidence)
        
    def calculate_path_deviation(self, model_v2) -> float:
        """
        Calculate lateral deviation between laneful and laneless paths.
        
        Returns maximum deviation in meters.
        """
        if not hasattr(model_v2, 'laneLines') or not hasattr(model_v2, 'predictedPath'):
            return 0.0
            
        # Get inner left lane line as laneful reference
        if len(model_v2.laneLines) < 2:
            return 0.0
            
        laneful_path = model_v2.laneLines[1]  # Inner left
        laneless_path = model_v2.predictedPath
        
        # Calculate deviation at each point
        min_len = min(len(laneful_path.x), len(laneless_path.x))
        if min_len == 0:
            return 0.0
            
        deviations = []
        for i in range(min_len):
            # Simple lateral deviation (y-coordinate in path coordinates)
            dev = abs(laneless_path.y[i] - laneful_path.y[i])
            deviations.append(dev)
            
        return float(np.max(deviations)) if deviations else 0.0
        
    def calculate_model_confidence(self, model_v2) -> float:
        """
        Calculate overall model confidence.
        """
        lane_conf = self.calculate_lane_confidence(model_v2)
        
        # Path prediction confidence
        if hasattr(model_v2, 'predictedPathStd'):
            path_std = np.mean(np.abs(model_v2.predictedPathStd))
            path_conf = 1.0 / (1.0 + path_std)
        else:
            path_conf = 0.5
            
        # Combined
        return 0.6 * lane_conf + 0.4 * path_conf
        
    def update_auto_mode(self, model_v2, v_ego: float) -> bool:
        """
        Update Auto mode state machine.
        
        Returns:
            use_laneless: Whether to use laneless mode
        """
        self.lane_confidence = self.calculate_lane_confidence(model_v2)
        self.model_confidence = self.calculate_model_confidence(model_v2)
        self.path_deviation = self.calculate_path_deviation(model_v2)
        
        # State machine transitions
        if self.state == DLatState.LANEFUL:
            # Check for transition to evaluation
            if self.lane_confidence < self.LANEFUL_TO_LANELESS_THRESHOLD:
                self.state_timer += DT_MDL
                if self.state_timer >= self.LANEFUL_TO_LANELESS_TIME:
                    self.state = DLatState.EVALUATE
                    self.state_timer = 0.0
            else:
                self.state_timer = 0.0
                
            # Check for forced laneless due to large deviation
            if (self.path_deviation > self.FORCE_LANELESS_DEVIATION and 
                self.model_confidence > 0.8):
                self.state = DLatState.LANELESS
                self.state_timer = 0.0
                
        elif self.state == DLatState.EVALUATE:
            # Stay in evaluate for additional confirmation
            self.state_timer += DT_MDL
            
            # Confirm transition to laneless
            if (self.lane_confidence < self.LANEFUL_TO_LANELESS_THRESHOLD and
                self.state_timer >= self.LANELESS_TO_LANEFUL_TIME):
                self.state = DLatState.LANELESS
                self.state_timer = 0.0
                
            # Abort transition if confidence returns
            elif self.lane_confidence > self.LANELESS_TO_LANEFUL_THRESHOLD:
                self.state = DLatState.LANEFUL
                self.state_timer = 0.0
                
        elif self.state == DLatState.LANELESS:
            # Check for transition back to laneful
            if self.lane_confidence > self.LANELESS_TO_LANEFUL_THRESHOLD:
                self.state_timer += DT_MDL
                if self.state_timer >= self.LANELESS_TO_LANEFUL_TIME:
                    self.state = DLatState.LANEFUL
                    self.state_timer = 0.0
            else:
                self.state_timer = 0.0
                
        return self.state == DLatState.LANELESS
        
    def get_desired_curvature(self, model_v2) -> float:
        """
        Get desired curvature based on current mode.
        
        Returns:
            curvature: Desired path curvature (1/m)
        """
        if not hasattr(model_v2, 'predictedPath'):
            return 0.0
            
        if self.use_laneless or self.mode == DLatMode.LANELESS:
            # Use E2E predicted path curvature
            if hasattr(model_v2.predictedPath, 'curvature'):
                return float(np.clip(model_v2.predictedPath.curvature, -0.1, 0.1))
            else:
                # Calculate from path points
                return self._calculate_curvature_from_path(model_v2.predictedPath)
        else:
            # Use lane line based curvature
            if len(model_v2.laneLines) >= 2:
                # Average of inner lane lines
                left_curv = model_v2.laneLines[1].curvature if hasattr(model_v2.laneLines[1], 'curvature') else 0
                right_curv = model_v2.laneLines[2].curvature if len(model_v2.laneLines) > 2 and hasattr(model_v2.laneLines[2], 'curvature') else 0
                return float((left_curv + right_curv) / 2)
                
        return 0.0
        
    def _calculate_curvature_from_path(self, path) -> float:
        """Calculate curvature from path points."""
        if not hasattr(path, 'x') or len(path.x) < 3:
            return 0.0
            
        # Use first few points for immediate curvature
        x = np.array(path.x[:5])
        y = np.array(path.y[:5])
        
        # Simple curvature approximation
        # κ = |y''| / (1 + y'^2)^(3/2)
        if len(x) >= 3:
            dy = np.gradient(y, x)
            d2y = np.gradient(dy, x)
            
            curvature = np.abs(d2y[0]) / (1 + dy[0]**2)**(3/2)
            return float(np.clip(curvature, -0.1, 0.1))
            
        return 0.0
        
    def update(self, model_v2, v_ego: float) -> dict:
        """
        Main update method.
        
        Returns:
            Dict with mode, use_laneless, curvature, confidences
        """
        # Update mode from params periodically
        if int(v_ego * 100) % 10 == 0:  # Every ~100ms
            new_mode = self._get_initial_mode()
            if new_mode != self.mode:
                self.mode = new_mode
                # Reset state on mode change
                self.state = DLatState.LANELESS if new_mode == DLatMode.LANELESS else DLatState.LANEFUL
                
        # Determine use_laneless
        if self.mode == DLatMode.LANEFUL:
            self.use_laneless = False
        elif self.mode == DLatMode.LANELESS:
            self.use_laneless = True
        else:  # AUTO
            self.use_laneless = self.update_auto_mode(model_v2, v_ego)
            
        # Smooth transition factor
        target_factor = 1.0 if self.use_laneless else 0.0
        self.laneless_factor += self.smoothing_alpha * (target_factor - self.laneless_factor)
        
        # Get curvature
        curvature = self.get_desired_curvature(model_v2)
        
        return {
            'mode': self.mode.value,
            'use_laneless': self.use_laneless,
            'laneless_factor': self.laneless_factor,
            'curvature': curvature,
            'lane_confidence': self.lane_confidence,
            'model_confidence': self.model_confidence,
            'path_deviation': self.path_deviation,
            'state': self.state.name
        }
```

### 4.3 Integration in controlsd.py

```python
# selfdrive/controls/controlsd.py

from openpilot.selfdrive.controls.lib.dlat import DLAT, DLatMode

class ControlsD:
    def __init__(self):
        # ... existing init ...
        self.dlat_controller = DLAT()
        
    def update(self):
        # ... existing update logic ...
        
        # Update DLAT
        dlat_output = self.dlat_controller.update(
            model_v2=sm['modelV2'],
            v_ego=CS.vEgo
        )
        
        # Get desired curvature for lateral control
        desired_curvature = dlat_output['curvature']
        
        # Apply to lateral controller
        # lat_controller.update(desired_curvature, ...)
        
        # Log DLAT state
        # self.pm.send('dlatState', ...)
```

### 4.4 Parameter Management

**EOP Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPDLATMode` | String | `"Auto"` | Mode: "Auto", "Laneful", "Laneless" |
| `EOPDLPCurvesEnabled` | Bool | `0` | Allow Auto to switch to Laneless for tight curves |

---

## 5. Safety Analysis

### 5.1 Safety Mechanisms

| Mechanism | Implementation |
|-----------|----------------|
| **Hysteresis** | Time-based and threshold-based to prevent oscillation |
| **Smooth Transition** | Low-pass filter on laneless_factor (0→1) |
| **Confidence Gating** | Only switch if model confidence > 0.8 |
| **Fallback** | Always fallback to Laneful if modelV2 invalid |
| **User Override** | Manual mode selection overrides Auto |

### 5.2 Risk Assessment

| Risk | Mitigation | Likelihood | Severity |
|------|------------|------------|----------|
| Mode oscillation | Hysteresis + smoothing | Low | Low |
| Wrong mode selection | Confidence thresholds, user override | Low | Medium |
| Control discontinuity | Smooth interpolation | Very Low | Medium |
| Model failure | Fallback to Laneful | Very Low | High |

---

## 6. Testing Plan

### 6.1 Unit Tests

```python
# selfdrive/controls/lib/tests/test_eop_dlat.py

def test_lane_confidence_calculation():
    dlat = DLAT()
    # Test with known lane line probabilities
    
def test_hysteresis_transitions():
    dlat = DLAT()
    # Verify timing requirements for state transitions
    
def test_path_deviation():
    dlat = DLAT()
    # Test deviation calculation between paths
    
def test_forced_laneless():
    dlat = DLAT()
    # Verify large deviation forces laneless mode
```

### 6.2 Integration Tests

- Highway driving (should prefer Laneful)
- Urban intersections (should switch to Laneless)
- Construction zones (lane shifts)
- Faded lane markings

### 6.3 Real-World Validation

- Minimum 500km mixed driving
- Log mode switches and confidence values
- Validate no "ping-ponging"

---

## 7. Comparison with Reference Forks

| Aspect | FrogPilot | Sunnypilot | EOP Proposal |
|--------|-----------|------------|--------------|
| **Mode Selection** | Conditional rules | NN enhancement | **Heuristic state machine** |
| **Auto Logic** | Curve/intersection based | Model-based | **Confidence + deviation** |
| **Transitions** | Immediate | Smooth | **Hysteresis + smoothing** |
| **Complexity** | Low | High | **Medium** |

**EOP Differentiation:**
- Focus on the Laneful/Laneless decision (not control enhancement like NNLateralControl)
- More sophisticated than FrogPilot's conditional logic
- Simpler than full NNLateralControl

---

## 8. Integration Points

### 8.1 Data Flow

```
modeld (modelV2.laneLines, modelV2.predictedPath)
    ↓
DLAT.update()
    ↓
    ├─→ calculate_lane_confidence()
    ├─→ calculate_path_deviation()
    ├─→ update_auto_mode()
    └─→ get_desired_curvature()
    ↓
controlsd (select curvature for lateral controller)
    ↓
latcontrol (LQR/Torque/PID controller)
    ↓
CarController
```

---

## 9. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Design Document | ✅ Complete | This document |
| State Machine Design | ✅ Complete | 3-state with hysteresis |
| Parameter Definition | ✅ Complete | `EOPDLATMode`, `EOPDLPCurvesEnabled` |
| Core Implementation | ✅ Complete | `selfdrive/controls/lib/dlat.py` |
| controlsd Integration | ✅ Complete | Laneful/Laneless switching |
| Unit Tests | ⏳ Pending | Test scenarios defined |
| Documentation | ✅ Complete | This document |

---

---

## Implementation

### Function

Deep learning-based lateral control:
- Neural network predicts desired curvature
- Augments traditional PID control

### Algorithm

```
Input: modelV2.path, modelV2.laneLines, carState.vEgo
Output: desiredCurvature, laneChange
```

### Model

| Model | Input | Output |
|-------|-------|--------|
| driving_policy | Camera + kinematics | Steering angle + curvature |

### Code Location

- `selfdrive/controls/lib/lateral_planner.py` *(not implemented)*
- `selfdrive/controls/lib/lane_planner.py`


## 10. Related Documents

- [EOP OVERVIEW](../../00_Index/OVERVIEW.md) - EOP Architecture Overview
- [DLON.md](./DLON.md) - Dynamic Longitudinal Profile (complementary feature)
- Sunnypilot nnlc - Reference implementation
