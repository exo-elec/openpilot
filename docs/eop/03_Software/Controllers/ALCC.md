# ALCC - Always Lane Centering Control

**Type:** Controller (runs inside `controlsd`)  
**File:** `selfdrive/controls/controlsd.py` (integrated)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/controls/controlsd.py` (integrated) |
| **UI** | ✅ Toggle in EOP panel |

---

## Overview

ALCC is the baseline lateral control mode that keeps the vehicle centered in the lane. It is the default lateral mode when no other lateral feature is explicitly engaged. Unlike stock openpilot which requires cruise control to be active for lane centering, ALCC can operate independently (when `EOPALCCAllowAlways` is enabled).

---

## Architecture

```
modelV2.path + modelV2.laneLines ──► controlsd ──► ALCC logic ──► desired curvature
```

ALCC runs as the default lateral mode in `controlsd.py`:
- Lines 126-148: ALCC integration
- Uses `modelV2.path` prediction for trajectory
- Uses `modelV2.laneLines` for lane boundary awareness
- No additional daemon required

---

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `EOPALCCEnabled` | 1 | Enable ALCC |
| `EOPLatALCC` | 1 | Lateral ALCC offset |
| `EOPALCCAllowAlways` | 0 | ALCC without cruise main |
| `EOPALCCHoldAtStandstill` | 0 | Keep lateral torque at stop |
| `EOPALCCBrakeMode` | "Maintain" | Maintain/Pause/Disengage on brake |

---

## Behavior

### Engagement Conditions

| Condition | Requirement |
|-----------|-------------|
| Vehicle speed | > 0 m/s (or hold at standstill if enabled) |
| Model valid | `modelV2` message received within timeout |
| Driver override | Steering torque < threshold |

### Disengagement Conditions

| Condition | Action |
|-----------|--------|
| Driver steering override | Temporary disengage, resume when released |
| Brake pressed | Depends on `EOPALCCBrakeMode` |
| Turn signal active | May pause (vehicle-dependent) |
| Standstill | Hold if `EOPALCCHoldAtStandstill` enabled |

### Brake Mode Behavior

| Mode | Brake Pressed | Release Brake |
|------|---------------|---------------|
| **Maintain** | ALCC stays engaged | Continues |
| **Pause** | ALCC pauses | Resumes automatically |
| **Disengage** | ALCC disengages | Requires re-engagement |

---

## Integration with Other Controllers

ALCC serves as the **fallback lateral mode** when:
- DLAT is in "Laneful" mode
- DLAT Auto mode detects high lane line confidence
- Driver has not requested E2E/Laneless mode

When DLAT selects "Laneless" mode, the path following uses `modelV2.predictedPath` instead of lane lines, but ALCC framework remains the execution layer.

---

## File Location

- **Integration**: `selfdrive/controls/controlsd.py` (lines 126-148)
- **UI Toggle**: `selfdrive/ui/qt/offroad/eop_panel.cc`

---

## Related Documents

- DLAT.md - Dynamic Lateral Profile (mode switching)
- LCA.md - Lane Change Assist
- RED.md - Road Edge Detection (safety guardrail)
- SOC.md - Smart Offset Control (truck nudge)
