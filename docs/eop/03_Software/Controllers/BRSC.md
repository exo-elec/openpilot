# Design Document: BRSC (Bumpy Road Speed Controller)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `nagaspilot/controls/ngp_brsc.py` (pure policy) + `selfdrive/controls/lib/longitudinal_planner.py` (EOP10 wiring) |
| **Tests** | ✅ `nagaspilot/tests/test_ngp_brsc.py` |
| **Thresholds** | ⚠️ Provisional — order-of-magnitude defaults, not yet road-calibrated (dev-PC stage) |

---

> **Component Type:** Controller (inside `plannerd`)
> **Complexity:** Low-Medium
> **Naming:** `NGP`-prefixed, not `EOP`-prefixed — this feature lives in the shared
> `nagaspilot/controls/` package (see `nagaspilot/docs/NAMING_CONVENTIONS.md`) and is
> ported byte-identical across `dev/EOP10`, `dev/NGP10`, and `dev/EDP10`. This is a
> deliberate exception to EOP10's own `EOP<Feature><Param>` rule, made specifically
> for features shared verbatim across branches.
> **EOP Integration:** `selfdrive/controls/lib/longitudinal_planner.py`

---

## 1. Objective

VTSC/MTSC slow down for curves using vision/map curvature. Neither reacts to road
**surface roughness** — potholes, expansion joints, washboard/broken pavement — none
of which show up as path curvature. BRSC fills that gap: it
reads the vertical axis of the accelerometer, estimates how rough the road currently
is, and asks the planner to reduce cruise speed and/or the positive acceleration limit
while it's rough — the same way a careful driver eases off the gas on a rough patch.

**Key benefits:**
- Reduces harsh vertical jolts and the risk of losing traction on broken pavement
- Works from IMU only — no camera, no map data, and no dependency on any other
  controller, so it's trivially portable to any branch that publishes `accelerometer`
- Never raises speed or acceleration above what other controllers already allow — a
  pure *reduction* policy, like `ngp_road_condition.py` / `ngp_tja.py`

---

## 2. Real-World Usage Cases (drove the tuning)

| Case | Signature | Desired behavior |
|---|---|---|
| Railroad crossing / single pothole | 1-2 isolated spikes, over in <0.3s | **Must NOT** trigger a sustained slowdown — reacting after the fact doesn't avoid the bump, and braking hard for a single already-past jolt is itself uncomfortable/unsafe |
| Speed bump | Driver already braking on approach | Don't fight the driver's own braking; this controller only *adds* a cap, it never overrides driver braking |
| Washboard / broken pavement | Sustained elevated vertical RMS for several seconds | **This is the actual target** — hold a reduced speed/accel for as long as the roughness lasts |
| Rough patch ends | RMS drops back to normal | Recover speed **gradually** over a couple of seconds, not a step — bumpy sections are typically over in a few seconds, so an abrupt full-power resume right at the edge of the patch is jarring and can catch the tail end of it |
| Series of close-spaced bumps (e.g. multiple frost heaves) | Repeated short rough intervals with brief smooth gaps | Should not fully release between bumps — hold time accumulates (capped) so the car doesn't yo-yo speed between each one |

---

## 3. Technical Architecture

### 3.1 Signal Source

Input is the **raw `accelerometer` service** (`SensorEventData.acceleration.v[2]`,
published at 104 Hz by `imud` on EOP10 / `sensord` on NGP10 & EDP10 — same service
name on all three branches), sampled at `plannerd`'s 20 Hz cadence. `livePose` was
considered and rejected: it's locationd's EKF *output*, which treats high-frequency
vertical motion as measurement noise and attenuates it — bump energy (~4-15 Hz) barely
survives that filter. The raw, 20 Hz-decimated accelerometer signal is aliased but
preserves broadband variance, which is what a roughness *RMS* estimate needs (a
discrete bump-*event* detector would need the full 104 Hz and therefore a dedicated
daemon — out of scope here; RMS-roughness is what "the road is bumpy" actually means).

Axis convention: index `[2]` is treated as vertical, matching the existing (currently
unused) `IMUBumpDetector`/`SurfaceQualityAnalyzer` code in
`selfdrive/surfaced/surface_detector.py`, which assumes the same. This holds for a
device mounted level (standard windshield mount); it is not roll/pitch-corrected.

### 3.2 Core Algorithm — `nagaspilot/controls/ngp_brsc.py::NGPBRSC`

1. **High-pass** — a slow EMA (`BASELINE_TC_S = 2.0s`) tracks gravity + road-grade
   offset; subtracting it leaves only dynamic vertical motion.
2. **Windowed RMS** — RMS of the high-passed signal over `WINDOW_S = 0.2s`.
   **Invariant: `WINDOW_S < ATTACK_S`.** This is what makes isolated spikes safe: a
   single sample can only "linger" and look rough in the RMS window for `WINDOW_S`
   seconds. If the window were wider than the attack duration, one pothole could
   satisfy the attack timer purely by sitting in the window. Keeping it shorter
   structurally forces genuinely repeated/sustained roughness to reach `ATTACK_S`.
3. **Attack debounce** — `ATTACK_S = 0.3s` of continuous RMS ≥ `RMS_MILD` (1.0 m/s²)
   required before engaging.
4. **Hold, with accumulation and a cap** — once engaged, `hold_timer` is kept topped
   up to at least `HOLD_BASE_S = 2.0s` for as long as roughness continues. If a new
   rough interval starts while a previous one is still decaying (recurring bumps),
   `HOLD_PER_RETRIGGER_S = 0.5s` is added on top — capped at `HOLD_CAP_S = 8.0s` so a
   long rough stretch can't compound into an unbounded slowdown.
5. **Reduction, capped, only deepens mid-hold** — severity is `(rms - RMS_MILD) /
   (RMS_SEVERE - RMS_MILD)` clamped to `[0, 1]`; `speed_factor` and the accel-cap
   fraction move toward `1 - severity * (1 - floor)` but are only ever allowed to
   *decrease* while holding (never relax mid-episode from a momentary dip).
   `SPEED_FACTOR_FLOOR = 0.75` (never cut more than 25%),
   `ACCEL_MAX_FLOOR_FRACTION = 0.45` (accel cap never below 45% of the caller's max).
6. **Decay to resume** — once `hold_timer` reaches 0, `speed_factor`/accel scale ramp
   back to 1.0 at `RELEASE_RATE = 0.5`/s (roughly 2s to fully recover), not a step.

The class has zero `cereal`/`Params`/messaging imports — pure Python taking `(az, dt)`
in, `BumpyRoadResult` out — so the exact same file works at any sample rate and is
copy-portable to `dev/NGP10` and `dev/EDP10` without modification.

### 3.3 EOP10 Wiring (`selfdrive/controls/lib/longitudinal_planner.py`)

- `plannerd.py`'s `SubMaster` adds `accelerometer` (`ignore_alive`, since it's not
  guaranteed present in replay/tests).
- `NGPBRSC.update()` is called every planner frame (20 Hz) so its internal
  baseline/hold state stays current regardless of gating.
- **Speed**: while `active` and `v_ego > NGP_BUMP_MIN_V_EGO (5.0 m/s)`, a target
  `v_cruise * speed_factor` (floored at `NGP_BUMP_MIN_SPEED_MS ≈ 8.3 m/s / 30 km/h`)
  is applied via the same `_apply_speed_limit()` clamp SQSC/RCD/TLSC use.
- **Accel**: while `active`, `max_accel` is scaled by the accel-cap fraction inside
  the existing `mode == 'acc'` block, alongside the weather-severity limit
  (`_apply_weather_severity_limit`) — same pattern, same insertion point.
- Debug fields on `longitudinalPlan`: `ngpBrscActive`, `ngpBrscSpeed`,
  `ngpBrscRoughness` (capnp `@66`-`@68`, next free after `ddscSpeed @65`).
- Toggle: `ngp_lon_brsc` (default on), `selfdrive/ui/qt/offroad/eop_panel.cc`
  under Speed Control, cached/refreshed every 2s like `EOPAdaptiveGapEnabled`.

### 3.4 Cross-Branch Port

| Branch | Module | Wiring |
|---|---|---|
| `dev/EOP10` | `nagaspilot/controls/ngp_brsc.py` | `selfdrive/controls/lib/longitudinal_planner.py` (above); toggle in `eop_panel.cc` |
| `dev/NGP10` | same file, byte-identical | `selfdrive/controls/lib/longitudinal_planner.py` via `NGPFlags.BRSC` (same bitmask pattern as DLON/coasting/TJA); toggle in `ngp_panel.cc`. **Not** added to `nagaspilot/controls/ngp_suite.py` — that manifest is a standalone feature-port inventory, not runtime-wired on any branch |
| `dev/EDP10` | same file, byte-identical | `selfdrive/controls/lib/longitudinal_planner.py` (dragonpilot-style wiring); toggle in `dp_panel.cc` |

`dev/EDP10` and `dev/EOP10` have no common git merge base, so the module is copied
verbatim rather than cherry-picked. See each branch's own docs for its wiring notes
(`nagaspilot/docs/NGP10_FEATURE_MATRIX.md` on NGP10,
`nagaspilot/docs/EOP10_FEATURE_PORT_AUDIT.md` on EDP10).

### 3.5 Toggle UI Across Branches

Each branch exposes `ngp_lon_brsc` through its own settings-panel convention:

| Branch | Panel class / file | Notes |
|---|---|---|
| `dev/EOP10` | `EopPanel` (`eop_panel.cc`), under "ExoPilot" tab | Full EOP toggle panel, pre-existing |
| `dev/EDP10` | `DPPanel` (`dp_panel.cc`), under "DP" tab | Pre-existing dragonpilot-style panel, `add_longitudinal_toggles()` |
| `dev/NGP10` | `NGPPanel` (`ngp_panel.cc`), under "NGP" tab | NGP10 previously had no dedicated toggle panel (only `DeveloperPanel`). Added for BRSC, then completed the same day to expose every other already-integrated `ngp_*` param (ALCC, LCA, road-edge, coasting, DLON) alongside it — full detail in `nagaspilot/docs/NGP10_FEATURE_MATRIX.md` on `dev/NGP10`, not duplicated here since it's outside BRSC's own scope. Named `NGP` rather than `DP` because NGP10 is mid-migration toward `dev/EOP10`'s naming/architecture, not EDP10's. Registered in `settings.cc`'s panel list and `selfdrive/ui/SConscript`. |

---

## 4. Safety Notes

- **Reduction only** — this policy can never raise `v_cruise` or `max_accel` above
  what other controllers already computed; it only tightens the existing clamp.
- **Speed floor** — never reduces cruise speed below ~30 km/h, and is gated off below
  5 m/s, so it can't create a crawl-to-stop condition on its own.
- **Accel floor** — never caps positive accel below 45% of the caller's max, so the
  car retains meaningful acceleration authority (e.g. to complete a merge) even during
  a severe rough patch.
- **Driver override** — does not touch braking and does not fight driver throttle/brake
  input; it only lowers the ceiling the planner/MPC is allowed to target.
- **Isolated-event immunity** — the `WINDOW_S < ATTACK_S` invariant (§3.2 step 2) is
  load-bearing; do not widen `WINDOW_S` past `ATTACK_S` without re-verifying
  `test_single_expansion_joint_does_not_trigger`.

---

## 5. Testing

`nagaspilot/tests/test_ngp_brsc.py` covers, on the pure module (no capnp/Params
required, runs anywhere):
- Smooth road stays inactive
- A single isolated spike does not trigger
- Sustained roughness engages and caps the reduction at the floor
- Reduction never exceeds the floor even for extreme roughness
- Hold survives a short gap between close-spaced bumps
- Release ramps back monotonically after the hold expires (not a step)
- Accumulated hold is bounded by the cap

Real-world calibration (RMS thresholds, floors, hold/release timing) is **not yet
validated on hardware** — this is dev-PC stage per `CLAUDE.md`. Defaults were chosen
from order-of-magnitude reasoning about typical vertical accel signatures (smooth-road
noise floor ~0.1-0.3 m/s² RMS, sustained rough/washboard ~1-3 m/s² RMS, transient
pothole/speed-bump spikes 3-15 m/s²) and should be revisited once the device is
mounted on a vehicle and can be tuned against logged `accelerometer` data.

---

## 6. Related Documents

- `nagaspilot/docs/NAMING_CONVENTIONS.md` — `ngp_` prefix / shared-module convention
- `docs/eop/01_Core/NAMING_CONVENTIONS.md` — EOP10's own naming rules and the
  documented exception for this feature
- `docs/eop/03_Software/Controllers/VTSC.md`, `SQSC.md` — sibling speed controllers
  this design borrows patterns from (state hysteresis from VTSC, accel-scaling
  pattern from the weather-severity limit)
