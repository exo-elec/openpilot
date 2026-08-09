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
- Hailo-8 side-camera object detection (when the optional PCIe module is present)
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

### 2. Hailo-8 Camera-Based BSD

`sided` runs YOLOv8-nano on `side_left` / `side_right` cameras. Hailo-8 is an
optional PCIe module on ExoPilot 01M (RK3588) — without it, side cameras still
provide a visual overlay but no AI object detection.

`reard` (rear camera RCTA) shares the same physical Hailo-8 chip and the same
`HailoSideDetector` class. Both daemons run concurrently and reach the
accelerator only through `inferenced`'s IPC job queue (`use_ipc=True`), never
by opening a Hailo `VDevice` directly — the Hailo-8 only grants one process
exclusive device ownership, so two daemons each opening their own `VDevice`
would leave one of them silently undetecting. See "Hailo Backend" in
`docs/INFERENCED_ARCHITECTURE.md`.

| Hailo-8 | BSD Alert | Chime |
|---------|-----------|-------|
| ❌ Not present | Visual overlay only | ❌ No AI-detected blind-spot events to chime on |
| ✅ Present | Visual + fused blocking | ✅ If `EOPBSDChimeEnabled` |

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

## Gap Evaluation

When `EOPLCAGapEvalEnabled` is true, LCA performs active gap evaluation using both vision leads and radar (if available):

```python
# Simplified logic
def can_change_lane(direction, radar_state, model_v2, v_ego):
    adjacent_leads = get_adjacent_leads(radar_state, model_v2, direction)
    for lead in adjacent_leads:
        if is_in_blindspot(lead) and lead.vRel < -2.0:
            return False
    return True
```

| Check | Threshold | Description |
|-------|-----------|-------------|
| Blindspot distance | < 5 m laterally | Vehicle in adjacent lane |
| Relative velocity | < -2 m/s | Vehicle approaching from behind |
| Time gap | < 3 s | Insufficient time gap |

---

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `EOPLCAControllerEnabled` | 1 | Enable LCA |
| `EOPLCAGapEvalEnabled` | 0 | Active gap evaluation |
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
- **Hailo Detector**: `selfdrive/sided/hailo_side_detector.py`
- **Gap Evaluation**: `desire_helper.py` — vision + radar (if available)
- **Adjacent Lead Handoff**: `selfdrive/controls/lib/lc_lead_handoff.py` — pure camera

---

## Related Documents

- ALCC.md - Baseline lane centering
- DLAT.md - Dynamic Lateral Profile
- SOC.md - Smart Offset Control (post-lane-change positioning)
- BSD.md - Blind Spot Detection controller
