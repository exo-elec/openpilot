# Object protection: what EOP10 does, what NGP10 can prove, a middle way (2026-10-04)

> **Correction from the user (2026-10-04): NGP10 devices have no radar add-on and ship no AEB or FCW.**
> Read everything below with that: (1) there is nothing to carry from EOP10's AEB (no radar lead exists; `radarState` leads on NGP10 are the vision model's, `radar` false); (2) the lead-anchored ranging anchors on *vision* leads (`radarState`/`modelV2`), which are the same camera model, not an independent reference, so "range error vs radar" cannot be measured on these devices and needs an external reference (a radar-equipped test car, a range finder or surveyed targets); (3) Tier C (braking authority) is not applicable; (4) Tier A/B would be the *only* object-aware protection on the device beyond stock openpilot, so it needs the proof in section 4 even more. Note: the stock openpilot FCW code is still in the tree (`longitudinal_planner.py` `fcw = mpc.crash_cnt > 2`, and the model's `hardBrakePredicted` in `selfdrived.py`); whether it is switched off for the product is a product decision I have not changed.

Study only. Nothing here is implemented or run on a vehicle. Read from EOP10 `dev/EOP10` (`2af2ffbac`).

## 1. What EOP10's rule-based protection actually is

| Piece | Inputs | Authority | Portable to NGP10? |
|---|---|---|---|
| `controls/lib/aeb.py` (AEB: RSS distance, TTC levels CAUTION..EMERGENCY, precharge/brake/hold state machine) | `radarState.leadOne/leadTwo` **with `.radar` true** (the vehicle's own 77 GHz radar), `carState` | **Brakes**: clamps `actuators.accel` to the AEB decel, applied after the -2.5 m/s² comfort limit | Core math yes (pure after removing `Params`/`realtime`). Only works where the car has a radar lead. |
| AEB entry gate | range 0.75-120 m, ego >= 2 m/s, closing >= 1 m/s, radar confidence >= 0.70, TTC <= partial threshold, required decel >= 0.8 m/s², **3 consecutive frames** with continuous range (< 8 m jump) | - | Yes, same numbers are a good template |
| `controls/lib/radar_zones.py` (side/rear/cross-traffic zones, ISO 17387 TTCs) | BLE corner radars + side/rear cameras | **Advisory only** (docs: "does not feed longitudinal actuation or AEB") | No (needs corner radars and side/rear cameras) |
| `pathd/lon_nudge.py` (speed trim from drivable distance/occupancy/bike hazard) | `stereoGround`, gridd tracks | Soft: at most 30 % speed reduction, EMA 2 s, only when drivable distance < 40 m | No (needs stereo + gridd) |
| `pathd/soc.py`, `blindspot.py`, `lat_nudge.py` | gridd tracks, corner radar | Soft lateral | Partly: SOC basic already on NGP10 |

**The key design fact**: EOP10 already separates *who may brake* from *who may only warn*. `_collect_objects` documents a "hard boundary": BLE corner radar and camera-only tracks **cannot acquire braking authority**; they feed FCW/RCW/FCTA/RCTA advisory logic. Braking comes only from the built-in forward radar.

So "prove the YOLO objects in NGP10" cannot mean "let them brake". The camera objects stay in the advisory/soft tier on EOP10 too.

## 2. What NGP10 has for this

- `monoDetections` (road camera only, ranged from flat-ground geometry, per-class scale anchored on `radarState` leads, Kalman tracks, occlusion coast, 3 s path) - default off, publish-only.
- `radarState` from the car's radar where one exists (otherwise comma's vision lead).
- The comma vision model's 3 leads in `modelV2`.
- Not available: stereo, corner radar, side/rear cameras, gridd, BLE.

## 3. Middle way: a three-tier protection layer (default off, shadow first)

Tier A - **Object warning (advisory)**. A pure `ngp_object_guard.py` takes confirmed `monoDetections` tracks and computes, for objects inside the planned corridor, TTC and an RSS-style required decel, using the AEB gate numbers above (confirm frames, closing speed, range sigma, confidence). Output: a warning flag only (alert + chime). Coasting tracks (confidence 0) never warn.

Tier B - **Soft speed trim**. Same guard may ask the planner for a small speed/accel reduction, copying `lon_nudge`'s limits: reduce only, <= 30 % speed, EMA-smoothed, never beyond the -2.5 m/s² comfort limit, and only for an object that openpilot's own lead logic does *not* already follow (otherwise the model/radar already handles it). Needs Tier A proof first.

Tier C - **Braking authority**: **not granted** to camera-only tracks (same hard boundary as EOP10). A camera track may only *corroborate* a radar lead (it can lower the confirm-frame count or raise confidence of a radar lead; it cannot create a target). Revisit only with a measured false-brake rate.

Guards that apply to every tier (the "protection layer"):
1. Health: `monoDetections` fresh (frame age < 0.3 s), `modelExecutionTime` under budget, `modeld` not dropping frames, `liveCalibration` valid, speed >= ~8 m/s.
2. Ranging trust: enough lead-anchor matches this drive and a stable scale (`k_all` within 0.7-1.4); `source == 'ground'` preferred over class-height range; sigma growth rejects coasting objects.
3. Driver in charge: any brake/gas press or steering override cancels and holds off; standstill/reverse disabled.
4. Corridor from the *planned path* (`modelV2.position`, y-right -> flip once to left-positive), not a fixed straight width.
5. Classes: person, bicycle, motorcycle, car, bus, truck only.
6. Rate limit: at most one warning per N s for the same track id; hard off-switch param per tier.

## 4. How to prove it on NGP10 (shadow mode, no actuation)

1. Run `monod` + the guard in **shadow**: the guard evaluates every frame and writes "would have warned/trimmed" into a status message/log; the car is not touched.
2. Offline replay metrics over logged drives: range error vs `radarState` leads (median, p95), detection lead time vs the first `modelV2` lead / radar lead for the same object, false warnings per hour, missed hazards (hard-braking events with no prior warning), `modelExecutionTime` and `modeld` drop rate.
3. Promotion gates (numbers to be set after the first drives): Tier A on only when false warnings/hour and range p95 are under bounds; Tier B only after Tier A has run for N km without nuisance; Tier C stays closed.
4. Vehicle tests: closed track with a soft target, then public road in shadow.

## 5. Carry-over to EOP10

- The guard core lives in `nagaspilot/controls/ngp_object_guard.py` (pure) and is shared. On EOP10 an adapter feeds it from its RKNN `monoDetections` and gridd tracks (EOP10 already has FCW/RCW advisory logic; the shared core would replace duplicated TTC math, with an equivalence test like CAT/RED).
- EOP10's AEB core can be split the same way: a pure `ngp_aeb_core` shared with NGP10 for radar-lead cars; EOP10 keeps its adapter (`Params`, `driverAssistance` message).
- The lead-anchored ranging (and its health stats) is an improvement for EOP10's `monod` ranging (`calibration_fusion.py`) too.
- One publisher per service: NGP10's `monod` must not start on EOP10.

## 6. Not decided / open

- Where the guard runs (inside `selfdrived` like lead departure, or its own daemon publishing a status message). A schema addition (status/shadow message) would need an ordinal decision matching EOP10.
- Which alert Tier A raises: `EventName.fcw` is a critical full-screen alert with a chime, too strong for an unverified camera warning; a new, softer event needs an `EventName` ordinal (EOP10 `@109+` is taken, see ordinal notes).
- Numbers for the promotion gates.
- Wide (1.7 mm) camera ranging (fisheye model).
- Whether cars without a radar lead (vision-only) should get Tier A at all before Tier B is proven.
