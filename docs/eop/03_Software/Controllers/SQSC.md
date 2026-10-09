# SQSC - Surface Quality Speed Controller

**Type:** Controller (runs inside `plannerd`)  
**File:** `selfdrive/controls/lib/sqsc.py`

---

## Overview
---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ COMPLETE |
| **Code** | ✅ `selfdrive/controls/lib/sqsc.py` |

---



SQSC provides speed limits based on road surface quality. It has two data sources:

1. **Real-time** — `surfaceStatus` from surfaced daemon (shocks, roughness score)
2. **Predictive** — GPS history DB (geohash lookup of previous drives)

Like MTSC/VTSC but for surface quality instead of curve geometry.

---

## Architecture

```
surfaceStatus (surfaced) ──► SQSC ──► speed limit → longitudinal_planner
gpsLocation              ──►
                              │
                              ▼ (record when not in traffic jam)
                    surface_quality.db ──────────────────────────────────►
                    (geohash + heading)          (also read as predictive)
```

---

## Speed Source Priority

```
Priority 1: Shock response (immediate) — already hit pothole/bump
Priority 2: Predictive GPS history — rough road known from previous drive
Priority 3: Real-time quality — roughness detected in current frame
```

Most restrictive (lowest speed) wins when multiple sources active.

---

## Profiles

| Profile | Quality Threshold | Speed Reduction | Min Speed (kph) | Use Learned |
|---------|-------------------|-----------------|-----------------|-------------|
| sport | 0.6 | 30% | 60 | No |
| balanced | 0.4 | 50% | 40 | Yes |
| comfort | 0.2 | 70% | 20 | Yes |

Parameter: `EOPSQSCProfile` = sport / balanced / comfort

---

## History Recording — Traffic Jam Guard

### Problem

The surface history DB stores the driver's observed speed at a GPS location.
This creates an ambiguity: a slow speed could mean:

| Reason | Should record? | Effect |
|--------|---------------|--------|
| Rough road / pothole | ✅ Yes | Correct — slows next pass |
| Traffic jam | ❌ No | Wrong — would limit speed on empty road |
| Curve (already in CSLB) | ❌ No | Duplicate — CSLB handles this |
| Red light queue | ❌ No | Wrong — transient, not a road property |

### Guard Conditions

SQSC only records to `surface_quality.db` when **all** of the following are true:

```python
should_record = (
    # 1. Moving at meaningful speed (not stopped in traffic)
    v_ego_ms > RECORD_MIN_SPEED_MS          # default 5 m/s = 18 kph

    # 2. Not tailgating (lead car absent or far away)
    and (lead_distance_m > RECORD_MIN_LEAD_M    # default 40m
         or lead_distance_m < 0)                # no lead car detected

    # 3. Surface has a reason (roughness detected OR shock active)
    and (surface_quality.score > RECORD_MIN_QUALITY  # default 0.25
         or shock_count > 0)

    # 4. Not currently in a curve section (CSLB handles curves)
    and not is_curve_section                    # curvature < CURVE_THRESHOLD
)
```

**Why check lead distance?**
Traffic jams cause bumper-to-bumper slow driving. Lead distance < 40m at slow speed is the clearest signal of congestion — not a road quality event.

**Why check roughness OR shock?**
Only record when there is a measurable surface reason. A normal road at low speed (speed bump not yet detected, or just urban traffic) should not pollute the DB.

**Why exclude curves?**
Curves already have their own DB (`curve_speeds.db` via CSLB). If a curve also has rough surface, both databases will learn independently and the most restrictive wins at runtime.

### Guard Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `EOPSQSCRecordMinSpeedKph` | 18 | Below this = traffic/stop, don't record |
| `EOPSQSCRecordMinLeadM` | 40 | Below this + slow = congestion, don't record |
| `EOPSQSCRecordMinQuality` | 0.25 | Must have roughness OR shock to record |
| `EOPSQSCRecordInterval` | 5.0s | Minimum seconds between recordings |

---

## Relationship with CSLB (Curve Speed Learning)

CSLB (`curve_speeds.db`) and SQSC (`surface_quality.db`) are **separate databases** queried independently. The most restrictive result from either applies at runtime.

| Aspect | CSLB | SQSC |
|--------|------|------|
| **Trigger** | Road curvature (radius < 500m) | Surface roughness OR shock |
| **DB file** | `/data/curve_speeds.db` | `/data/media/0/surface_quality.db` |
| **Key** | geohash + heading | geohash + heading (same scheme) |
| **Speed type** | Curve comfort speed | Surface safety speed |
| **Record guard** | Speed + curvature present | Speed + roughness/shock + no congestion |
| **Consumer** | MTSC (`get_curve_speed()`) | SQSC predictive phase |

**At query time in `longitudinal_planner.py`:**
```python
curve_limit = mtsc.update(...)       # from CSLB / OSM
surface_limit = sqsc.update(...)     # from surface_history + surfaceStatus
final_limit = min(curve_limit, surface_limit, v_cruise)
```

The two DBs remain separate to keep learning orthogonal — curve speed and surface speed are independent characteristics of a road segment.

---

## Phase Details

### Phase 1: Shock Response

```python
if shock.distanceM < 0.1:        # already hit
    shock_active = True
    shock_end_time = now + shock_duration_s

if shock.distanceM < 20.0 and shock.severity > 0.5:  # imminent
    shock_active = True
    shock_end_time = now + shock_duration_s

# While shock active: return shock_limit_kph / 3.6
```

### Phase 2: Predictive (GPS history)

```python
predictions = predictive_analyzer.update(lat, lon, heading, v_ego)
# Returns SurfacePrediction[] for 50, 100, 200, 300m ahead
# Picks most restrictive within max_distance_m
```

### Phase 3: Real-time

```python
quality = surfaceStatus.surfaceQuality.score * sensitivity
if quality > profile.quality_threshold:
    excess = (quality - threshold) / (1 - threshold)
    reduction = excess * profile.speed_reduction
    limit = max(base_speed * (1 - reduction), profile.min_speed)
```

### Phase 4: Record (with guard)

```python
if should_record(v_ego, lead_distance, surface_quality, is_curve):
    db.record_surface(lat, lon, heading, quality, shock_count, texture, v_ego)
```

---

## Parameters

```cpp
EOPSQSCEnabled               // 1 = enabled
EOPSQSCProfile               // sport / balanced / comfort
EOPSQSCSensitivity           // 0.5-2.0 quality multiplier
EOPSQSCLookaheadEnabled      // 1 = GPS history predictive mode
EOPSQSCUseLearnedSpeed       // 1 = use recommended_speed_ms from history
EOPShockDetection            // 1 = IMU shock detection
EOPShockSpeedLimit           // kph after shock (default 15)
EOPShockDuration             // seconds at reduced speed (default 3)
EOPSQSCRecordMinSpeedKph     // min speed to record (default 18)
EOPSQSCRecordMinLeadM        // min lead distance to record (default 40)
EOPSQSCRecordMinQuality      // min roughness to record (default 0.25)
EOPSQSCRecordInterval        // seconds between recordings (default 5.0)
```

---

## Files

- `selfdrive/controls/lib/sqsc.py` — controller
- `selfdrive/controls/lib/surface_history.py` *(not implemented)* — history DB (write path)
- `selfdrive/surfaced/surface_detector.py` — history DB (read path, surfaced side)
- `cereal/custom.capnp` — SurfaceStatus, SurfaceShock, SurfaceQuality structs

---

---

## Implementation

### Function

Quality-aware speed planning:
- Adjusts target speed based on road quality
- Considers comfort and efficiency

### Algorithm

```
Input: modelV2.meta.roadState, carState.vEgo
Output: targetSpeed, speedQualityScore
```

### Code Location

- `selfdrive/controls/lib/longitudinal_planner.py`


## Related

- MTSC.md — curve speed (separate DB, same query pattern)
- VTSC.md — vision curve speed
- SURFACED.md — upstream perception daemon
- SURFACE_ARCHITECTURE.md — full pipeline
