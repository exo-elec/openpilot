# LCA - Lane Change Assist

**Type:** Controller (runs inside `controlsd` / `desire_helper.py`)  
**File:** `selfdrive/controls/lib/desire_helper.py` (integrated)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/controls/lib/desire_helper.py` (integrated) |
| **BSM Integration** | ✅ Vehicle CAN + Hailo-8 side camera fusion |
| **Active Cancellation** | ✅ BSD can abort mid-maneuver |
| **UI** | ✅ Toggle in EOP panel |

---

## Overview

LCA assists lane changes when the driver activates a turn signal. **Human steering nudge is the default**; automatic (nudgeless) lane change is opt-in via `EOPAutoLaneChange`.

When the driver activates a turn signal, LCA evaluates whether the adjacent lane is clear using:
- Vehicle-native BSM CAN signals (if available)
- Side-camera object detection (YOLOv8 on the SoC's RKNN NPU)
- Radar-based gap evaluation (if enabled)
- Lane width validation (if enabled)

If an object enters the blind spot **during** an active lane change, LCA cancels the maneuver and steers back to the original lane.

---

## Architecture

```
Turn signal ──► desire_helper.py ──► Gap evaluation ──► BSM/Hailo BSD check ──► Lane change desire
                    │
                    ├──► Active cancellation (mid-maneuver BSD detection)
                    └──► Adjacent lead handoff ──► longitudinal_planner.py
```

---

## Activation Conditions

All must be true simultaneously:

1. `EOPLCAControllerEnabled == 1`
2. Vehicle speed > minimum lane change speed (configurable via `EOPLatLCASpeed`)
3. Turn signal active (left or right)
4. **Human nudge** (`steeringPressed` + torque in signal direction) — default
5. **Gap evaluation passed** (if `EOPLCAGapEvalEnabled`)
6. **BSM/Hailo BSD clear** (fused vehicle CAN + camera-based detection)
7. **Lane width sufficient** (if `EOPLCALaneWidthEnabled`)

### Nudgeless Mode (Opt-In)

When `EOPAutoLaneChange == 1`, the driver does **not** need to apply steering torque. After `EOPLaneChangeDelay` seconds, the lane change starts automatically if all other safety checks pass.

> **Safety:** Nudgeless is disabled by default. Even when enabled, the driver can override at any time with steering input.

---

## Blind Spot Detection (BSD)

LCA fuses **two sources** of blind-spot data:

### 1. Vehicle-Native BSM (CAN)

| Signal | Source | Meaning |
|--------|--------|---------|
| `LEFT_BLINDSPOT` | Vehicle CAN | Vehicle detected in left blindspot |
| `RIGHT_BLINDSPOT` | Vehicle CAN | Vehicle detected in right blindspot |

Controlled by `EOPLCABSMEnabled`.

### 2. Camera-Based BSD (YOLOv8 on RKNN)

`sided` runs YOLOv8-nano on `side_left` / `side_right` cameras on the
SoC's RKNN NPU (core 1), left and right every frame, 20 Hz each. It does not
depend on the PCIe card, which only segments the side cameras (in `segd`).

`reard` (rear camera RCTA) uses the same `YoloDetector` class on the same NPU
core, in its own RKNN context, at 10 Hz. RKNN is on the die, so camera BSD does
not depend on anything that can be unfitted.

| RKNN yolo_640 model | BSD Alert | Chime |
|---------------------|-----------|-------|
| ❌ Missing | Visual overlay only | ❌ No AI-detected blind-spot events to chime on |
| ✅ Loaded | Visual + fused blocking | ✅ If `EOPBSDChimeEnabled` |

---

## Active Cancellation

If a blind-spot object is detected **while the lane change is in progress** (`laneChangeStarting`), LCA immediately:

1. Reverses the lane-change direction
2. Transitions to `laneChangeFinishing`
3. Steers back to the original lane

```python
# desire_helper.py — LaneChangeState.laneChangeStarting
blindspot_detected = self._blindspot_blocked(carstate, blind_spot_alert, self.lane_change_direction)
if blindspot_detected:
    # Abort back to original lane
    self.lane_change_direction = opposite_direction
    self.lane_change_state = LaneChangeState.laneChangeFinishing
```

This matches the proven FrogPilot pattern for mid-maneuver BSD intervention.

---

## Adjacent Lead Handoff

When `EOPLCAdjacentLeadHandoff` is enabled and the lane change enters `laneChangeStarting`, longitudinal control switches to track the **lead vehicle in the target lane** (from `modelV2.leadsV3`) instead of the current-lane lead.

This creates human-like gap behavior: as you merge, you naturally start following the car in the lane you're entering — rather than continuing to track the car you just left behind.

### How It Works

1. During `laneChangeStarting`, scan `modelV2.leadsV3` for leads with lateral position `|y| > 1.5 m` in the target lane direction
2. Select the closest adjacent lead (minimum longitudinal distance)
3. Inject it as `leadOne` into the longitudinal MPC
4. The original current-lane lead is demoted to `leadTwo` (fallback)
5. Handoff persists through `laneChangeFinishing` with a 1-second hysteresis
6. Lead distance is smoothed with a 0.3s first-order filter to prevent MPC jumps

> **Platform note:** Pure camera — no radar required. Uses vision leads only.

### Safety

- Only active above `LANE_CHANGE_SPEED_MIN` (11.0 m/s — EOP 3-zone spec, zone 1 ≤11 m/s disables ALC)
- If no adjacent lead is found, normal current-lane tracking continues
- Handoff is automatically disabled when the lane change completes

## Gap Evaluation and Lane Width

Both checks are pure functions in `nagaspilot/controls/ngp_lane_change.py`, shared with NGP10
(there from modelV2 only, opt-in). `desire_helper.py` keeps the state machine and calls them
while waiting in `preLaneChange`. Missing or malformed data never blocks.

**Gap** (`EOPLCAGapEvalEnabled`, default 1) looks at leads in the target lane, from `radarState`
(`yRel`, left positive) and `modelV2.leadsV3` (flipped once from the right-positive model frame):

| Check | Threshold |
|-------|-----------|
| Adjacent lane | lead more than 1.5 m to the target side; model leads need prob >= 0.5 |
| Fast approach | closing faster than 10 m/s blocks |
| Time to collision | closing, `dRel / |vRel|` < 2 s blocks |
| Level or pulling away | closer than 10 m blocks (confidence 0.3) |

**Lane width** (`EOPLCALaneWidthEnabled`, default 1): median of target-lane width at 5, 10, 20, 30
and 40 m. The road edge caps the outer boundary, so a close edge (shoulder) collapses the width.
Blocks below `EOPMinimumLaneWidth` (3.0 m).

On NGP10 the same checks sit behind `ngp_lat_lca_gap_eval` and `ngp_lat_lca_lane_width`
(default off) and use `modelV2` only: `radarState.leadOne/leadTwo` are radard's copies of
`modelV2.leadsV3[0/1]`, one frame later, so they would double-count.

---

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `EOPLCAControllerEnabled` | 1 | Enable LCA |
| `EOPLCAGapEvalEnabled` | 1 | Active gap evaluation |
| `EOPLCALaneWidthEnabled` | 1 | Target-lane width / shoulder check |
| `EOPLCAdjacentLeadHandoff` | 0 | Track target-lane lead during lane change |
| `EOPLCABSMEnabled` | 0 | Use vehicle BSM signals |
| `EOPLatLCASpeed` | 0 | Minimum speed for LCA (km/h, 0 = any speed) |
| `EOPAutoLaneChange` | 0 | **Nudgeless** auto lane change (opt-in) |
| `EOPOneLaneChange` | 0 | Single lane change limit per signal |
| `EOPLaneChangeDelay` | 1.0 | Delay before nudgeless lane change (s) |
| `EOPMinimumLaneWidth` | 3.0 | Minimum lane width (m) |
| `EOPBSDChimeEnabled` | 0 | BSD chime (requires side cameras) |

---

## Safety

| Feature | Implementation |
|---------|----------------|
| **Default mode** | Human nudge required (`EOPAutoLaneChange = 0`) |
| Driver override | Steering torque > threshold cancels lane change |
| Turn signal cancel | Signal off → cancel pending lane change |
| Speed check | Below minimum speed → block lane change |
| BSM veto (pre-start) | BSM/Hailo active → block lane change start |
| BSM veto (mid-maneuver) | BSM/Hailo active → abort and return to original lane |
| One-shot | `EOPOneLaneChange` prevents multiple consecutive changes |

---

## File Location

- **Implementation**: `selfdrive/controls/lib/desire_helper.py`
- **BSD Controller**: `selfdrive/controls/lib/bsd.py`
- **Side Camera Daemon**: `selfdrive/sided/sided.py`
- **Detector**: `selfdrive/sided/yolo_detector.py` (RKNN)
- **Gap Evaluation**: `desire_helper.py` — vision + radar (if available)
- **Adjacent Lead Handoff**: `selfdrive/controls/lib/lc_lead_handoff.py` — pure camera

---

## Related Documents

- ALCC.md - Baseline lane centering
- DLAT.md - Dynamic Lateral Profile
- SOC.md - Smart Offset Control (post-lane-change positioning)
- BSD.md - Blind Spot Detection controller
