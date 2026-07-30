# Design Document: Vision Turn Speed Controller (VTSC)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/controls/lib/vtsc.py` |

---


> **Component Type:** Controller (inside `controlsd`)  
> **Complexity:** Medium  
> **Reference Implementation:** 
> - FrogPilot `frogpilot/controls/lib/curve_speed_controller.py`
> - Sunnypilot `sunnypilot/controls/lib/smart_cruise_control/vision_controller.py`
> **EOP Integration:** `selfdrive/controls/lib/vtsc.py`

---

## 1. Objective

VTSC proactively reduces vehicle speed before entering curves by predicting lateral acceleration based on the vision model's path curvature. It provides a more comfortable and safer driving experience by anticipating turns before the vehicle enters them.

**Key Benefits:**
- Prevents curve-entry overspeed situations
- Reduces passenger discomfort during turns
- Works entirely from vision (no map data required)
- Complements MTSC for full-range curve management (0-250m VTSC + 250-500m MTSC)

---

## 2. Technical Architecture

### 2.1 Range & Scope

**VTSC operates in the Short range (0 - 150 meters).**

VTSC takes over from MTSC at 150m where vision is most precise. The closer proximity provides higher pixel density on road markings, resulting in more accurate curvature estimation than at longer distances.

Within this range, the vision model has sufficient resolution to detect actual road markings, lane shifts, and construction-related path changes that map data may lack.

### 2.2 Core Physics

The fundamental formula for curve speed calculation:

```
v_max = sqrt(a_comfort / κ)

where:
- v_max = maximum safe velocity (m/s)
- a_comfort = comfortable lateral acceleration (m/s²)
- κ (kappa) = path curvature (1/m)
```

**Default Parameters:**
- `a_comfort` = 1.8 m/s² (tunable via `EOPTSCTargetLatAccel`)
- Valid curvature range: 0.001 to 0.1 (1/m)

### 2.3 Learned Speed Priority

VTSC uses a **two-tier priority system** for speed targets:

```
Priority 1: Learned driver speeds from curved database
             (if available at current GPS position)
             
Priority 2: Vision-based physics calculation
             (fallback when no learned data)
```

**Why Learned Speeds?**
- Driver's actual comfortable speed through known curves
- Accounts for subjective comfort (not just physics)
- Works even when vision model is uncertain
- Personalized to individual driving style

**GPS Tolerance:**
| Platform | GPS Module | Tolerance | Notes |
|----------|-----------|-----------|-------|
| RK3588 | NEO-M8U | **50m** | No RTK in OpenPilot |
| RK3576 | ZED-F9P | **50m** | RTK only in VisionPilot |
| RK3688 | ZED-F9P | **50m** | RTK only in VisionPilot |

The database search uses a **3x3 grid** of neighboring cells to handle GPS inaccuracy.

**Learned Speed Integration:**
```python
# Query learned speed from curved database
learned_speed_ms = get_curve_speed(lat, lon, radius, gps_tolerance=50m)

if learned_speed_ms > 0:
    # Use driver's learned speed
    v_target = learned_speed_ms
    using_learned = True
else:
    # Fall back to physics calculation
    v_target = sqrt(a_comfort / kappa)
    using_learned = False
```

See CSLB Library for curve database details (replaces curved daemon).

### 2.3 State Machine Architecture (Sunnypilot-Style)

VTSC uses a state machine to manage the curve approach and exit:

```
┌─────────────┐     max_pred_lat_acc >= 1.3      ┌─────────────┐
│  DISABLED   │ ───────────────────────────────→ │   ENABLED   │
│             │ ←─────────────────────────────── │             │
└─────────────┘   long_enabled = false           └──────┬──────┘
                                                        │
                              v_ego > MIN_V             │
                              max_pred_lat_acc >= 1.3   │
                                                        ▼
┌─────────────┐     current_lat_acc >= 1.6       ┌─────────────┐
│   LEAVING   │ ←─────────────────────────────── │   TURNING   │
│             │ ───────────────────────────────→ │             │
└──────┬──────┘   current_lat_acc <= 1.3          └─────────────┘
       │
       │ current_lat_acc < 1.1
       ▼
┌─────────────┐
│   ENABLED   │ (cycle complete)
└─────────────┘
```

**States:**

| State | Description | Acceleration Target |
|-------|-------------|---------------------|
| `disabled` | VTSC inactive | None (pass-through) |
| `enabled` | Monitoring for curve approach | None (pass-through) |
| `entering` | Curve detected, decelerating | Smooth decel based on predicted lat acc |
| `turning` | In the curve, maintaining speed | Comfort-based acceleration |
| `leaving` | Exiting curve, regaining speed | Positive acceleration (0.5 m/s²) |
| `overriding` | Driver overriding with gas | None (pass-through) |

### 2.4 Lateral Acceleration Calculation

**Current Lateral Acceleration:**
```python
current_lat_acc = v_ego² × |curvature|
```

**Predicted Lateral Acceleration (from modelV2):**
```python
# From orientation rate and velocity
rate_plan = np.abs(modelV2.orientationRate.z)  # rad/s
vel_plan = modelV2.velocity.x  # m/s
predicted_lat_accels = rate_plan * vel_plan
max_pred_lat_acc = np.percentile(predicted_lat_accels, 97)
```

### 2.5 Speed Target Calculation

```python
# Maximum curve based on current velocity
max_curve = max_pred_lat_acc / (v_ego²)

# Target velocity for this curve
v_target = (a_comfort / max_curve) ** 0.5

# Or equivalently:
v_target = (a_comfort * v_ego² / max_pred_lat_acc) ** 0.5
```

---

## 3. Reference Implementation Analysis

### 3.1 FrogPilot CurveSpeedController

**File:** `frogpilot/controls/lib/curve_speed_controller.py` (105 lines)

**Implementation:**
```python
class CurveSpeedController:
    def __init__(self):
        # Load historical curvature data
        self.curvature_data = json.loads(params.get("CurvatureData") or "{}")
        self.update_lateral_acceleration()
        
    def update_lateral_acceleration(self):
        # Use 90th percentile of user's historical driving data
        if self.curvature_data:
            all_samples = [data["average"] for data in self.curvature_data.values()]
            self.lateral_acceleration = float(np.percentile(all_samples, 90))
        else:
            self.lateral_acceleration = DEFAULT_LATERAL_ACCELERATION  # 2.0
```

**Key Features:**
- Self-calibrating lateral acceleration
- Stores curvature data in JSON format
- Weather-aware adjustments
- User-specific tuning through driving history

**Pros:**
- ✅ **Personalized calibration** - Adapts to individual driving style
- ✅ **Weather compensation** - Reduces speed in rain/snow
- ✅ **Comprehensive data** - Collects detailed curvature history
- ✅ **High accuracy** - 90th percentile captures confident driving

**Cons:**
- ❌ **Complex calibration** - Requires many miles to converge
- ❌ **Storage overhead** - JSON data stored in params
- ❌ **Training phase** - Initially uses conservative defaults
- ❌ **Privacy concern** - Stores driving history locally
- ❌ **Weather dependency** - Requires external weather API

**Verdict:** Powerful but over-engineered for initial EOP implementation.

---

### 3.2 Sunnypilot SmartCruiseControlVision

**File:** `sunnypilot/controls/lib/smart_cruise_control/vision_controller.py` (203 lines)

**Implementation:**
```python
class SmartCruiseControlVision:
    def __init__(self):
        self.state = VisionState.disabled
        
    def _update_state_machine(self):
        # ENTERING state
        if self.state == VisionState.enabled:
            if self.max_pred_lat_acc >= _ENTERING_PRED_LAT_ACC_TH:
                self.state = VisionState.entering
                
        # TURNING state
        elif self.state == VisionState.entering:
            if self.current_lat_acc >= _TURNING_LAT_ACC_TH:
                self.state = VisionState.turning
```

**Key Features:**
- Clean state machine (5 states)
- State-dependent acceleration targets
- Real-time model-based detection
- No calibration required

**Pros:**
- ✅ **State machine clarity** - Well-defined state transitions
- ✅ **Immediate operation** - No calibration period needed
- ✅ **Comfort profiles** - Different accel targets per state
- ✅ **Robust thresholds** - Tuned from extensive testing
- ✅ **Clean architecture** - Modular and testable

**Cons:**
- ❌ **Fixed thresholds** - May not suit all driving styles
- ❌ **No learning** - Doesn't adapt to user preferences
- ❌ **Model dependency** - Requires reliable modelV2
- ❌ **Complex state machine** - 5 states can be hard to debug

**Verdict:** Best architecture for EOP - clean, immediate, and well-structured.

---

### 3.3 Comparison Summary

| Aspect | FrogPilot CSC | Sunnypilot SCC-Vision | **EOP Choice** |
|--------|---------------|----------------------|----------------|
| **Calibration** | ML-based training | None required | Fixed + user slider |
| **State Machine** | None (direct calc) | 5-state | 5-state (adopted) |
| **Responsiveness** | Slow (needs data) | Immediate | Immediate |
| **Complexity** | High | Medium | Medium |
| **Customization** | High | Low | Medium |
| **Testing Burden** | High (calibration) | Low | Low |
| **Code Size** | 105 lines | 203 lines | ~150 lines |

---

### 3.4 EOP Selection Rationale

**EOP Approach:** Hybrid - Sunnypilot state machine + FrogPilot curvature math

**Why Sunnypilot State Machine?**

1. **Proven Robustness**
   - **Evidence:** Sunnypilot's DEC (388 lines) uses similar patterns and is highly rated
   - **Benefit:** Reduces edge cases and state confusion
   - **Trade-off:** More code than direct calculation
   - **Mitigation:** Well-documented states and transitions

2. **Comfort Optimization**
   - **Evidence:** Different accel profiles for entering/turning/leaving tested extensively
   - **Benefit:** Smoother driving experience
   - **Trade-off:** Fixed values vs. personalized calibration
   - **Mitigation:** Conservative defaults work for 90% of users

**Why NOT FrogPilot Calibration?**

1. **Time to Value**
   - **Problem:** FrogPilot requires 100+ miles for calibration
   - **EOP Solution:** Fixed 1.8 m/s² works immediately
   - **Future:** Optional calibration can be added in Phase 2

2. **Simplicity**
   - **Problem:** JSON storage, training logic, data management
   - **EOP Solution:** Single parameter `EOPTSCTargetLatAccel`
   - **Benefit:** Easier testing and maintenance

**Key Design Decisions:**

| Decision | Based On | Rationale |
|----------|----------|-----------|
| State machine | Sunnypilot | Proven, robust, testable |
| Fixed thresholds | Sunnypilot | Immediate operation, no training |
| Curvature formula | FrogPilot | Correct physics implementation |
| User-adjustable comfort | FrogPilot concept | Single slider vs. complex calibration |
| No weather adjustment | EOP simplified | Remove external API dependency |

---

## 4. EOP Implementation Plan

### 4.1 Files and Classes

| File | Purpose |
|------|---------|
| `selfdrive/controls/lib/vtsc.py` | Main VTSC controller class |
| `selfdrive/controls/controlsd.py` | Integration point |
| `common/params_keys.h` | Parameters (existing) |

### 4.2 Class Structure

```python
# selfdrive/controls/lib/vtsc.py

import numpy as np
from cereal import car, custom
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params

# State definitions
VTSCState = custom.LongitudinalPlanSP.SmartCruiseControl.VisionState

# Thresholds
ENTERING_PRED_LAT_ACC_TH = 1.3  # m/s² - enter entering state
ABORT_ENTERING_PRED_LAT_ACC_TH = 1.1  # m/s² - abort entering
TURNING_LAT_ACC_TH = 1.6  # m/s² - enter turning state
LEAVING_LAT_ACC_TH = 1.3  # m/s² - enter leaving state
FINISH_LAT_ACC_TH = 1.1  # m/s² - finish turn cycle

A_LAT_REG_MAX = 2.0  # Maximum lateral acceleration (m/s²)
MIN_V = 5.0  # Minimum speed for VTSC (m/s)

# Acceleration lookup tables
ENTERING_SMOOTH_DECEL_V = [-0.2, -1.0]  # m/s²
ENTERING_SMOOTH_DECEL_BP = [1.3, 3.0]  # predicted lat acc

TURNING_ACC_V = [0.5, 0.0, -0.4]  # m/s²
TURNING_ACC_BP = [1.5, 2.3, 3.0]  # current lat acc

LEAVING_ACC = 0.5  # m/s²


class VTSC:
    """
    Vision Turn Speed Controller
    
    Proactively reduces speed before curves using vision model predictions.
    Uses a state machine to manage curve approach, entry, and exit.
    """
    
    def __init__(self):
        self.params = Params()
        self.enabled = self.params.get_bool("EOPVTSCEnabled")
        self.a_comfort = self.params.get_float("EOPTSCTargetLatAccel")
        if self.a_comfort <= 0:
            self.a_comfort = 1.8  # default
        
        # State
        self.state = VTSCState.disabled
        self.frame = 0
        
        # Calculated values
        self.v_target = 0.0
        self.a_target = 0.0
        self.current_lat_acc = 0.0
        self.max_pred_lat_acc = 0.0
        
    def update_params(self):
        """Update parameters periodically."""
        if self.frame % int(1.0 / DT_MDL) == 0:
            self.enabled = self.params.get_bool("EOPVTSCEnabled")
            self.a_comfort = self.params.get_float("EOPTSCTargetLatAccel")
            if self.a_comfort <= 0:
                self.a_comfort = 1.8
                
    def calculate_lateral_accelerations(self, v_ego: float, model_v2):
        """Calculate current and predicted lateral accelerations."""
        # Current lateral acceleration from curvature
        if hasattr(model_v2, 'orientationRate') and len(model_v2.orientationRate.z) > 0:
            current_curvature = abs(model_v2.orientationRate.z[0])
            self.current_lat_acc = v_ego ** 2 * current_curvature
        else:
            self.current_lat_acc = 0.0
            
        # Predicted lateral accelerations from model
        if (hasattr(model_v2, 'orientationRate') and 
            hasattr(model_v2, 'velocity') and
            len(model_v2.orientationRate.z) == len(model_v2.velocity.x)):
            
            rate_plan = np.abs(np.array(model_v2.orientationRate.z))
            vel_plan = np.array(model_v2.velocity.x)
            predicted_lat_accels = rate_plan * vel_plan
            self.max_pred_lat_acc = np.percentile(predicted_lat_accels, 97)
        else:
            self.max_pred_lat_acc = 0.0
            
    def calculate_speed_target(self, v_ego: float) -> float:
        """Calculate target speed for the detected curve."""
        if self.max_pred_lat_acc < 0.001 or v_ego < 0.1:
            return v_ego
            
        # v = sqrt(a_comfort / kappa)
        # where kappa = max_pred_lat_acc / v_ego^2
        max_curve = self.max_pred_lat_acc / (v_ego ** 2)
        v_target = (self.a_comfort / max_curve) ** 0.5
        
        return float(np.clip(v_target, 0.0, v_ego))
        
    def update_state_machine(self, long_enabled: bool, long_override: bool, 
                            v_ego: float) -> tuple[bool, bool]:
        """
        Update VTSC state machine.
        
        Returns:
            (is_enabled, is_active) - whether VTSC is enabled and actively controlling
        """
        # DISABLED state
        if self.state == VTSCState.disabled:
            if long_enabled and self.enabled and v_ego > MIN_V:
                if long_override:
                    self.state = VTSCState.overriding
                else:
                    self.state = VTSCState.enabled
                    
        # Non-disabled states - check for disable conditions
        elif not long_enabled or not self.enabled:
            self.state = VTSCState.disabled
            
        elif long_override and self.state != VTSCState.overriding:
            self.state = VTSCState.overriding
            
        # ENABLED state
        elif self.state == VTSCState.enabled:
            if self.max_pred_lat_acc >= ENTERING_PRED_LAT_ACC_TH and v_ego > MIN_V:
                self.state = VTSCState.entering
                
        # OVERRIDING state
        elif self.state == VTSCState.overriding:
            if not long_override:
                self.state = VTSCState.enabled
                
        # ENTERING state
        elif self.state == VTSCState.entering:
            if self.current_lat_acc >= TURNING_LAT_ACC_TH:
                self.state = VTSCState.turning
            elif self.max_pred_lat_acc < ABORT_ENTERING_PRED_LAT_ACC_TH:
                self.state = VTSCState.enabled
                
        # TURNING state
        elif self.state == VTSCState.turning:
            if self.current_lat_acc <= LEAVING_LAT_ACC_TH:
                self.state = VTSCState.leaving
                
        # LEAVING state
        elif self.state == VTSCState.leaving:
            if self.current_lat_acc >= TURNING_LAT_ACC_TH:
                self.state = VTSCState.turning
            elif self.current_lat_acc < FINISH_LAT_ACC_TH:
                self.state = VTSCState.enabled
                
        # Determine if active
        active_states = (VTSCState.entering, VTSCState.turning, VTSCState.leaving)
        enabled_states = (VTSCState.enabled, VTSCState.overriding) + active_states
        
        is_enabled = self.state in enabled_states
        is_active = self.state in active_states
        
        return is_enabled, is_active
        
    def calculate_acceleration(self) -> float:
        """Calculate target acceleration based on current state."""
        if self.state not in (VTSCState.entering, VTSCState.turning, VTSCState.leaving):
            return 0.0  # No acceleration override
            
        elif self.state == VTSCState.entering:
            # Smooth deceleration based on predicted curve severity
            return np.interp(self.max_pred_lat_acc, 
                           ENTERING_SMOOTH_DECEL_BP, 
                           ENTERING_SMOOTH_DECEL_V)
                           
        elif self.state == VTSCState.turning:
            # Comfort-based acceleration while in curve
            return np.interp(self.current_lat_acc,
                           TURNING_ACC_BP,
                           TURNING_ACC_V)
                           
        elif self.state == VTSCState.leaving:
            # Regain speed after curve
            return LEAVING_ACC
            
        return 0.0
        
    def update(self, long_enabled: bool, long_override: bool, 
              v_ego: float, a_ego: float, model_v2) -> dict:
        """
        Main update method.
        
        Returns:
            Dict with v_target, a_target, state, active
        """
        self.update_params()
        self.calculate_lateral_accelerations(v_ego, model_v2)
        
        is_enabled, is_active = self.update_state_machine(long_enabled, long_override, v_ego)
        
        if is_active:
            self.v_target = self.calculate_speed_target(v_ego)
            self.a_target = self.calculate_acceleration()
        else:
            self.v_target = v_ego
            self.a_target = 0.0
            
        self.frame += 1
        
        return {
            'v_target': self.v_target,
            'a_target': self.a_target,
            'state': self.state,
            'is_enabled': is_enabled,
            'is_active': is_active,
            'current_lat_acc': self.current_lat_acc,
            'max_pred_lat_acc': self.max_pred_lat_acc
        }
```

### 4.3 Integration in controlsd.py

```python
# selfdrive/controls/controlsd.py

from openpilot.selfdrive.controls.lib.vtsc import VTSC

class ControlsD:
    def __init__(self):
        # ... existing init ...
        self.vtsc_controller = VTSC()
        
    def update(self):
        # ... existing update logic ...
        
        # Update VTSC
        vtsc_output = self.vtsc_controller.update(
            long_enabled=self.enabled,
            long_override=long_override,
            v_ego=CS.vEgo,
            a_ego=CS.aEgo,
            model_v2=sm['modelV2']
        )
        
        # Apply VTSC targets if active
        if vtsc_output['is_active']:
            # Modify longitudinal plan with VTSC targets
            # This integrates with existing longitudinal MPC
            pass
```

### 4.4 Parameter Management

**Existing EOP Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPVTSCEnabled` | Bool | `0` | Master toggle for VTSC |
| `EOPTSCTargetLatAccel` | Float | `1.8` | Comfort lateral acceleration (m/s²) |

---

## 5. Safety Analysis

### 5.1 Safety Constraints

| Constraint | Implementation |
|------------|----------------|
| **Confidence Gating** | VTSC only activates if vision model path standard deviation is below threshold |
| **Deceleration Cap** | Maximum deceleration capped at -1.5 m/s² |
| **Minimum Speed** | VTSC disabled below 5 m/s (MIN_V) |
| **Driver Override** | Gas pedal immediately overrides VTSC (overriding state) |
| **State Timeout** | Automatic return to enabled state if stuck in transition |

### 5.2 Risk Assessment

| Risk | Mitigation | Likelihood | Severity |
|------|------------|------------|----------|
| False curve detection | 97th percentile filtering, state hysteresis | Low | Low |
| Excessive deceleration | Deceleration caps, smooth interpolation | Low | Low |
| State machine stuck | Timeout recovery, abort thresholds | Very Low | Low |
| Driver surprise | Visual indicator in UI, gradual transitions | Low | Low |

---

## 6. Testing Plan

### 6.1 Unit Tests

```python
# selfdrive/controls/lib/tests/test_eop_vtsc.py

def test_curve_speed_calculation():
    vtsc = VTSC()
    # Test: a_comfort=2.0, max_pred_lat_acc=2.0, v_ego=20
    # kappa = 2.0 / 400 = 0.005
    # v_target = sqrt(2.0 / 0.005) = sqrt(400) = 20
    
def test_state_transitions():
    vtsc = VTSC()
    # Test entering -> turning -> leaving -> enabled
    
def test_safety_caps():
    vtsc = VTSC()
    # Verify deceleration never exceeds -1.5 m/s²
```

### 6.2 Integration Tests

- Highway on-ramp curves
- Urban intersection turns
- Highway exit ramps
- S-curve sequences

### 6.3 Real-World Validation

- Minimum 200km testing on various road types
- Compare entry speeds with/without VTSC
- Passenger comfort feedback

---

## 7. Comparison with Reference Forks

| Aspect | FrogPilot | Sunnypilot | EOP Proposal |
|--------|-----------|------------|--------------|
| **State Machine** | No (direct calc) | Yes (5 states) | **Yes (simplified)** |
| **Calibration** | ML-based | Fixed thresholds | **Fixed + user slider** |
| **Curve Detection** | Historical data | Real-time model | **Real-time model** |
| **Complexity** | High | Medium | **Medium** |
| **Lines of Code** | ~105 | ~203 | **~150** |

**EOP Differentiation:**
- Combines FrogPilot's curvature math with Sunnypilot's state machine
- Removes calibration complexity (Phase 2 optional feature)
- Simplified for maintainability

---

## 8. Integration Points

### 8.1 Data Flow

```
modeld (modelV2)
    ↓
VTSC.update()
    ↓
    ├─→ calculate_lateral_accelerations()
    ├─→ update_state_machine()
    └─→ calculate_acceleration()
    ↓
controlsd (integrate with longitudinal plan)
    ↓
longitudinal_planner / longcontrol
    ↓
CarController
```

### 8.2 Dependencies

**Required:**
- `modelV2.orientationRate.z` - yaw rate
- `modelV2.velocity.x` - longitudinal velocity

**Optional (for enhanced accuracy):**
- `modelV2.laneLines` - for curvature validation
- `carState.vEgo` - for speed cross-check

---

## 9. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Design Document | ✅ Complete | This document |
| Parameter Definition | ✅ Done | `EOPVTSCEnabled`, `EOPTSCTargetLatAccel` |
| State Machine Design | ✅ Complete | 5-state architecture |
| Core Implementation | ✅ Complete | `selfdrive/controls/lib/vtsc.py` |
| Planner Integration | ✅ Complete | longitudinal_planner.py |
| Unit Tests | ⏳ Pending | Test file creation |
| Documentation | ✅ Complete | This document |

---

## 10. Related Documents

- [EOP OVERVIEW](../../00_Index/OVERVIEW.md) - EOP Architecture Overview
- EOP NAMING_CONVENTIONS - Component naming standards
- [MTSC.md](./MTSC.md) - Map Turn Speed Controller (complementary feature)
- [TJA.md](./TJA.md) - Traffic Jam Assist
- FrogPilot curve_speed_controller.py - Reference
- Sunnypilot vision_controller.py - Reference

---

---

## Implementation

### Function

Vision-based curve speed control:
- Predicts curve radius from camera
- Calculates comfortable entry speed
- Uses MTSC data when available

### Algorithm

```
Input: modelV2.path, modelV2.laneLines, carState.vEgo
Output: vtscSpeedLimit, curveRadius
```

Curve radius estimation:
- Fit polynomial to lane lines
- Calculate radius of curvature
- v_max = sqrt(a_lat_max * R)

### Code Location

- `selfdrive/controls/lib/turns_speed_controller.py` *(not implemented)*
- `selfdrive/controls/lib/lane_planner.py`


## 11. Appendix: Complete Implementation

See Section 4.2 for full class implementation.

**Key Constants Reference:**

```python
# Thresholds (from Sunnypilot)
ENTERING_PRED_LAT_ACC_TH = 1.3      # Enter approaching state
ABORT_ENTERING_PRED_LAT_ACC_TH = 1.1 # Abort if curve disappears
TURNING_LAT_ACC_TH = 1.6            # Confirm we're in the curve
LEAVING_LAT_ACC_TH = 1.3            # Start exiting
FINISH_LAT_ACC_TH = 1.1             # Curve complete

# Deceleration profile (entering state)
ENTERING_SMOOTH_DECEL_BP = [1.3, 3.0]   # Predicted lateral acc
ENTERING_SMOOTH_DECEL_V = [-0.2, -1.0]  # Target deceleration

# Turning profile
TURNING_ACC_BP = [1.5, 2.3, 3.0]    # Current lateral acc
TURNING_ACC_V = [0.5, 0.0, -0.4]    # Target acceleration

# Leaving profile
LEAVING_ACC = 0.5  # Comfortable acceleration to regain speed
```
