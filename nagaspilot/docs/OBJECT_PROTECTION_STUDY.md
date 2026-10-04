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

## 7. Implemented 2026-10-04: cut-in speed trim (Tier B, cut-in only), opt-in

`nagaspilot/controls/ngp_cutin_speed.py` (pure), `nagaspilot/runtime/cutin_adapter.py`, hook in `longitudinal_planner.py` after BRSC (`NGPFlags.CUTIN`, param `ngp_lon_cutin`, default off; needs `ngp_monod_enabled`). For a confirmed, non-occluded track outside the corridor (|y| > 1.5 m) moving toward it (>= 0.3 m/s), predict the entry time (<= 3 s), the gap and closing speed at entry; trigger if TTC after entry <= 4 s or headway < 1.0 s. Response: lower `v_cruise` toward the object's speed, at most 25 % below current speed, not below 8.3 m/s, no action under 8 m/s, 2 consecutive confirmations, 2 s hold, never raises speed. No braking authority: the MPC still brakes (and still has no radar here). Objects already in the corridor are left to the lead logic. Gates: monod alive+valid, confidence >= 0.5, range sigma <= 25 % of range, range 3-80 m.
Tests: 9 pure tests (both sides, cap/floor, non-triggers, headway, hold/release, gates, track switching, adapter). The planner hook is not run (needs the built MPC). Not proven on a vehicle: use shadow-mode metrics (section 4) before turning it on; ranging error here has no independent radar reference.
Carry to EOP10: it can use the same pure policy from its gridd `trackedObjects`/monoDetections through an adapter; EOP10's AEB/radar authority split is unchanged.


## 8. Shadow-mode replay tool (2026-10-04)

`python3 -m nagaspilot.tools.replay_object_guard <route>...` runs a logged route through the same pure cut-in policy and reports, without actuating: `monoDetections` rate and `modelExecutionTime` (mean/p95/max), how often the cut-in trim would have triggered (and per hour, with times/tracks/target speeds), ranging agreement against `radarState` leads (median and p95 relative error; consistency only on radarless devices), and how much earlier the detector saw an object than a lead matched it. Needs a route recorded with `ngp_monod_enabled` on. Tested on synthetic logs only (2 tests); not yet run on a real route.

## 9. Improve EOP10 logic by proving it on NGP10 first (process + first finding, 2026-10-04)

Process, per EOP10 rule: (1) read it and run it on edge cases (done below for the AEB path check); (2) extract the logic into a pure `nagaspilot/controls/` core with an equivalence test against EOP10's current behaviour, so the starting point is pinned; (3) fix the weakness in the core with a test that fails on the old behaviour; (4) prove it on NGP10 with the shadow replay tool (section 8) on logged routes; (5) EOP10 becomes an adapter over the core (as CAT/RED/lead departure already did), and its own replay/tests show the same numbers.

**First finding (verified by running EOP10 `aeb.CollisionPredictor.check_collision`, `~/work/openpilot` at `498587926`)**: `_is_in_path` returns True for an object whose lateral speed is above 0.1 m/s and which would cross the centre line within 3 s, **without checking the direction**. An adjacent-lane car at y = +2.5 m moving away (vy = +1.0 m/s) is reported as an in-path threat, same as one moving toward the lane (y = +2.5, vy = -1.0); and y = -2.5 moving away (vy = -1.0) too. Also the corridor is a fixed straight width (2.5 m + object width), with no path curvature. Impact today is small: EOP10's AEB only feeds radar leads with `v_y = 0.0`, so the branch is latent, but any camera/BLE track that carries a lateral speed would hit it. NGP10's cut-in policy checks the direction (tested both sides, moving-away case returns no action) and takes the corridor half-width as a parameter.
Next on this list (not yet checked by running): the planner/path corridor from `modelV2.position` instead of a straight width, `radar_zones` TTC chosen by speed (its own comment says only the middle ISO 17387 value is used), and `lon_nudge` thresholds (fixed 40 m / 8 m, no speed scaling).

## 10. What Autoware Universe and Vision Pilot do, and what we take (2026-10-04)

Sources: Autoware Universe obstacle stop/cruise module docs (index.ros.org `autoware_motion_velocity_obstacle_stop_module`), Autoware Foundation `vision_pilot` README (github.com/autowarefoundation/vision_pilot), a TTC-metrics cut-in paper (arXiv 2511.21280, only skimmed through search results). The Vision Pilot README names the features (ACC, FCW, AEB, LKAS, LDW, ISA, single-lane autopilot; one front camera; Apache-2.0 incl. weights) and three models (AutoSpeed: closest in-path object; AutoSteer: ego-path waypoints; AutoDrive: distance/object presence and curvature) but **does not document its FCW/AEB thresholds or state logic**: read its source before copying any number. I did not read the source.

Autoware (documented parameters, not source):
- **Filter, then predict, then margin.** Obstacles are filtered by object type and by *lateral distance from the ego trajectory* (`max_lat_margin`, different margins for unknown objects), crossing obstacles are handled separately, and the future position of an "outside" obstacle over a time horizon is considered (our cut-in prediction does the same).
- **Collision time margin**: ego and obstacle both move at constant velocity; the *difference between when each occupies the collision area* is the margin; below `collision_time_margin` the obstacle is kept as a target. Better than raw TTC for crossing/cut-in because it asks "do we arrive at the same place at the same time", not just "are we closing". Worth adding to our policy.
- **Insert a stop/slow point into the trajectory** with a margin (`stop_margin`) instead of braking hard, and **cancel the stop if the required decel is below a strong-decel threshold**, to avoid sudden stops. Slower lead: assume a constant decel for the obstacle and keep a margin from where it will stop. These match our "only tighten the speed target" rule; the cancel-below-threshold idea maps to our hold/release and MIN_TARGET_SPEED.
- **Separate modules**: cruise (follow) vs stop (static/slow) vs emergency braking. Same split as EOP10's lead logic vs AEB.

Vision Pilot takeaway: with one camera it still depends on a *most-important in-path object* model and an ego-path estimate, i.e. the same "closest in-path object" idea as the lead model; its cut-in handling is not documented.

**What this means for the 3-leads limit.** openpilot's model gives 3 leads in the lane, picked by the model. Autoware-style logic evaluates *all* filtered obstacles against the ego trajectory with predicted paths; that is what `monoDetections` tracks + our path prediction allow. The protection layer should therefore (1) take every confirmed track, (2) filter by class and lateral margin to the *planned path*, (3) score by collision time margin, (4) only tighten speed.

**Bike and car cut-in (user, 2026-10-04: the usual failure).** Implemented now as per-class profiles in `ngp_cutin_speed.py` (`PROFILES`): car/truck/bus vs motorcycle/bicycle. Two-wheelers use a narrower corridor (1.3 m), accept slower lateral drift (0.2 m/s), trigger at TTC <= 5 s or headway < 1.3 s, and may cut speed up to 30 % (cars: 1.5 m, 0.3 m/s, 4 s, 1.0 s, 25 %). These numbers are starting values, not validated: tune with the replay tool on logged Thai-road routes. Pedestrians and unknown classes are ignored by this policy (crossing pedestrians need their own rule). EOP10 only has a boolean `bike_hazard` from `stereoGround` (stereo-only) with a fixed 12 % bump and no class-aware prediction, so this is also an improvement to carry over.

Next candidates from this study: collision-time-margin scoring (replace raw TTC), corridor from the planned path (`modelV2.position`) instead of a fixed half width, filtering by lateral margin to predicted path, and a speed-dependent threshold set.
