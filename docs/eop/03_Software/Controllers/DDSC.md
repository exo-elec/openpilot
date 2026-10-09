# DDSC - Driver Distraction Speed Controller

**Type:** Controller (runs inside `plannerd`)  
**File:** `selfdrive/controls/lib/ddsc.py`

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ COMPLETE |
| **Code** | ✅ `selfdrive/controls/lib/ddsc.py` |
| **Tests** | ✅ 21/21 passing (`selfdrive/driverd/test_attention_tracker.py` *(not implemented)*) |

---

## Overview

DDSC proactively limits vehicle speed when the driver is critically distracted (`tooDistracted`) or unconscious (medical emergency). It is a **speed CAP** applied to `v_cruise` in `longitudinal_planner.py`, not a standalone daemon.

Unlike VTSC/MTSC which react to road geometry, DDSC reacts to **driver state** from the DMS (`driverStatus`).

### Two Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| **Distraction** | `tooDistracted=True` (3× critical alerts or 30s cumulative) | Cap at traffic-aware safe speed |
| **Unconscious** | `unconsciousActive=True` (no driver detected + no steering ≥20s) | Immediate deceleration, hazard lights, standstill latch |

---

## Architecture

```
driverStatus (driverd.py) ──► DDSC ──► speed cap → longitudinal_planner
    │                        │
    ├─ tooDistracted         ├─ Follow lead car
    ├─ unconsciousActive     ├─ Stop at traffic light
    └─ safeSpeedLimitMps     └─ Crawl / standstill latch

carState ──► DDSC
    ├─ vEgo
    ├─ gasPressed  (override)
    ├─ standstill
    └─ steeringTorque (indirect, via driverd)

radarState.leadOne ──► DDSC
    └─ vLead (follow with margin)
```

---

## Speed Cap Logic

### Priority Order (highest first)

1. **Gas override** — driver presses accelerator → cap cancelled immediately
2. **Standstill latch** — unconscious + stopped → stay at 0 m/s until gas
3. **Lead car** — if lead is present, cap at `lead_v × 0.9`
4. **Traffic stop** — TLSC/DLON wants to stop → cap at 0 m/s
5. **Unconscious crawl** — no lead, no stop signal → crawl at 15 km/h
6. **Highway** — empty highway (`v_ego > 90 km/h`) → cap at 80 km/h
7. **Urban** — default DMS cap → 60 km/h

### Constants

```python
DMS_BASE_LIMIT_MPS = 16.67        # 60 km/h — standard urban cap
HIGHWAY_CAP_MPS = 22.2            # 80 km/h — empty highway
HIGHWAY_VEGO_THRESHOLD_MPS = 22.0 # > 90 km/h considered highway
LEAD_SPEED_RATIO = 0.90           # follow lead with 10% margin
ABSOLUTE_MIN_MPS = 1.0            # ~3.6 km/h — never fully stop on highway
UNCONSCIOUS_CRAWL_MPS = 4.17      # 15 km/h — avoid being rear-ended
STANDSTILL_THRESHOLD_MPS = 0.3    # ~1 km/h — latch threshold
DDSC_MAX_DECEL_MPS2 = -1.5        # gentle deceleration
UNCONSCIOUS_DECEL_MPS2 = -2.0     # firmer but still gentle
```

---

## Unconscious Detection

### Trigger Condition

```
No driver detected  ≥ 20 seconds
AND
No steering torque ≥ 20 seconds (threshold: 0.3 Nm)
→ unconsciousActive = True
```

### Why 20 Seconds?

- 15s = UN R79 pre-alert boundary — too short, could be a yawn
- 20s = strong indicator of medical emergency (heart attack, seizure, fainting)
- 60s = normal critical boundary — too slow for unconscious driver

### Behavior When Unconscious

| Time | Action |
|------|--------|
| t+0s | `unconsciousActive=True`, `tooDistracted=True`, attention_prob=0 |
| t+0s | Hazard lights auto-activate (both blinkers) |
| t+0s | DDSC cap drops to crawl speed or follows lead |
| t+... | Car continues lane-keeping (L2 stack does NOT disengage) |
| Traffic light | ACC stops car naturally; standstill latch engages |
| Police cut-in | ACC brakes; standstill latch engages |
| Stopped | `standstill_latched=True` — car stays parked |
| Gas pressed | First responder override — latch releases, car can move |

### Why Not Disengage?

- L2 stack keeps steering — unconscious driver cannot steer
- Sudden disengagement = uncontrolled car = worse outcome
- Gradual deceleration + hazard lights = safer for all road users

---

## Integration

### longitudinal_planner.py

```python
# DDSC update — called every frame
self.ddsc_v_target = self.ddsc.update(
    driver_status=sm['driverStatus'],
    car_state=sm['carState'],
    lead_speed=lead_v,
    should_stop=self.dlon_force_stop,
)
v_cruise = self._apply_speed_limit(v_cruise, self.ddsc_v_target)
```

### controlsd.py

```python
# Hazard lights for unconscious driver
if (sm['driverStatus'].unconsciousActive
    and laneChangeState == off):
    CC.leftBlinker = True
    CC.rightBlinker = True
```

### driverd.py

```python
# Unconscious detection in AttentionTracker
if no_face_elapsed >= 20.0 and no_steering_elapsed >= 20.0:
    unconscious_active = True
    attention_prob = 0.0
    too_distracted = True
```

---

## Capnp Schema Changes

### DriverStatus (custom.capnp)

```capnp
struct DriverStatus {
  # ... existing fields ...
  unconsciousActive @19 :Bool;  # NEW
}
```

### LongitudinalPlan (log.capnp)

```capnp
struct LongitudinalPlan {
  # ... existing fields ...
  ddscActive @62 :Bool;
  ddscUnconscious @63 :Bool;
  ddscStandstillLatched @64 :Bool;
  ddscSpeed @65 :Float32;
}
```

### OnroadEvent (log.capnp)

```capnp
enum EventName {
  # ... existing alerts ...
  faceUnconscious @122;  # NEW — medical emergency alert
}
```

---

## Safety Analysis

### Airbag-Safe Speed Rationale

| Speed | Risk Level | Rationale |
|-------|-----------|-----------|
| < 30 km/h | ✅ Airbag-safe | Airbags deploy safely; car structure survives |
| 30–60 km/h | ⚠️ Moderate | Airbags help but injury risk rises |
| > 60 km/h | 🔴 Severe | Structural crash risk; high injury probability |

DDSC caps at 60 km/h (urban) or 80 km/h (highway) during distraction. In unconscious mode, it crawls at 15 km/h or stops.

### Rear-End Collision Avoidance

- **Never stop on highway** — minimum crawl 15 km/h keeps car visible
- **Follow lead car** — ACC handles safe following distance
- **Gentle deceleration** — -1.5 m/s² (half of comfort braking) avoids startling trailing drivers

### Override Rules

| Input | Effect on DDSC |
|-------|---------------|
| **Gas pressed** | Cancels cap AND releases standstill latch |
| **Steering torque** | Does NOT cancel cap (driver may be slumped but touching wheel) |
| **Driver detected** | Cancels unconscious mode; normal attention recovery begins |

---

## Testing

### Unit Tests

```bash
python3 selfdrive/driverd/test_attention_tracker.py
# 21 tests — includes 3 unconscious detection tests
```

### Test Coverage

| Test | Description |
|------|-------------|
| `test_unconscious_detection` | 25s no driver detected + no steering → unconscious active |
| `test_unconscious_resets_on_recovery` | Driver detected returns → unconscious clears |
| `test_unconscious_not_triggered_with_steering` | Steering active → no unconscious |

### Manual Validation Scenarios

1. **Highway unconscious** — Car crawls at 15 km/h with hazard lights; lanes maintained
2. **Urban traffic light** — Car stops at red light; standstill latch holds; resumes only on gas
3. **Police cut-in** — ACC brakes behind police car; stops; latch holds
4. **First responder** — Presses gas; latch releases; car moves to shoulder
5. **False positive** — Driver removes hat (no face) but steers → no unconscious

---

## Related Documents

- [EOP OVERVIEW](../../00_Index/OVERVIEW.md)
- [VTSC.md](./VTSC.md) — Curve speed control (complementary)
- [MTSC.md](./MTSC.md) — Map-based curve speed
- [TLSC.md](./TLSC.md) — Traffic light stop control
- driverd.py *(not implemented)* — DMS daemon
