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
| DLON (longitudinal profile) | `dlon.py`, `EOPDLONMode` | `ngp_dlon.py`, `ngp_lon_dlon_mode` |
| TJA (traffic-jam gap policy) | `tja.py` | `ngp_tja.py` |
| BRSC (bumpy-road speed) | `ngp_brsc.py` | `ngp_brsc.py` (shared file) |
| ALCC (always-on lane centering) | `EOPLatALCC` | `ngp_lat_alcc` (inline in `controlsd.py`) |
| LCA speed threshold | `EOPLatLCASpeed` | `ngp_lat_lca_speed`/`_auto_sec` (via upstream `DesireHelper`) |
| Road Edge Detection | `EOPLatRoadEdgeDetection` | `ngp_lat_road_edge_detection`, `ngp_road_edge.py` (wired in `modeld.py`) |

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
| **DLAT** (Laneful/Laneless/Dynamic lane planner select) | `dlon.py`-adjacent, `EOPDLATMode` | `ngp_dlat.py` — explicitly a "proving line," docstring says "deliberately has no controlsd or cereal integration" | Feed `modelV2` lane-line probs into `NGPDLAT.lane_confidence()`, wire the `DLATSuggestion` into whatever selects laneful vs. laneless on this branch. **Safety-relevant** — the module's own non-controlling status is a deliberate gate, not an oversight; don't flip it without validating the confidence thresholds against real driving data first. |
| VTSC (Vision Turn Speed Control, 0-250m) | `vtsc.py`, `EOPVTSCEnabled` | `ngp_vtsc.py` | Feed `modelV2` curvature into it, apply result via the same `_apply_speed_limit`-style clamp DLON/BRSC already use in `longitudinal_planner.py` |
| MTSC (Map Turn Speed Control, 250-500m) | `mtsc.py`, `EOPMTSCEnabled` | `ngp_mtsc.py` | Needs OSM curvature data — check whether `mapd`/`mapData` (already subscribed for DLON's speed-limit trigger) carries this, or whether EOP10's `mapd` does something NGP10's doesn't have |
| Collision-risk advisory | (folded into AEB path) | `ngp_collision.py` — "Advisory collision-risk assessment using normalized radar tracks" | Feed it `radarState`/`liveTracks`; explicitly non-controlling (`control_authority=False`) by design — stock AEB stays the real safety net regardless |
| Traffic-light/stop-sign approach | `tlsc.py` (needs `stereoObjects` from gridd — **not portable as-is**, see Tier 4) | `ngp_traffic_control.py` — "Non-controlling traffic-light/stop-sign approach proposal" | This is the one Tier-2 item with a real question mark: NGP10's version was written to *not* need stereo, but check what input it actually expects before assuming it's a drop-in replacement for EOP10's TLSC. Partial overlap already exists — NGP10's DLON has its own `detect_traffic_control()` heuristic (stop + no lead + low speed) |
| Speed-limit policy (map/nav/car, source resolution) | `mslc.py` + `nslc.py` + `speed_limit_resolver.py` (three separate modules on EOP10) | `ngp_speed_policy.py` — "Portable, non-controlling speed-limit and speed-zone policy" | **Real gap, not just unwired**: NGP10's DLON already reads `mapData.speedLimit`/`navInstruction.speedLimit` as an E2E-mode *trigger* (2026-08-08 fix), but that only switches driving mode — it doesn't clamp `v_cruise` to the posted limit the way EOP10's MSLC/NSLC actually do. Wiring `ngp_speed_policy.py` for real speed-limit enforcement is a distinct feature from what DLON already does, not a duplicate. |
| Normalized radar / zones | (part of EOP10's own radar4d + upstream fusion) | `ngp_radar.py` — "Normalized Tesla-gateway radar2D/radar3D tracking and zone assessment" | Feed it whatever NGP10's current radar path already publishes; used by `ngp_collision.py` and blind-spot-style zone checks |
| Adaptive personality/gap profile | `adaptd` daemon (real process on EOP10) | `selfdrive/adaptd/ngp_profile.py` — exists, zero importers, `adaptd` isn't even a registered process in NGP10's `process_config.py` | Would need adding `adaptd` as an actual process, not just wiring a function call — bigger than the others in this tier |

---

## Tier 3 — EOP10 has it, NGP10 has no module at all yet

| Feature | EOP10 | Notes |
|---|---|---|
| Lane Change Lead Handoff | `lc_lead_handoff.py`, `EOPLCAdjacentLeadHandoff` (opt-in) | Pure `modelV2.leadsV3` camera-based — no special hardware. Small, self-contained, genuinely portable; probably the easiest brand-new port on this list. |
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

1. **Lane Change Lead Handoff** (Tier 3) — smallest, self-contained, no design questions, pure camera data already available.
2. **VTSC** (Tier 2) — module exists, `modelV2` curvature is already flowing through `longitudinal_planner.py`, same wiring shape as BRSC.
3. **Speed-limit enforcement via `ngp_speed_policy.py`** (Tier 2) — real functional gap (DLON's trigger vs. actual clamping), data source (`mapData`/`navInstruction`) already subscribed.
4. **DLAT** (Tier 2) — highest value but explicitly safety-gated by its own author; needs real validation before flipping from advisory to controlling, budget more time here than the size of the module suggests.
5. Everything else in Tier 2/3, roughly in the order listed.

Not recommending Tier 4 items be attempted at all on comma-3 — they're
correctly out of scope, not just deprioritized.
