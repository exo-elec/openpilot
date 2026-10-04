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

## 11. Planned-path corridor (implemented 2026-10-04)

`ngp_cutin_speed.evaluate` now measures an object's lateral offset from *our planned path* (`PlannedPath`, from `modelV2.position`, y-right flipped once in `cutin_adapter.cutin_path`) instead of the car's straight centre line. It steps the object forward at constant relative velocity (0.1 s) to the first time its centre is inside the class corridor around the path, then applies the TTC/headway tests at that moment. On a curve the corridor follows the road: an adjacent-lane car on the inside of a bend counts as already in the lane (left to the lead logic), and one outside the bend is judged against where the path really is. Falls back to the straight corridor when the model path is missing or invalid. The replay tool uses the logged `modelV2` path too. Tests: curve vs straight, interpolation/validity, bad-path fallback, the y-flip, replay with a logged path (21 pure tests in the cut-in set). Autoware's collision time margin is the same as the headway at entry for same-direction constant-velocity cut-ins, so no extra trigger was added. Ego yaw rate is still ignored in the relative-motion prediction (adequate for the 3 s window at highway curvature; unchecked at tight turns).


## 12. pathd as the rule-based add-on layer: what EOP10 does, and the NGP10 base layer (2026-10-04)

**EOP10 `selfdrive/pathd` (read, not run)**: the module docstring says pathd's job is emergency longitudinal avoidance for objects stock openpilot misses, plus occupancy-grid speed reduction. Pipeline: gridd/stereo -> `track.py` (cluster tracker) -> `predict.py` (constant velocity, 3 s, threat level from TTC and lateral position) -> `compute_speed_reduction` -> `LatNudge`/`LonNudge` (stereo boundary lateral nudge; speed trim <= 30 %) -> published as `enhancedTrajectory`, which `controlsd` (curvature, SOC offset) and `longitudinal_planner` (speed limits, `lhlc*` fields) consume.
**Hybrid A\* is not in the policy loop.** `hybrid_astar.py` (805 lines, from Autoware's freespace_planner and Dolgov et al.) is used only by `OsmHybridPlanner` for the 500 m long-horizon path (LHLC), behind `EOPHybridPlannerEnabled`, and needs `mapData` (OSM) and `gridObjects` (BEV occupancy grid). So steering/accel add-ons today are the rules above, not a search planner.

**Why Hybrid A\* does not go to NGP10**: it needs an occupancy/drivable grid and a map; a comma 3 with one forward camera has neither. The base-layer alternative is a **Frenet-style sampler** around the policy's own path: few lateral offsets, predicted objects, a cost for clearance/comfort/deviation.

**Implemented (pure, not wired)**: `nagaspilot/controls/ngp_path_selector.py` (`PathSelector`). Inputs: policy path (`PlannedPath` from `modelV2.position`), tracked objects (class, position, relative velocity), room inside the lane each side. It samples offsets (0.1 m steps, at most 0.6 m and never more than the room), predicts objects over 3 s, scores clearance against class-specific wanted gaps (car 0.8 m, truck/bus 1.0 m, motorcycle 1.1 m, bicycle/person 1.2 m), deviation and change from the last output, and returns `offset_m` (left positive) and `speed_factor` (only <= 1.0, at most 20 % down, used when no offset in the room clears the gap). Bounded, no braking authority; objects alongside (slightly behind) are included. 13 pure tests: clear road, truck alongside on either side, motorcycle filtering, no room -> slowdown not nudge, offset cap, low-confidence/behind ignored, curved path, smoothness, nudge alone suffices.
Unvalidated: wanted gaps, weights and caps are starting values; ego yaw rate ignored; no lane-line "room" adapter yet.

**Not done (needs your decision)**: wiring. Options: (a) a small `pathd` daemon at the base layer publishing a new message (needs an Event ordinal and a schema struct, EOP10 would map it to `enhancedTrajectory` fields), consumed by `controlsd` (offset added like SOC) and the planner (speed cap beside the cut-in trim); (b) no new daemon: call the selector in `controlsd`/planner like the existing SOC/cut-in hooks. Recommendation: (a) with the daemon at `nagaspilot/runtime/pathd.py`, because it keeps upstream-file hooks to a few lines and gives EOP10 one message to read; shadow-replay first (extend `replay_object_guard`).

## 13. pathd add-on daemon, publish-only (implemented 2026-10-04, option (a))

`nagaspilot/runtime/pathd.py` (process `pathd`, only with `ngp_pathd_enabled`, default off) reads `modelV2`, `carState`, `monoDetections`, runs `PathSelector` with the model's planned path and the lane room from `modelV2` lane lines (`path_adapter.lane_room`: model y-right; an unseen line gives zero room on that side) and publishes **`pathAdjust`** (20 Hz): `frameId`, `offsetM` (left positive), `speedFactor` (<= 1), `minClearanceM` (NaN with no object), `reason` (off/clear/nudge/slow), `roomLeftM`, `roomRightM`, `numObjects`. Schema: `custom.capnp` `PathAdjust @0x86ee74962cb31a1d`, `log.capnp` Event `pathAdjust @152`, service `pathAdjust`. A real pycapnp round-trip of the message passed; the daemon loop itself is unrun. **No consumer**: `controlsd` and the planner do not read it, so it cannot move the car. `replay_object_guard` now also reports the selector (nudge fraction, max/p95 offset, slow frames, minimum speed factor) from logged `modelV2` + `monoDetections`.
Next: replay real routes, tune gaps/weights, then add the two consumers (offset in `controlsd` like SOC, speed factor in the planner next to the cut-in trim) behind separate opt-in params. EOP10 would map `pathAdjust` onto its `enhancedTrajectory` fields (carry-over, not done); the process name `pathd` is also EOP10's daemon: do not start both on one device.

## 14. pathAdjust consumers (implemented 2026-10-04, opt-in)

`nagaspilot/controls/ngp_pathd_consumer.py` (pure, 8 tests): `PathAdjustFollower` clamps the offset to +-0.6 m, slews it at 0.15 m/s in and out (never a step), and releases it when the message is stale or the driver is in charge; `speed_cap` turns the speed factor into a cap that only lowers `v_cruise` (floor 8.3 m/s, nothing under 8 m/s). Hooks: `controlsd` (`ngp_lat_pathd`: adds `offset * BIAS_PER_METER` curvature, same gain as SOC; allowed only with lateral active, no steering press, no blinker, no lane change; when on, SOC's own offset is skipped so the two never add) and the planner (`ngp_lon_pathd`, `NGPFlags.PATHD`, after BRSC/cut-in). Both need `ngp_pathd_enabled`; all default off. Budget lines named: `controlsd.py` +10/-2, `longitudinal_planner.py` +8, `plannerd.py` +2. Hooks and daemon unrun on a device; stale `pathAdjust` means no action.

## 15. Control style for pathd: decision (2026-10-04)

Options considered:

| Style | What pathd outputs | Strength | Weakness |
|---|---|---|---|
| A. Scalar nudge (what 13/14 first did) | one offset + one speed factor | simplest, 2 hooks | each new add-on needs its own hook and its own way of adding to curvature/speed; conflicts are implicit (SOC + pathd both add) |
| B. Replacement trajectory (EOP10's `enhancedTrajectory`, Hybrid A*) | a full path + speed profile that replaces/blends with the policy | most expressive, fits a real planner | needs a grid/map to be safe; takes authority from the policy; hard to verify; one big hook |
| C. Proposer + envelope, tighten-only arbitration (**chosen**) | small bounded proposals (offset, speed cap), merged by one arbiter, plus a horizon profile | policy stays primary; any proposer (rules, sampler, Hybrid A*, a learned model) plugs in without new hooks; the merge rules are the safety argument and are unit-testable; message carries a profile so an MPC can consume it later | less expressive than B: it cannot make a big evasive path (by design) |

Why C is the future-proof choice: it does not depend on how the policy works (modeld today, an end-to-end or bigger model later), it composes (SOC, RED, cut-in trim and pathd are already four proposers), it keeps the verification surface to two small functions (`arbitrate`, the consumers' slew/clamp), and B stays reachable: a Hybrid-A* proposer on a board with a grid (EOP10) just emits Proposals, or fills the profile.

Implemented: `nagaspilot/controls/ngp_arbiter.py` (speed = minimum of caps, never raises; lateral: same-side offsets do not add, the largest wins; opposite sides conflict to zero offset; bounded by lane room and 0.6 m); `controlsd` now merges SOC and pathd through it (one curvature bias = arbitrated offset * 0.002); speed caps (pathd, cut-in trim) already combine by `min` in the planner, which is the arbiter's speed rule; `PathAdjust` carries a horizon profile (`horizonDt`, `offsetProfile`, `speedCapProfile`, append-only fields @8-@10, schema checked by a capnp round-trip) built by `build_profile` at the consumers' slew rate; scalar consumers use only the first element.

Simulation without a vehicle: `python3 -m nagaspilot.tools.sim_scenarios` runs a toy closed loop (ego point mass, stand-in for stock lead-following, scripted traffic: car/bike cut-ins, truck alongside, filtering bike, steady and receding adjacent cars) with the layer off and on. Current results: car cut-in min gap 4.5 -> 11.1 m, min TTC 1.5 -> 6.1 s; bike cut-in 4.0 -> 7.6 m, 1.7 -> 17.2 s, no overlap; alongside traffic gets 0.2-0.3 m nudges with no slowdown; steady/receding cars cause no action. Same direction with 5 % range noise. It tests the logic and tuning direction only (first-order ego response, no real MPC, no perception error beyond range noise). 4 tests.
For MetaDrive/other simulators: feed `monoDetections` from ground truth (class, x forward, y left, relative velocity, sigma, confidence) and run with `ngp_monod_enabled` off and the detector replaced by the bridge; `ngp_pathd_enabled`, `ngp_lat_pathd`, `ngp_lon_pathd`, `ngp_lon_cutin` on.

## 16. pathd as a parallel rule channel and DPP (user, 2026-10-04; coded as pure cores, not wired)

The user's point: a protection layer is not enough; pathd should also run **in parallel to the policy model**, fed by the monod rule pipeline, as an optional alternative channel. The chosen style (section 15) already allows it: the protection layer is the *lowest* authority of a ladder, and a full rule planner is just a stronger proposer. Authority is staged so each step can be proven before the next:

| Mode | Authority of the rule channel | Use |
|---|---|---|
| OFF | none | perception unhealthy |
| SHADOW | none; runs and is measured (disagreement with the policy) | default, proof in sim/replay |
| SUPERVISE | accel = min(policy, rule), curvature stays the policy's | brake-only protection |
| PRIMARY_LONG | accel from the rule channel (jerk-limited) | following, cut-ins |
| PRIMARY_LAT | curvature from the rule channel (slew-limited) | traffic alongside in clear lanes |
| PRIMARY_BOTH | both | opt-in ceiling only |

Always back to the policy on stale/invalid rule output or driver override; in PRIMARY modes a 1 s disagreement (curvature 0.004 1/m or accel 2 m/s^2) hands control back for 3 s, keeping the more conservative accel.

New pure cores: `ngp_rule_planner.py` (`RulePlanner`: lane centre + `PathSelector` offset -> pure-pursuit curvature; IDM car-following on the nearest in-lane object, a predicted cut-in, or a radar lead; speed capped by set speed, a 2 m/s^2 lateral-acceleration curve limit and the selector's slowdown; brake limit -4 m/s^2), `ngp_policy_arbiter.py` (`PolicyArbiter`, the table above), and **`ngp_dpp.py` (DPP, Dynamic Path Planner)**: like DLAT/DLON, an automatic selector of the mode by case, inside a user ceiling (`ngp_dpp_max_mode`, not yet a param): cruise -> SHADOW; cut-in predicted -> PRIMARY_LONG; lead inside 1.2 s headway -> SUPERVISE; truck/bus/two-wheeler alongside with confident lanes and a straight road -> PRIMARY_LAT, with weak lanes -> SUPERVISE; weak lanes or a tight curve -> policy; unhealthy perception/rule or driver override -> OFF/SHADOW at once; policy-rule disagreement -> SHADOW. Escalation needs 0.3 s, de-escalation 2.5 s dwell, urgent drops are immediate, a lowered ceiling applies at once. 5 tests; rule planner 10; policy arbiter 10.
Toy closed loop (`sim_scenarios`, third row per scenario, rule planner as the only controller): car cut-in min gap 4.5 -> 12.0 m, bike cut-in 4.0 -> 8.0 m, no overlap; no action when nothing threatens; 0.2-0.3 m offsets beside a truck or filtering bike. Same ballpark as the protection layer, with the rule planner able to run alone.
**Not done**: nothing here is wired into `controlsd`/planner/pathd; `Situation` needs an adapter (lane confidence from `modelV2.laneLineProbs`, curvature from the lane polyline, `cut_in_risk`/VRU flags from the tracks, `disagree` from the arbiter's d_curv/d_accel, perception health from monod's exec time and message age); params `ngp_dpp_max_mode` and a mode for the shadow log; the policy/rule disagreement belongs in `replay_object_guard`. Also: the lane polyline comes from the policy network's *perception* heads (`modelV2.laneLines`), so the channels are independent in planning but share lane perception; a second lane source (monod segmentation on EOP10) is the way to make them independent in perception too.

## 17. Wiring of the parallel rule channel (implemented 2026-10-04, all default off)

Flow: `pathd` (process behind `ngp_pathd_enabled`) reads `modelV2` (lane lines, planned path, policy action), `carState`, `radarState`, `monoDetections` and publishes `pathAdjust` with the protection request AND the rule channel: `ruleValid/ruleCurvature/ruleAccel/ruleSpeedTarget`, `dppMode`, `dppCase`, `disagreeCurvature/Accel` (schema fields @11-@18, append-only). `RuleChannel` (pathd side, `nagaspilot/runtime/rule_channel.py`) builds the lane-centre polyline from `modelV2` (model y-right flipped once; falls back to the planned path with lane confidence 0), runs `RulePlanner`, measures the disagreement with the policy action in `modelV2.action`, builds DPP's `Situation` (cut-in from `ngp_cutin_speed.evaluate`, two-wheeler/person or truck/bus alongside, lane confidence, curve, lead gap, perception health = monod fresh + `modelExecutionTime` <= 0.15 s + `modelV2` valid, driver override) and lets DPP choose the mode inside `ngp_dpp_max_mode` (0 idle, 1 shadow ... 5 primary both; read every second, so it can be changed while driving). Consumers: `controlsd` (`apply_curvature`) and `longitudinal_planner` (`apply_accel`) each own a `PolicyArbiter` and apply the published mode to THEIR real policy value; one line each. Without `pathd`, with a stale or invalid message, with mode < the authority needed, with lateral/driver override: the policy value passes through unchanged and the arbiter state is reset. The arbiter now applies the rule channel as a **slewed correction on top of the policy** (curvature 0.004 1/m/s, accel 2.5 m/s^3) instead of replacing it, so the policy's own dynamics pass through, authority builds smoothly and it converges to the rule command; a sustained disagreement decays the correction and hands back to the policy (keeping the more conservative accel).
Switches: `ngp_pathd_enabled` (process), `ngp_dpp_max_mode` (ceiling; 0 = rule channel idle), `ngp_lat_pathd` / `ngp_lon_pathd` / `ngp_lon_cutin` (the protection-layer hooks). Everything default off; turning DPP off at runtime = ceiling 0 (pathd publishes mode 0 within a second).
Checked: 24 pure tests (rule channel pathd side, consumers, arbiter, sim), a real pycapnp round trip of the new fields. NOT run: `controlsd`/planner/pathd loops on a build or in a simulator. Budget lines named: `controlsd.py` +5, `longitudinal_planner.py` +4.

## 18. Proving it on NGP10: the toolchain (2026-10-04)

What can be proven where, and the code for each (all pure, run from `python3 -m nagaspilot.tools.<name>`; exit code 1 when a gate fails):

| Claim | Evidence | Tool | Needs |
|---|---|---|---|
| Ranging from the box (flat ground + lead anchor) is accurate | independent reference: EOP10 stereo depth or a real radar lead, NOT the same camera model | `validate_ranging` (published vs ngp vs anchored, error by distance band and class, gates: median <= 8 %, p90 <= 20 %, beats the published prior) | an EOP10 route with `monoDetections` boxes + `stereoObjects`/`radarState` |
| Cut-in/path/DPP logic is right and tuned in the right direction | closed-loop toy sim | `sim_scenarios`, `scenario_sweep` (Monte-Carlo cut-ins with noise and dropouts, benign traffic for false triggers; gates: never worse than the stand-in policy in >= 95 % of runs, no extra overlaps, < 2 % false triggers) | nothing |
| Safe under perception faults | fault injection in the integrated loop (`controller='dpp'`, `dropout`) | `test_scenario_sweep` (total outage -> mode OFF and identical to the policy; short outages do not flap the mode) | nothing |
| It runs in a simulator | ground-truth `monoDetections` incl. boxes, with noise/dropout | `sim_bridge` (`ground_truth_detections`, `publish`; box projection round-trips through the NGP10 ranging) | a simulator that gives actor poses |
| It behaves on real drives | log replay: detector rate/exec time, would-trigger counts, selector stats, rule-channel mode/disagreement | `replay_object_guard` | a route recorded with the flags on |

**EOP10 change that makes the first row possible**: `selfdrive/monod/monod.py` now publishes the detector's box (`u, v, w, h`, normalised) in `monoDetections`; before, it published only the ranged position, so no log could re-range a box. EOP10 ranges with the class-height prior alone (`height_m * focal / box_h`, no calibration anchor), which is exactly what the NGP10 ranger improves; EOP10's stereo depth is the independent reference. So an EOP10 drive proves or disproves NGP10's ranging before any comma 3 exists.

**What the sim already taught (and changed)**: the first integrated run (stand-in policy + rule channel + DPP + arbiters) improved a car cut-in minimum gap only 4.5 -> 4.6 m while the rule planner alone reached 12.0 m. Causes found and fixed: DPP waited 0.3 s before escalating (now 0.1 s for cut-in/close-lead), the arbiter built braking authority at 2.5 m/s^3 (now 6 m/s^3 for MORE braking, 2.5 to release), and, the biggest, a rule channel that brakes harder than the policy was counted as a "disagreement" and pushed DPP back to SHADOW (now only a rule channel that is LESS cautious counts). After the fix: car cut-in 4.5 -> 9.9 m (TTC 1.5 -> 3.4 s), bike cut-in 4.0 -> 6.8 m (TTC 1.7 -> 9.1 s), no overlap. 60-run sweep (random gap/speed/lateral speed/class/noise/dropout): baseline overlaps in 14 runs, layer 4, rule planner 5, integrated DPP 7 (more outages are treated as unhealthy and handed to the policy by design); no run worse than the baseline; false-trigger rate 0 % for all. Remaining tuning targets are visible in that table (DPP's overlap count vs the layer's), not hidden.
**Limits stated plainly**: the sim ego is first-order, the stand-in policy is a hold-speed + lead-follow rule, and the sweep's perception errors are range noise and frame dropouts only; none of it is a statement about the real car. The ranging and replay tools have been tested on synthetic logs only until a real route exists.

## 19. Status and what is left (2026-10-04)

Built, pure-tested, carried to EOP10 / 01M / 02M, all default off: YOLO decode, flat-ground ranging with lead anchor, Kalman tracker, cut-in trim (per class, planned-path corridor), path selector, arbiter, parallel rule channel (rule planner + policy arbiter + DPP), `pathAdjust` with horizon profile, consumers in `controlsd`/planner, EOP10 `pathadjustd` + `MonoTrackFeed`, proof tooling (`validate_ranging`, `scenario_sweep`, integrated sim with fault injection, `sim_bridge`, replay stats), EOP10 `monod` box publish and the ground-plane ranging hook (`ngp_monod_ranger`).

| Switch | Where | Default |
|---|---|---|
| `ngp_monod_enabled`, `ngp_monod_hz` | NGP10 monod runtime | off |
| `ngp_pathd_enabled` | pathd (NGP10) / pathadjustd (EOP10, 01M, 02M) | off |
| `ngp_dpp_max_mode` (0-5) | DPP ceiling, runtime-changeable | 0 (idle) |
| `ngp_lat_pathd`, `ngp_lon_pathd`, `ngp_lon_cutin` | protection-layer hooks | off |
| `ngp_monod_ranger` | EOP10 / 01M / 02M monod ground ranging | off |

Left, in order of what unblocks what:
1. **A real EOP10 stereo route -> `validate_ranging`.** Decides `ngp_monod_ranger` on EOP10 and is the only independent evidence for NGP10's ranging. Nothing has seen a real route.
2. **Simulator run by the user** with `sim_bridge` + `ngp_pathd_enabled` + `ngp_dpp_max_mode`: tune DPP thresholds (`ngp_dpp.py`), the disagreement limits (`rule_channel.py`), profile gaps (`ngp_path_selector.py`, `ngp_cutin_speed.py`).
3. **NGP10 device checks**: tinygrad runner, NV12 conversion, GPU time beside `modeld`, `camerad` with no driver sensor, the unrun loops (`monod`, `pathd`).
4. **Licence of the YOLO weights** (Ultralytics = AGPL-3.0); wide-camera (fisheye) ranging; a second lane source so the rule channel's lane perception is independent of the policy network.
5. **Rebase hygiene on 01M/02M**: re-add `pathadjustd` in `nagaspilot/manager/process_config.py`, re-apply any EOP10 `monod.py` edit to `nagaspilot/daemons/monod/monod.py`, re-run `footprint.py --update`, diff the README afterwards (done for every propagation so far).

## 20. Layering decision: sensing / perception / planning (user, 2026-10-04)

Mapped to Autoware's layers: **sensing = monod and stereod** (detectors and depth: pixels in, metric detections out), **perception = gridd** (fusion, tracking, velocity, prediction), **planning = pathd** (the only planner). Consequences, applied today:
- **One process name, `pathd`, on every branch.** `pathadjustd` and `eop_pathadjustd.py` are removed. NGP10 registers `nagaspilot.runtime.pathd`; EOP10/01M/02M keep their `pathd` (`selfdrive.pathd.pathd`, relocated to `nagaspilot/daemons/pathd/` on 01M/02M) and host `SharedPathdHost` inside it (default off, `ngp_pathd_enabled`). The same planner code runs everywhere; the board's own planner results join it as proposals (`extras_from_eop`: LatNudge/SOC offset flipped once from the path's y-right frame, `speed_reduction` + LonNudge as a factor >= 0.8) into the same tighten-only arbitration.
- **Objects reach pathd through one interface** (`runtime/object_sources.py`): `MonoDetectionsSource` (NGP10: monod's own tracks) and `GriddSource` (EOP10/01M/02M: gridd's fused `stereoObjects` incl. `vRel`, `vyRel`). pathd does no tracking and no sensor fusion.
- **gridd now produces what the planner needs**: its camera objects had `vRel` 0 and no lateral speed (the Kalman tracker in `gridd/tracker.py` was not wired to `stereoObjects`). `CameraTrackAnnotator` (`runtime/fusion_tracks.py`, the shared tracker) annotates fused camera objects with `vRel` (only where there is no radar Doppler) and `vyRel` (new `CameraObject.vyRel @17`). Radar objects keep their Doppler.
- **Stereo refinement stays in gridd** (it already blends stereo depth into mono ranges within 30 m); the pathd-side `StereoAssistedFeed` I started was dropped as a layer violation.

Where the layers are still blurred (honest list):
1. NGP10 has no gridd, so **monod's runtime tracks** (perception work inside a sensing process). Fix: a `gridd` process on NGP10 that hosts `ObjectTracker` and publishes the same fused-object message (needs `CameraObject`/`stereoObjects` in NGP10's schema, or a smaller shared message), leaving monod as detection + box ranging.
2. **EOP10's pathd keeps its own cluster tracker and 3 s predictor** (`pathd/track.py`, `predict.py`): perception inside the planner. Leave until the shared planner has proof; then move prediction to gridd (`TrackedObjects`, which already carries predicted trajectories) and let pathd consume predicted paths.
3. **Cut-in prediction (`ngp_cutin_speed.evaluate`) runs in the planner's rule channel.** In Autoware terms it is prediction; it should consume gridd's predicted paths instead of extrapolating itself. Same fix as 2.
4. **monod's ground-plane ranging** (`ngp_monod_ranger`) is sensing (box -> metric), which is the right layer.
Verified: EOP10 gridd tests, pathd test, `test_controlsd_smoke`, `test_selfdrived_smoke` and the new unit tests (69 passed) on the built clone; 148/147 pure tests on 01M/02M. NOT run: a drive, `pathd` with the param on (the clone's compiled Params predates the new keys).

## 21. gridd on NGP10 (implemented 2026-10-04): blur point 1 closed

NGP10 now has the three layers as separate processes: **monod** (sensing: box -> metric detection, lead-anchored ranging, publishes UNTRACKED `monoDetections`; `MonoPipeline.detect`, `fill_raw_detections`), **gridd** (perception, `nagaspilot/runtime/gridd.py`, registered as process `gridd` next to monod under `ngp_monod_enabled`), **pathd** (planning, now reads `GriddSource` by default on NGP10 too). gridd runs the shared Kalman tracker (`CameraTrackAnnotator`, which now also assigns stable track ids where the source has none) and publishes **confirmed tracks only** as `stereoObjects` (`dRel`, `yRel`, `vRel`, `vyRel`, class enum, probability, id): the same message EOP10's gridd publishes. If monod goes stale gridd publishes nothing, so pathd's freshness check sees a missing detector instead of an empty road. Schema: `CameraObject` and `StereoObjects` ported to NGP10 with EOP10's struct ids (Event `stereoObjects @153` here, `@155` on EOP10; service `stereoObjects`). Verified with a real pycapnp round trip: gridd -> `stereoObjects` -> `GriddSource` -> planner objects with velocity (a car closing at 8 m/s drifting 1.5 m/s toward the lane reads vx -7.9, vy -1.48, id 1). Tests: 4 gridd, tracker-id test, monod `detect` test; the earlier pathd/host tests unchanged. NOT run: the processes together on a device. The perception-layer blur points that remain are EOP10's: its pathd tracker/predictor and the planner-side cut-in extrapolation (section 20, items 2-3). The shared `MonoTrackFeed` and `MonoDetectionsSource` stay as unused fallbacks.

## 22. Strategy: port EOP10's pure logic to the NGP10 base, prove it there, EOP10 carries it and adds on (user, 2026-10-04)

Rule: anything in EOP10 that is pure logic moves into `nagaspilot/controls/` (shared), is **pinned by outputs recorded from the original** (golden test), is proven on NGP10 in the sim/sweep, and EOP10 keeps a thin shim or wrapper and adds what only it can (stereo, grid, radars, side cameras). Nothing is rewritten twice.

| EOP10 piece | Status | Where | Proof |
|---|---|---|---|
| `LatNudge`, `LonNudge`, `predict` (`selfdrive/pathd`) | **ported verbatim** | `ngp_lat_nudge.py`, `ngp_lon_nudge.py`, `ngp_predict.py`; EOP10 files are shims | `eop_golden.json` (outputs of the originals) reproduced exactly; EOP10 `test_shared_cores.py` |
| `compute_speed_reduction` (pathd) | **ported, one fix** | `ngp_speed_reduction.py` (`legacy_scale_bug=True` = EOP10's old behaviour); EOP10/01M/02M pathd call it, default unchanged | golden (legacy) + fix tests; sweep |
| as proposers on camera-only inputs | **done** | `runtime/nudge_extras.py` (`NudgeExtras`: boundaries from `modelV2` lines, objects from gridd) -> `Extras` into `SharedPathdHost`; param `ngp_pathd_nudges` (default off) | sim controllers `eop`, `eop_legacy`; 9 tests |
| `gridd` fused camera objects with velocity | done earlier | `runtime/fusion_tracks.py` + `runtime/gridd.py` | tests, capnp round trip |
| `aeb.py` RSS/TTC core, `radar_zones.py`, `blindspot.py`, `lane_change.py` gate | **not ported**: need radar / corner radar / side cameras (NGP10 has none; no AEB product) | EOP-only for now; the AEB core could be split into a pure part later | - |
| `track.py` cluster tracker, BEV `OccupancyGridView`, `hybrid_astar`, `path_corridor_fusion`, `soc.py` | **not ported**: need the BEV grid / stereo / segmentation (EOP-only perception) | EOP10 keeps them; they feed the same `Extras`/`stereoObjects` interfaces | - |

**Findings from proving the ports on NGP10** (all in the 60-run sweep, `scenario_sweep`; baseline cut-in overlaps 14):
1. **EOP10's `compute_speed_reduction` never reduced speed**: `DISTANCE_SCALE_THRESHOLDS` is a lower-bound table, the loop tested `<=`, the first entry (`inf`, scale 0) always matched. Pinned by the golden test (every recorded case returns 0.0 or inf). The shared core fixes the lookup; `ngp_pathd_fix_scale` (default off) lets a branch adopt it, so EOP10's behaviour does not change silently.
2. **`LatNudge` is off above 80 km/h** (by design) and **ignores object width** (it uses the track centre minus 0.5 m): a truck alongside never nudges it; the shared selector accounts for width and class.
3. **EOP10's proposers are reactive**: they act on objects already in the lane (`|yRel| <= 1.8`) with no prediction. Cut-in overlaps: baseline 14, EOP10's original logic 14, with the fixed scale 14 (median gap gain 0.0-0.1 m), against 4 (protection layer), 5 (rule planner), 7 (integrated DPP). They do not hurt (no run worse than the baseline, 0 % false triggers) and still carry the in-lane lead trim and centering; the prediction-based logic is what handles cut-ins.
Left to port when a use for it exists on NGP10: the AEB RSS core as a pure module (default off; the product decision on AEB/FCW stays the user's).
