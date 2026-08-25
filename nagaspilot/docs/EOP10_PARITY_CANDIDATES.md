# EOP10 → NGP10 feature parity candidates

Full inventory of `dev/EOP10`'s driving-policy features, checked against what
`dev/NGP10` actually has wired today, for picking what to port next. Built by
reading `selfdrive/ui/qt/offroad/eop_panel.cc` (every toggle EOP10 exposes)
and `selfdrive/controls/lib/longitudinal_planner.py`'s imports (features that
are always-on, no toggle) directly on `dev/EOP10`, then cross-checking each
against `nagaspilot/controls/*.py` on this branch.

Context: NGP10/comma-3 is a test branch proving EOP10's software ahead of the
ExoPilot 01M hardware move (see [[ngp10-purpose-and-scope]] in project
memory) — HAL layers stay out of scope; this doc is about
`selfdrive/`-level driving-policy features only.

---

## Tier 1 — already wired, matches EOP10

No action needed.

| Feature | EOP10 | NGP10 |
|---|---|---|
| DLON (longitudinal profile) | `dlon.py`, always-on `Auto` — `EOPDLONMode` removed 2026-08-10, no longer user-selectable (matches NGP10) | `ngp_dlon.py`, always-on `Auto` — no user-selectable mode (2026-08-09) |
| DLAT (lateral profile) | `dlat.py` — RED curvature nudge (hardware-dependent, no comma-3 equivalent) + LCA initiation gate (2026-08-09); `EOPDLATMode` removed 2026-08-10, no longer user-selectable (matches NGP10) | `ngp_dlat.py`, wired into `controlsd.py` (2026-08-09) — no curvature authority (no RED equivalent), real LCA initiation gate, no user-selectable mode |
| DLAT→DLON confidence coupling | `dlon.py::detect_lane_confidence_trigger()` reads `dlatUseLaneless`, always consulted since 2026-08-10 (no mode gate left) | `ngp_dlon.py::detect_lane_confidence_trigger()` reads `ngpDlatUseLaneless`, always consulted (2026-08-09, both branches) |
| TJA (traffic-jam gap policy) | `tja.py` | `ngp_tja.py` |
| BRSC (bumpy-road speed) | `ngp_brsc.py` | `ngp_brsc.py` (shared file) |
| ALCC (always-on lane centering) | `EOPLatALCC` | `ngp_lat_alcc` (inline in `controlsd.py`) |
| LCA speed threshold | `EOPLatLCASpeed` | `ngp_lat_lca_speed`/`_auto_sec` (via upstream `DesireHelper`) |
| Road Edge Detection | `EOPLatRoadEdgeDetection` | `ngp_lat_road_edge_detection`, `ngp_road_edge.py` (wired in `modeld.py`) |
| Lane Change Lead Handoff | `lc_lead_handoff.py`, `EOPLCAdjacentLeadHandoff` (opt-in, no panel toggle) | `ngp_lc_lead_handoff.py` (ported 2026-08-25), `ngp_lon_lc_lead_handoff` — wired into `longitudinal_planner.py` via `NGPFlags.LC_LEAD_HANDOFF`, no panel toggle (matches EOP10) |
| VTSC (Vision Turn Speed Control, 0-250m) | `vtsc.py`, `EOPVTSCEnabled` (learned-speed DB + self-calibration) | `ngp_vtsc.py` (wired 2026-08-25), `ngp_lon_vtsc` — comma-3-safe vision-only slice (no learned DB/self-calibration, deliberately simpler than EOP10's), wired into `longitudinal_planner.py` via `NGPFlags.VTSC`, panel toggle in `NGPPanel`'s Longitudinal Ctrl section |
| NSLC-equivalent (nav-source speed-limit enforcement) | `nslc.py`, `EOPNSLCEnabled` (no panel toggle either) — offset + `SpeedLimitConfirmation` debounce | `ngp_speed_policy.py` (wired 2026-08-25), `ngp_lon_nslc` — `SpeedLimitPolicy.NAVIGATION` only (no map source on this branch, no `driver_overriding`/offset/confirmation debounce — hard instant clamp), wired via `NGPFlags.NSLC` |

Note: EOP10 also has a *separate*, camera-based "Lane Change Assistant (LCA)"
toggle (`EOPLCAControllerEnabled`, multi-camera blind-spot detection) — don't
confuse this with the LCA-speed-threshold feature above, which is a
different, smaller thing (turn-signal lane change speed gate). The camera
one is Tier 4 below.

---

## Tier 2 — NGP10 already has a portable pure-policy module, just never wired

**This is the highest-value, lowest-risk tier.** Someone already wrote these
as hardware-agnostic classes that take a plain `Observation`/input dataclass
— e.g. `ngp_road_condition.py`'s docstring literally says *"Pure road-condition
policy without EOP10's OpenCV/RK perception path"*. The work remaining is the
same shape as this session's BRSC/DLON wiring: find or produce the input data
on comma-3 (usually already-published cereal fields), call `.evaluate()`/
`.update()` from the right daemon, apply the result, add a param + panel
toggle. No hardware blocker, no new policy design — see
[[brsc-feature-cross-branch]] for what that wiring pattern looks like end to
end.

| Feature | EOP10 module | NGP10 module (unwired) | What it'd need on NGP10 |
|---|---|---|---|
| Adaptive personality/gap profile | `adaptd` daemon (real process on EOP10) | `selfdrive/adaptd/ngp_profile.py` — exists, zero importers, `adaptd` isn't even a registered process in NGP10's `process_config.py` | Would need adding `adaptd` as an actual process, not just wiring a function call — bigger than the others in this tier |

**Corrected 2026-08-25**: three items previously listed here (Collision-risk
advisory, Traffic-light/stop-sign approach, Normalized radar/zones) moved to
Tier 2.5 below. This tier's own description says the remaining work is
"find the input data, call `.evaluate()`, apply the result" — but for those
three, nothing on this branch would ever read the result. "Feed it
`radarState`/`liveTracks`" describes plumbing an input; it doesn't name a
consumer. Wiring them as originally described would mean computing a value
every frame that nothing observes — not a smaller version of what BRSC/DLON
wiring did, a different, unfinished kind of task.

---

## Tier 2.5 — module is pure/portable, but blocked on missing infrastructure, not wiring effort

Two different flavors of the same underlying problem: "wire it" implicitly
promises there's something meaningful to connect on both ends. These modules
are missing one end or the other.

### No input source exists

| Feature | EOP10 module | NGP10 module | Why it's blocked |
|---|---|---|---|
| MTSC (Map Turn Speed Control, 250-500m) | `mtsc.py`, `EOPMTSCEnabled` | `ngp_mtsc.py` — pure `update([(distance_m, curvature_1_per_m), ...])`, no map API access of its own (same "comma-3-safe" pattern as `ngp_vtsc.py`) | **Confirmed 2026-08-25, not just an open question**: NGP10's `cereal/log.capnp` has no `MapData` struct/Event field at all (EOP10 has both), `cereal/services.py` has no `'mapData'` entry, and no process anywhere in this tree publishes it. A prior session's `mapData` subscription in `plannerd.py` actually crashed `SubMaster.__init__` with `KeyError('mapData')` on every start — see `NGP10_FEATURE_MATRIX.md`'s "Correction (2026-08-25)" note and the fix commit. `ngp_mtsc.py` itself is ready; no *pre-computed* curvature-tuple source exists to feed it without porting EOP10's map-data infrastructure (new cereal schema, a `mapd`-equivalent daemon, OSM pipeline) — not attempted here. One narrower option not evaluated: `navRoute` *is* a registered service on NGP10 (`cereal/services.py` has it) and carries raw `[(latitude, longitude), ...]` route coordinates (`struct NavRoute` in `log.capnp`) — deriving `(distance, curvature)` tuples from that geometry directly, without EOP10's map-data port, might be a smaller, NGP10-native path. Not checked whether the precision/update rate is usable for this, and no curvature-from-polyline code exists yet either way — noted as an option for a future session to evaluate, not ruled out. |
| Traffic-light/stop-sign approach | `tlsc.py` (needs `stereoObjects` from `gridd` — **not portable as-is**, see Tier 4) | `ngp_traffic_control.py` — "Non-controlling traffic-light/stop-sign approach proposal" | **Corrected 2026-08-25**: `ngp_traffic_control.py`'s `evaluate()` needs `TrafficControlObservation` (light/sign state, distance, confidence) as input — a real traffic-light/stop-sign classifier. Comma-3 stock openpilot has no such perception output on this branch, so unlike collision/radar below, this one is blocked on *both* ends: no input source, and `TrafficControlResult.control_authority` is `False` by design, same as collision, so there'd be nothing to consume the output either even if the input existed. NGP10's DLON already covers the same real-world case with a simpler heuristic (`detect_traffic_control()`: stop + no lead + low speed), so this isn't a functional gap in practice. |

### No consumer for the output

Different failure mode from the row above: the input data these modules need
is real and already flowing on this branch (`liveTracks` is published by
`card.py` and consumed by `radard.py` today). What's missing is a reader for
the *result* — both modules set `control_authority=False` by design (stock
AEB/BSM stay the real safety net), so "wire it" would mean computing a value
every 20 Hz frame that nothing on this branch reads, logs, or acts on.

| Feature | EOP10 module | NGP10 module | Why it's blocked |
|---|---|---|---|
| Collision-risk advisory | (folded into AEB path) | `ngp_collision.py` — "Advisory collision-risk assessment using normalized radar tracks" | **Corrected 2026-08-25**: `radarState`/`liveTracks` are real, working inputs (`liveTracks` published by `card.py`) — the previous entry's "feed it `radarState`/`liveTracks`" was accurate but incomplete, since `CollisionResult.control_authority` is `False` by design and nothing on NGP10 reads a `CollisionResult`. Wiring this today means adding a call whose output goes nowhere. Would become a real item once something consumes it (a UI collision-risk indicator, an event log entry, a future advisory alert path) — that consumer doesn't exist yet either. |
| Normalized radar / zones | (part of EOP10's own radar4d + upstream fusion) | `ngp_radar.py` — "Normalized Tesla-gateway radar2D/radar3D tracking and zone assessment" | **Corrected 2026-08-25**: its only real (non-test) importer on this branch is `ngp_lca.py`, which the feature matrix already documents as itself unwired (zero non-test importers, LCA speed/auto-sec is actually served by upstream `DesireHelper` instead). So `ngp_radar.py`'s one potential consumer is also dead code — same "output has no reader" blocker as collision above, one hop removed. |

---

## Tier 3 — EOP10 has it, NGP10 has no module at all yet

| Feature | EOP10 | Notes |
|---|---|---|
| Driver preference profile (speed-limit offset, following-distance choice) | `driver_prefs.py` | Mostly pure parameter logic ("No NPU impact" per its own docstring); the "shock/obstacle awareness" part of it uses IMU shock detection (`EOPShockDetection` etc.) which is a *different* concept from BRSC (impact/curb-hit event detection+recording vs. BRSC's sustained-roughness speed policy) — don't conflate the two if porting this. |
| Adaptive accel limit (low-speed clamp + cruise ramp-off) | inline function `_apply_adaptive_accel_limit()` in `longitudinal_planner.py`, always-on, no toggle | Tiny, pure `v_cruise`/`v_ego` math, no hardware dependency — cheap to port if wanted, but low visible impact. |
| DLP curve assist (pre-emptive laneless for tight curves) | `EOPDLPCurvesEnabled` | Separate from DLAT's Laneful/Laneless *mode selection* — this is a curve-specific override on top of whatever DLAT picks. Depends on DLAT existing first. |

---

## Tier 4 — hardware-gated on EOP10, not portable to comma3 as-is

Confirmed by reading the actual data source each one depends on — not
assumed from the feature name:

| Feature | Why it's hardware-gated |
|---|---|
| TLSC (as EOP10 implements it) | Subscribes to `stereoObjects` published by `gridd` at 20Hz — needs EOP10's stereo camera pipeline. (NGP10's `ngp_traffic_control.py` may or may not need the same input — worth checking before assuming it's blocked too, see Tier 2.) |
| SQSC (Surface Quality Speed Controller) | Reads `surfaceStatus` from the `surfaced` daemon — camera/stereo road classification, no comma-3 equivalent sensor. Conceptually adjacent to BRSC but camera-based rather than IMU-based; BRSC is the portable substitute already ported. |
| RCD (Road Condition Detection) | Classifies wet/icy/snow/debris **from camera imagery** — needs EOP10's CV pipeline, no comma-3 equivalent. `ngp_road_condition.py` was written input-agnostic ("Pure road-condition policy without EOP10's OpenCV/RK perception path") but still needs *some* observation source fed to it — nothing on comma-3 currently produces a `RoadConditionObservation`, so it's Tier 2 in spirit but has no obvious data source yet, closer to Tier 3 in practice. |
| Weather-severity accel limit | Gated on `sm.valid.get('radar4d', ...)` — needs the BGT60TR13C radar's weather-severity output (clutter/wiper/glass-contamination steps), no comma-3 sensor produces this. |
| Enhanced Perception section (stereo, dual-camera helpers, gridd spatial mapping) | All require EOP10's stereo camera hardware. |
| AEB (EOP10's RSS-based version) | Requires stereo + monod (Hailo-8) fusion. Comma-3 already has its own stock/car AEB via upstream — not a gap, just a different (already-present) mechanism. |
| Radar4D, MonoD, Point Cloud Recording, Global Localization (SGM part), Surface Quality Mapping, GPS/RTK, Voice AI | All tied to EOP10-specific sensors (BGT60TR13C, Hailo-8, stereo depth, u-blox ZED-F9P, mic). Explicitly out of scope per your HAL-layers boundary. |

---

## Tier 5 — different mechanism already covers the same purpose, no action needed

| EOP10 feature | NGP10's equivalent |
|---|---|
| CAT (Car Adaptive Tuning — steer ratio/stiffness learning) | Upstream `paramsd`/`LiveParametersV2`, already integrated per `NGP10_FEATURE_MATRIX.md` |
| Driver monitoring "always runs" | Stock `dmonitoringmodeld`, already present on comma-3 |
| Vehicle Platform size-category selector | EOP10 needs this because its BYD/Tesla physics model lacks full per-car interfaces; NGP10 gets real per-car physics from OpenDBC's actual car definitions already — this EOP10 workaround doesn't apply |
| Device/UI toggles (RHD, beep, alert mode, display mode, radar-track display) | Mostly cosmetic/device-preference, largely covered by stock openpilot's own `TogglesPanel`/`DevicePanel` already |

---

## Suggested order if starting from scratch

1. ~~Lane Change Lead Handoff~~ — done, 2026-08-25 (Tier 1 now): `ngp_lc_lead_handoff.py` ported verbatim (pure `modelV2.leadsV3` logic, unchanged), wired into `longitudinal_planner.py`/`plannerd.py` via `NGPFlags.LC_LEAD_HANDOFF` and `ngp_lon_lc_lead_handoff` (default off, no panel toggle — matches EOP10). 9 unit tests in `nagaspilot/tests/test_ngp_lc_lead_handoff.py`. **Stronger caveat than usual**: `plannerd` itself could not start on this branch from 2026-08-08 until the `mapData` fix below (`SubMaster.__init__` raised `KeyError('mapData')` on every launch) — this feature has literally never executed, not just "no on-road validation yet."
2. ~~VTSC~~ — done, 2026-08-25 (Tier 1 now): `ngp_vtsc.py` wired into `longitudinal_planner.py`/`plannerd.py` via `NGPFlags.VTSC` and `ngp_lon_vtsc` (default off, panel toggle added to `NGPPanel`'s Longitudinal Ctrl section). Clamps `v_cruise` the same way BRSC does (`min()`), only while the state machine is ENTERING/TURNING. 5 unit tests in `nagaspilot/tests/test_ngp_vtsc.py`. Same `plannerd`-never-started caveat as item 1 above.
3. ~~Speed-limit enforcement via `ngp_speed_policy.py`~~ — done, 2026-08-25 (Tier 1 now, as NSLC-equivalent): nav-only (`SpeedLimitPolicy.NAVIGATION`), not map-then-nav — `mapData` doesn't exist on this branch (see Tier 2.5). Wired via `NGPFlags.NSLC`/`ngp_lon_nslc`, default off, no panel toggle (matches `EOPNSLCEnabled`). 4 unit tests in `nagaspilot/tests/test_ngp_speed_policy.py`. No `driver_overriding`, offset, or confirmation-debounce — a hard instant clamp on 1 Hz nav data, a real behavioral gap vs. EOP10's `nslc.py` worth closing before relying on this in practice.
4. ~~DLAT~~ — done, 2026-08-09 (Tier 1 now): wired into `controlsd.py`, coupled into DLON's AUTO-mode switch, and given a real LCA-initiation-gate effect. See the feature matrix's "DLAT made a real default" note for what was and wasn't validated before shipping (thresholds reused from the module's own existing constants, not newly tuned; no on-road validation yet — same caveat as everything else in this doc's "vehicle actuation still requires HIL" note).
5. **Corrected 2026-08-25** — "everything else in Tier 2/3" turned out to be
   smaller than it looked once each item was checked for a real consumer, not
   just a wiring path. MTSC, Collision-risk advisory, Traffic-light/stop-sign
   approach, and Normalized radar/zones all moved to Tier 2.5 (see above) —
   none of them are "just needs wiring." The actual remaining Tier 2/3 items
   with a real effect once wired:
   - **Adaptive accel limit** (Tier 3) — `_apply_adaptive_accel_limit()` on
     EOP10 is an inline function, no new module needed; clamps `accel_clip`/
     `v_cruise` directly. No schema change.
   - **DLP curve assist** (Tier 3) — feeds DLAT's Laneful/Laneless choice,
     which `controlsd.py` already consumes.
   - **Driver preference profile** (Tier 3) — only the speed-offset half has
     a real effect (clamps `v_cruise`); the shock-detection half doesn't
     connect to anything on this branch and shouldn't be ported alongside it
     — see that row's own note for why the two halves are a different concept
     from BRSC despite the surface-level shock/roughness similarity.
   - **Adaptive personality/gap profile** (Tier 2) — real effect once wired,
     but needs a new `adaptd` process added to `process_config.py` first,
     not just a function call — its own decision, bigger than the others.

Not recommending Tier 4 items be attempted at all on comma-3 — they're
correctly out of scope, not just deprioritized. Tier 2.5 items aren't
recommended either, for a different reason: not hardware-gated, just missing
infrastructure (a data source, or a consumer) this doc's scope (see the top
of this file) doesn't cover building.
