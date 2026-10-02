# TJA - Traffic Jam Assist

**Type:** Controller (runs inside `longcontrol.py`)  
**File:** `selfdrive/controls/lib/longcontrol.py` (integrated)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/controls/lib/longcontrol.py` (integrated) |
| **UI** | ✅ Toggle in EOP panel |

---

## Overview

TJA provides smooth acceleration and deceleration in stop-and-go traffic. It modifies the longitudinal control behavior at low speeds to create a more comfortable driving experience during traffic jams.

---

## Architecture

```
carState.vEgo + modelV2.leads ──► longcontrol.py ──► TJA logic ──► Smooth accel profile
```

TJA logic is integrated into `longcontrol.py`:
- Progressive ramp for smooth starts from standstill
- Max hold at standstill: configurable (default 10 minutes)
- Reduced jerk limits in low-speed regime

---

## Behavior

### Smooth Start Profile

When starting from standstill (e.g., after a lead car moves):

```
Acceleration ramp: 0.25 m/s² → 1.2 m/s² over 2.0 seconds
```

This prevents the abrupt "lurch" that standard ACC can produce when resuming from a stop.

### Standstill Hold

When the vehicle is stopped with a lead car ahead:

| Parameter | Default | Description |
|-----------|---------|-------------|
| Hold duration | 10 minutes | Maximum time to hold brakes at standstill |
| Auto-resume | Yes | Resume when lead car moves |
| Creep speed | 5 km/h | Minimum forward speed when no lead |

### Resume Delay

After the lead car moves, TJA waits a configurable delay before accelerating:

| Parameter | Default | Description |
|-----------|---------|-------------|
| Resume delay | 1.5 seconds | Time before following lead car |

---

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `EOPTJAEnabled` | 0 | Enable TJA |
| `EOPTJAMaxHoldMinutes` | 10 | Max hold at standstill (minutes) |

---

## Integration

TJA modifies the behavior of `longcontrol.py` when:
- Vehicle speed < 20 km/h (low-speed regime)
- Lead car is present or recently was present
- `EOPTJAEnabled` is true

It does **not** replace the standard longitudinal planner — it applies a smoothing filter to the acceleration output in the low-speed regime.

---

## Comparison with Stock Behavior

| Scenario | Stock openpilot | With TJA |
|----------|-----------------|----------|
| Start from stop | Immediate target accel | Progressive ramp (0.25→1.2 m/s²) |
| Stop behind lead | Hold for limited time | Extended hold (up to 10 min) |
| Lead moves | Immediate resume | 1.5s delay + smooth ramp |
| Creep forward | Standard ACC | Limited to 5 km/h |

---

## File Location

- **Implementation**: `selfdrive/controls/lib/longcontrol.py`
- **UI Toggle**: `selfdrive/ui/qt/offroad/eop_panel.cc`

---

## Related Documents

- DLON.md - Dynamic Longitudinal Profile (mode switching)
- VTSC.md - Vision Turn Speed Control
- MTSC.md - Map Turn Speed Control
- MSLC.md - Map Speed Limit Control
