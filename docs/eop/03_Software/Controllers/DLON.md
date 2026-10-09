# Design Document: Dynamic Longitudinal Profile (DLON)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/controls/lib/dlon.py` |

---


> **Component Type:** Controller (inside `longitudinal_planner.py`/`controlsd.py`)  
> **Complexity:** High  
> **Reference Implementation:**
> - Sunnypilot `sunnypilot/controls/lib/dec/dec.py` (388 lines) - **Primary Reference**
> - FrogPilot `frogpilot/controls/lib/conditional_experimental_mode.py` (150+ lines)
> **EOP Integration:** `selfdrive/controls/lib/dlon.py`

---

## 1. Objective

DLON manages the longitudinal acceleration and braking strategy, seamlessly switching between comfortable highway cruising ("Chill") and intelligent, mapless urban driving ("Experimental" / E2E). It dynamically selects the best control mode based on real-time environmental triggers.

**Key Benefits:**
- Automatic E2E engagement for traffic lights and stop signs
- Smooth highway cruising without unnecessary E2E interventions
- Context-aware decision making
- Improved urban driving experience

---

## 2. Technical Architecture

### 2.1 Strategic Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Chill** | Standard ACC | Highway cruising, clear lane, no stops |
| **Experimental (E2E)** | End-to-End control | Urban driving, traffic lights, intersections |
| **Auto** | Dynamic switching | Default mode, selects best approach automatically |

### 2.2 Auto Switching Triggers (Contextual E2E)

The system switches to **Experimental** if any of the following are true:

| Trigger | Condition | Priority |
|---------|-----------|----------|
| **Traffic Control** | Vision model detects red/yellow traffic light or stop sign | High |
| **Slower/Stopped Lead** | Lead vehicle significantly slower or stopped ahead | High |
| **Low Speed Navigation** | Vehicle speed < 20 km/h with no lead (empty intersection) | Medium |
| **Turn Intent** | Turn signal activated at non-highway speeds | Medium |
| **Curve Approach** | Sharp curve detected ahead (complements VTSC) | Low |

### 2.3 State Machine with Mode Transition Manager

Following Sunnypilot's proven DEC pattern:

```
┌─────────────────────────────────────────────────────────────────┐
│                     MODE TRANSITION MANAGER                     │
└─────────────────────────────────────────────────────────────────┘

    ┌───────────┐
    │   CHILL   │◄──────────────────────────────────────────────┐
    │   (ACC)   │                                                │
    └─────┬─────┘                                                │
          │                                                      │
          │ Trigger detected (e.g., traffic light)               │
          │ Confidence > 0.6                                       │
          ▼                                                      │
    ┌───────────┐     Low confidence / No triggers    ┌───────────┤
    │ EVALUATE  │ ───────────────────────────────────►│   CHILL   │
    │  (temp)   │     (return to chill)                          │
    └─────┬─────┘                                                │
          │                                                      │
          │ Confidence sustained > 0.6                           │
          │ for > 1.0s                                           │
          ▼                                                      │
    ┌───────────┐     No triggers for > 5s          ┌───────────┘
    │    E2E    │ ─────────────────────────────────►│
    │ (Blended) │     (return to chill)                         │
    └───────────┘                                                │
          │                                                      │
          │ Emergency trigger (MPC FCW)                          │
          ▼                                                      │
    ┌───────────┐                                                │
    │ EMERGENCY │───────────────────────────────────────────────►│
    │   (E2E)   │   (return to E2E after FCW clears)             │
    └───────────┘
```

### 2.4 Smooth Kalman Filter for Decision Stability

```python
class SmoothKalmanFilter:
    """Enhanced Kalman filter with smoothing for stable decision making."""
    
    def __init__(self, measurement_noise=0.1, process_noise=0.01, 
                 smoothing_factor=0.85):
        self.R = measurement_noise
        self.Q = process_noise
        self.smoothing_factor = smoothing_factor
        self.x = 0.0  # Estimated value
        self.P = 1.0  # Error covariance
        self.confidence = 0.0
        
    def add_data(self, measurement):
        # Prediction
        self.P = self.P + self.Q
        
        # Update
        K = self.P / (self.P + self.R)
        effective_K = K * (1.0 - self.smoothing_factor) + self.smoothing_factor * 0.1
        
        # State update
        self.x = self.x + effective_K * (measurement - self.x)
        self.P = (1 - effective_K) * self.P
        
        # Confidence tracking
        if abs(measurement - self.x) < 0.1:
            self.confidence = min(1.0, self.confidence + 0.05)
        else:
            self.confidence = max(0.1, self.confidence - 0.02)
```

### 2.5 Trigger Detection Details

**Traffic Control Detection:**
```python
def detect_traffic_control(self, model_v2, radar_state, v_ego) -> bool:
    """Detect traffic lights and stop signs from modelV2.

    NOTE: modelV2.meta does not have trafficLightProbability / stopSignProbability.
    We use a heuristic: model predicts stop + no lead + low speed/standstill.
    This triggers force stops and E2E mode at intersections without requiring
    explicit traffic control metadata from the model.
    """
    if not model_v2:
        return False

    has_lead = radar_state.leadOne.status if radar_state else False
    should_stop = self.detect_stop_prediction(model_v2)

    # Heuristic: traffic control likely when model wants to stop,
    # no lead is present, and we're at low speed (intersection/standstill)
    return should_stop and not has_lead and v_ego < self.LOW_SPEED_THRESHOLD
```

**Lead Vehicle Detection:**
```python
def detect_slower_lead(radar_state, v_ego):
    """Detect significantly slower lead vehicle."""
    if not radar_state.leadOne.status:
        return False
        
    lead_v = radar_state.leadOne.vLead
    delta_v = lead_v - v_ego
    
    # Lead is slower by more than 5 m/s (~18 km/h)
    return delta_v < -5.0
```

---

## 3. Reference Implementation Analysis

### 3.1 Sunnypilot DEC (Dynamic Experimental Control)

**File:** `sunnypilot/controls/lib/dec/dec.py` (388 lines)

**Implementation:**
```python
class DynamicExperimentalController:
    def __init__(self, CP, mpc, params):
        self.mode_manager = ModeTransitionManager()
        
        # Multiple filters for different triggers
        self._lead_filter = SmoothKalmanFilter(measurement_noise=0.15)
        self._slow_down_filter = SmoothKalmanFilter(measurement_noise=0.1)
        self._slowness_filter = SmoothKalmanFilter(measurement_noise=0.1)
        self._mpc_fcw_filter = SmoothKalmanFilter(measurement_noise=0.2)
        
    def update(self, sm):
        # Update all filters
        self._lead_filter.add_data(float(sm['radarState'].leadOne.status))
        self._slow_down_filter.add_data(self._calculate_slow_down(sm['modelV2']))
        
        # Mode decision based on filtered values
        if self._has_slow_down and self._urgency > 0.7:
            self._mode_manager.request_mode('blended', confidence=1.0, emergency=True)
```

**Key Features:**
- Mode transition manager with hysteresis
- Multiple Kalman filters for different triggers
- Emergency override system
- Radarless and radar-based modes

**Pros:**
- ✅ **Best-in-class architecture** - Clean, modular, extensible
- ✅ **Smooth transitions** - Kalman filters prevent oscillation
- ✅ **Emergency handling** - FCW triggers immediate E2E
- ✅ **Dual mode support** - Handles both radar and radarless cars
- ✅ **Confidence tracking** - Each trigger has confidence value
- ✅ **Well-tested** - 388 lines of refined logic

**Cons:**
- ❌ **Complexity** - 388 lines, multiple classes
- ❌ **SP dependencies** - Uses sunnypilot-specific cereal messages
- ❌ **Over-engineered** - Some features may not be needed

**Verdict:** **Primary reference** - Best architecture despite complexity.

---

### 3.2 FrogPilot Conditional Experimental Mode

**File:** `frogpilot/controls/lib/conditional_experimental_mode.py`

**Implementation:**
```python
class ConditionalExperimental:
    def update(self, carState, modelV2, radarState):
        # Curves
        if self.curves_enabled and self._detect_curve(modelV2):
            return True
            
        # Leads
        if self.lead_enabled and self._detect_slow_lead(radarState):
            return True
            
        # Navigation
        if self.navigation_enabled and self._nav_intersection():
            return True
            
        # Stop lights
        if self.stop_lights_enabled and self._detect_stop_light(modelV2):
            return True
            
        return False
```

**Key Features:**
- Simple trigger-based logic
- User-configurable triggers
- Immediate response

**Pros:**
- ✅ **Simple to understand** - Clear trigger logic
- ✅ **User control** - Toggle individual triggers
- ✅ **Low latency** - No filtering delays

**Cons:**
- ❌ **No smoothing** - Can oscillate rapidly
- ❌ **No confidence** - Binary decisions
- ❌ **Limited hysteresis** - Ping-pong risk
- ❌ **Fixed thresholds** - No adaptation

**Verdict:** Good for understanding triggers, but lacks stability.

---

### 3.3 Comparison Summary

| Aspect | Sunnypilot DEC | FrogPilot CEM | **EOP DLON** |
|--------|----------------|---------------|--------------|
| **Architecture** | State machine + filters | Rule-based | State machine + filters |
| **Smoothing** | Kalman filters | None | Kalman filters |
| **Complexity** | High (388 lines) | Low (150 lines) | Medium (~250 lines) |
| **Emergency Handling** | Excellent | Basic | Good |
| **Transitions** | Smooth | Abrupt | Smooth |
| **Confidence** | Per-trigger | None | Per-trigger |

---

### 3.4 EOP Selection Rationale

**EOP Approach:** Simplified DEC with essential triggers only

**Why Sunnypilot DEC Over FrogPilot CEM?**

| Criteria | DEC Advantage |
|----------|---------------|
| **Stability** | Kalman filters prevent mode oscillation |
| **Safety** | Emergency FCW override |
| **Flexibility** | Easy to add new triggers |
| **Testing** | Confidence values help debug |

**Why Simplified vs. Full DEC?**

**Sunnypilot DEC Features → EOP Decision:**

| DEC Feature | Lines | EOP Include? | Rationale |
|-------------|-------|--------------|-----------|
| Mode transition manager | ~80 | ✅ Yes | Core functionality |
| Smooth Kalman filters | ~60 | ✅ Yes | Essential for stability |
| Lead detection filter | ~30 | ✅ Yes | Common trigger |
| Slow down detection | ~60 | ✅ Yes | Traffic/scenario detection |
| Slowness detection | ~40 | ✅ Yes | Low-speed handling |
| MPC FCW filter | ~40 | ✅ Yes | Safety critical |
| Radarless mode logic | ~50 | ❌ No | EOP assumes radarless |
| Debug/telemetry | ~28 | ❌ No | Remove for simplicity |

**Estimated EOP DLON Size:** ~250 lines (down from 388)

**Key Simplifications:**

1. **Single Mode (Radarless)**
   - **Sunnypilot:** Dual mode (radar/radarless)
   - **EOP:** RK3588 is radarless by design
   - **Benefit:** Remove 50 lines of mode switching

2. **Essential Triggers Only**
   - **Sunnypilot:** 5+ trigger types
   - **EOP:** 4 core triggers (traffic, lead, low-speed, turn)
   - **Benefit:** Simpler testing and validation

3. **Fixed Thresholds (initially)**
   - **Sunnypilot:** Some tunable thresholds
   - **EOP:** Hardcoded based on DEC defaults
   - **Benefit:** Reduce parameter surface

**Evidence from Research:**
- DEC is the most praised feature in sunnypilot community
- Kalman filtering is essential for smooth mode switches
- Emergency FCW override is safety-critical
- FrogPilot's simpler approach causes complaints about "jerkiness"

---

## 4. EOP Implementation Plan

### 4.1 Files and Classes

| File | Purpose |
|------|---------|
| `selfdrive/controls/lib/dlon.py` | Main DLON controller |
| `selfdrive/controls/lib/eop_dlon_filters.py` *(not implemented)* | Kalman filter implementations |
| `selfdrive/controls/controlsd.py` | Integration point |

### 4.2 Class Structure

```python
# selfdrive/controls/lib/dlon.py

from enum import Enum
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params


class DLONMode(Enum):
    """DLON operating modes."""
    CHILL = "Chill"
    EXPERIMENTAL = "Experimental"
    AUTO = "Auto"


class DriveMode(Enum):
    """Internal drive mode state."""
    ACC = "acc"
    E2E = "blended"


class ModeTransitionManager:
    """Manages smooth transitions between driving modes with hysteresis."""
    
    def __init__(self):
        self.current_mode = DriveMode.ACC
        self.mode_confidence = {DriveMode.ACC: 1.0, DriveMode.E2E: 0.0}
        self.transition_timeout = 0
        self.mode_duration = 0
        self.emergency_override = False
        self.min_mode_duration = 10  # frames
        
    def request_mode(self, mode: DriveMode, confidence: float = 1.0, emergency: bool = False):
        """Request mode transition with confidence and emergency override."""
        if emergency:
            self.emergency_override = True
            self.current_mode = mode
            self.transition_timeout = 15
            self.mode_duration = 0
            return
            
        # Update confidence
        self.mode_confidence[mode] = min(1.0, self.mode_confidence[mode] + 0.1 * confidence)
        for m in self.mode_confidence:
            if m != mode:
                self.mode_confidence[m] = max(0.0, self.mode_confidence[m] - 0.05)
                
        # Check minimum duration
        if self.mode_duration < self.min_mode_duration and not self.emergency_override:
            return
            
        # Hysteresis: higher threshold for mode changes
        threshold = 0.6 if mode != self.current_mode else 0.3
        
        if self.mode_confidence[mode] > threshold:
            if mode != self.current_mode and self.transition_timeout == 0:
                self.transition_timeout = 15
                self.current_mode = mode
                self.mode_duration = 0
                
    def update(self):
        """Call every frame to update timing."""
        if self.transition_timeout > 0:
            self.transition_timeout -= 1
        self.mode_duration += 1
        
        if self.emergency_override and self.mode_duration > 20:
            self.emergency_override = False
            
        # Gradual confidence decay
        for mode in self.mode_confidence:
            self.mode_confidence[mode] *= 0.98
            
    def get_mode(self) -> DriveMode:
        return self.current_mode


class SmoothKalmanFilter:
    """Kalman filter with smoothing for stable trigger detection."""
    
    def __init__(self, measurement_noise=0.1, process_noise=0.01,
                 smoothing_factor=0.85):
        self.R = measurement_noise
        self.Q = process_noise
        self.smoothing_factor = smoothing_factor
        self.x = 0.0
        self.P = 1.0
        self.confidence = 0.0
        self.initialized = False
        
    def add_data(self, measurement):
        if not self.initialized:
            self.x = measurement
            self.initialized = True
            return
            
        # Prediction
        self.P = self.P + self.Q
        
        # Update with smoothing
        K = self.P / (self.P + self.R)
        effective_K = K * (1.0 - self.smoothing_factor) + self.smoothing_factor * 0.1
        
        self.x = self.x + effective_K * (measurement - self.x)
        self.P = (1 - effective_K) * self.P
        
        # Confidence update
        if abs(measurement - self.x) < 0.1:
            self.confidence = min(1.0, self.confidence + 0.05)
        else:
            self.confidence = max(0.1, self.confidence - 0.02)
            
    def get_value(self):
        return self.x if self.initialized else 0.0
        
    def get_confidence(self):
        return self.confidence


class DLON:
    """
    Dynamic Longitudinal Profile Controller
    
    Dynamically switches between Chill (ACC) and Experimental (E2E) modes
    based on environmental triggers.
    """
    
    # Trigger thresholds
    LEAD_SLOW_VELOCITY_THRESHOLD = -5.0  # m/s
    LOW_SPEED_THRESHOLD = 11.0  # m/s (~40 km/h)
    HIGHWAY_SPEED_THRESHOLD = 80.0 / 3.6  # 80 km/h
    
    def __init__(self):
        self.params = Params()
        self.mode = DLONMode.AUTO
        self.enabled = self.params.get_bool("EOPDLONEnabled")
        
        # Mode manager
        self.mode_manager = ModeTransitionManager()
        
        # Filters for trigger stability
        self.lead_filter = SmoothKalmanFilter(measurement_noise=0.15)
        self.traffic_filter = SmoothKalmanFilter(measurement_noise=0.2)
        self.fcw_filter = SmoothKalmanFilter(measurement_noise=0.2)
        
        # State
        self.frame = 0
        self._v_ego = 0.0
        self._has_lead_filtered = False
        self._has_traffic_control = False
        self._has_mpc_fcw = False
        
    def update_params(self):
        """Update parameters periodically."""
        if self.frame % int(1.0 / DT_MDL) == 0:
            self.enabled = self.params.get_bool("EOPDLONEnabled")
            mode_str = self.params.get("EOPDLONMode", encoding='utf-8')
            try:
                self.mode = DLONMode(mode_str)
            except ValueError:
                self.mode = DLONMode.AUTO
                
    def detect_traffic_control(self, model_v2, radar_state, v_ego) -> bool:
        """Detect traffic lights and stop signs.

        NOTE: modelV2.meta does not have trafficLightProbability / stopSignProbability.
        We use a heuristic: model predicts stop + no lead + low speed/standstill.
        This triggers force stops and E2E mode at intersections without requiring
        explicit traffic control metadata from the model.
        """
        if not model_v2:
            return False

        has_lead = radar_state.leadOne.status if radar_state else False
        should_stop = self.detect_stop_prediction(model_v2)

        # Heuristic: traffic control likely when model wants to stop,
        # no lead is present, and we're at low speed (intersection/standstill)
        return should_stop and not has_lead and v_ego < self.LOW_SPEED_THRESHOLD
        
    def detect_slower_lead(self, radar_state, v_ego) -> bool:
        """Detect significantly slower lead vehicle."""
        if not radar_state.leadOne.status:
            return False
            
        delta_v = radar_state.leadOne.vLead - v_ego
        return delta_v < self.LEAD_SLOW_VELOCITY_THRESHOLD
        
    def detect_low_speed_scenario(self, v_ego, has_lead) -> bool:
        """Detect low-speed navigation scenario."""
        return v_ego < self.LOW_SPEED_THRESHOLD and not has_lead
        
    def detect_turn_intent(self, car_state, v_ego) -> bool:
        """Detect turn signal at non-highway speeds."""
        if v_ego > self.HIGHWAY_SPEED_THRESHOLD:
            return False
            
        return car_state.leftBlinker or car_state.rightBlinker
        
    def update(self, sm, mpc_crash_cnt: int = 0) -> dict:
        """
        Main update method.
        
        Args:
            sm: SubMaster with carState, modelV2, radarState
            mpc_crash_cnt: MPC FCW crash counter
            
        Returns:
            Dict with mode, e2e_enabled, triggers
        """
        self.update_params()
        
        CS = sm['carState']
        model_v2 = sm['modelV2']
        radar_state = sm['radarState']
        
        self._v_ego = CS.vEgo
        
        # Update filters
        has_lead = radar_state.leadOne.status
        self.lead_filter.add_data(float(has_lead))
        self._has_lead_filtered = self.lead_filter.get_value() > 0.5
        
        has_traffic = self.detect_traffic_control(model_v2, radar_state, v_ego)
        self.traffic_filter.add_data(float(has_traffic))
        self._has_traffic_control = self.traffic_filter.get_value() > 0.5
        
        self.fcw_filter.add_data(float(mpc_crash_cnt > 0))
        self._has_mpc_fcw = self.fcw_filter.get_value() > 0.5
        
        # Determine if E2E should be active
        if self.mode == DLONMode.CHILL:
            use_e2e = False
        elif self.mode == DLONMode.EXPERIMENTAL:
            use_e2e = True
        else:  # AUTO mode
            use_e2e = self._evaluate_auto_mode(CS, model_v2, radar_state)
            
        # Update mode manager
        requested_mode = DriveMode.E2E if use_e2e else DriveMode.ACC
        confidence = self._calculate_confidence()
        emergency = self._has_mpc_fcw
        
        self.mode_manager.request_mode(requested_mode, confidence, emergency)
        self.mode_manager.update()
        
        final_mode = self.mode_manager.get_mode()
        is_e2e = final_mode == DriveMode.E2E
        
        self.frame += 1
        
        return {
            'mode': self.mode.value,
            'e2e_enabled': is_e2e,
            'internal_mode': final_mode.value,
            'triggers': {
                'traffic_control': self._has_traffic_control,
                'slower_lead': self.detect_slower_lead(radar_state, self._v_ego),
                'low_speed': self.detect_low_speed_scenario(self._v_ego, has_lead),
                'turn_intent': self.detect_turn_intent(CS, self._v_ego),
                'mpc_fcw': self._has_mpc_fcw
            },
            'confidences': {
                'lead': self.lead_filter.get_confidence(),
                'traffic': self.traffic_filter.get_confidence(),
                'fcw': self.fcw_filter.get_confidence()
            }
        }
        
    def _evaluate_auto_mode(self, CS, model_v2, radar_state) -> bool:
        """Evaluate if E2E should be active in AUTO mode."""
        # High priority triggers
        if self._has_mpc_fcw:
            return True
            
        if self._has_traffic_control:
            return True
            
        if self.detect_slower_lead(radar_state, self._v_ego):
            return True
            
        # Medium priority triggers
        if self.detect_low_speed_scenario(self._v_ego, radar_state.leadOne.status):
            return True
            
        if self.detect_turn_intent(CS, self._v_ego):
            return True
            
        return False
        
    def _calculate_confidence(self) -> float:
        """Calculate overall confidence for mode decision."""
        confidences = [
            self.traffic_filter.get_confidence() if self._has_traffic_control else 0,
            self.fcw_filter.get_confidence() if self._has_mpc_fcw else 0,
            0.7 if self.detect_slower_lead(None, self._v_ego) else 0
        ]
        return max(confidences) if confidences else 0.5
```

### 4.3 Parameter Management

**EOP Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPDLONEnabled` | Bool | `0` | Master toggle |
| `EOPDLONMode` | String | `"Chill"` | Mode: "Chill", "Experimental", "Auto" |

---

## 5. Safety Analysis

### 5.1 Safety Mechanisms

| Mechanism | Implementation |
|-----------|----------------|
| **Emergency Override** | MPC FCW immediately forces E2E mode |
| **Mode Hysteresis** | 10-frame minimum duration prevents oscillation |
| **Confidence Thresholds** | 0.6 confidence required for mode change |
| **Transition Timeout** | 15-frame cooldown between switches |
| **User Override** | Manual mode selection takes precedence |

### 5.2 Risk Assessment

| Risk | Mitigation | Likelihood | Severity |
|------|------------|------------|----------|
| Mode oscillation | Kalman filters + hysteresis | Low | Low |
| Missed stop signal | Multiple trigger sources | Low | High |
| False E2E engagement | Confidence thresholds | Low | Medium |
| Highway E2E | Speed-based suppression | Very Low | Medium |

---

## 6. Testing Plan

### 6.1 Unit Tests

```python
def test_mode_transition_manager():
    mtm = ModeTransitionManager()
    # Test hysteresis and emergency override

def test_kalman_filter():
    kf = SmoothKalmanFilter()
    # Test smoothing and confidence tracking

def test_trigger_detection():
    dlon = DLON()
    # Test each trigger condition
```

### 6.2 Integration Tests

- Highway driving (should stay in ACC)
- Urban intersection (should switch to E2E)
- Traffic light detection
- Following slower lead
- Emergency FCW scenario

### 6.3 Real-World Validation

- Minimum 500km mixed driving
- Log all mode switches with trigger reasons
- Validate no unwanted oscillations

---

## 7. Comparison with Reference Forks

| Aspect | Sunnypilot DEC | FrogPilot CEM | **EOP DLON** |
|--------|----------------|---------------|--------------|
| **Lines of Code** | 388 | 150 | ~250 |
| **Filters** | 4 Kalman filters | None | 3 Kalman filters |
| **Emergency FCW** | ✅ Yes | ⚠️ Basic | ✅ Yes |
| **Mode Hysteresis** | ✅ Yes | ❌ No | ✅ Yes |
| **Radarless Support** | ✅ Yes | ✅ Yes | ✅ Yes (only) |
| **Debug Telemetry** | ✅ Extensive | ⚠️ Basic | ❌ Minimal |

---

## 8. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Design Document | ✅ Complete | This document |
| State Machine Design | ✅ Complete | Mode transition manager |
| Filter Design | ✅ Complete | SmoothKalmanFilter |
| Core Implementation | ✅ Complete | `selfdrive/controls/lib/dlon.py` |
| Planner Integration | ✅ Complete | longitudinal_planner.py |
| Unit Tests | ⏳ Pending | Test scenarios defined |
| Documentation | ✅ Complete | This document |

---

---

## Implementation

### Function

Deep learning-based longitudinal control:
- Neural network predicts acceleration
- Lead vehicle following
- Stop-and-go traffic

### Algorithm

```
Input: modelV2.leads, radarState, carState.vEgo
Output: desiredAcceleration, followingDistance
```

### Model

| Model | Input | Output |
|-------|-------|--------|
| driving_policy | Camera + radar | Acceleration + following |

### Code Location

- `selfdrive/controls/lib/longitudinal_planner.py`
- `selfdrive/controls/lib/lead_mpc.py` *(not implemented)*


## 9. Related Documents

- [EOP OVERVIEW](../../00_Index/OVERVIEW.md) - EOP Architecture Overview
- [DLAT.md](./DLAT.md) - Dynamic Lateral Profile (complementary)
- Sunnypilot dec.py - Primary reference

---

## 10. 2026-08-09 Update — DLAT confidence coupling

DLAT and DLON previously ran fully independently in AUTO mode despite both
being confidence-driven automatic switches over the same underlying signal
(how much the model trusts what it sees). `dlon.py::_evaluate_auto_mode()`
gained a new lowest-priority trigger,
`detect_lane_confidence_trigger()`, which reads `sm['controlsState'].
dlatUseLaneless` — DLAT's own hysteresis-resolved Laneful/Laneless decision,
published from `controlsd.py` — rather than re-deriving a second threshold
on the raw confidence value. Rationale: when DLAT has committed to Laneless
because lane lines are unreliable, E2E's path-only prediction is a better
fit than lane-line-anchored ACC — the same reason a human driver leans more
on road/path shape and less on lane markings when the markings are faded or
absent. A stale or missing `controlsState` resolves to no-trigger (neutral),
matching DLAT's own convention of defaulting to laneful rather than laneless
when its input is missing. Gated by a new per-trigger toggle,
`EOPDLONLaneConfidenceEnabled` (default on), following the existing sibling
pattern — only consulted while `EOPDLONMode` is `Auto`, same scope as the
other per-trigger toggles.

Design context: studied `~/pilot/dragonpilot`'s `aem.py`/`acm.py` for prior
art on confidence-based automatic mode switching per request. Both are
non-commercial-licensed (Copyright Rick Lan, 2025) and turned out not to be
about lane-confidence coupling anyway (`aem.py` uses throttle-intent
probability, `acm.py` is lead-gated coast suppression) — no code or
structure was copied; this implementation is independent.

The identical coupling was implemented on `dev/NGP10`'s `ngp_dlon.py`
(`ngp_lon_dlon_lane_confidence`), where it's always consulted since that
branch has no `EOPDLONMode`-equivalent mode selector at all — DLON is
unconditional automatic there by explicit design choice. See DLAT.md §11
for the companion LCA-initiation-gate change landed the same day.

---

## 11. 2026-08-10 Update — `EOPDLONMode` removed, matches NGP10

`EOPDLONMode` (the ACC/E2E/Dynamic `ButtonParamControl` in `eop_panel.cc`)
and its param key were removed — this branch's DLON is now unconditional
AUTO, same as `dev/NGP10`, closing the divergence noted above and in
§10's last paragraph ("that branch has no `EOPDLONMode`-equivalent mode
selector at all" — now true of neither branch). `update_params()` hardcodes
`self.mode = DLONMode.AUTO` instead of reading a param. Every reference to
`EOPDLONMode` in §4.x's design pseudocode and the §8/9 parameter tables
above predates this and reflects the original (never fully built) design
proposal, not current behavior — see DLAT.md §12 for the equivalent DLAT
change and full verification notes (same commit, same test run covers
both files).
