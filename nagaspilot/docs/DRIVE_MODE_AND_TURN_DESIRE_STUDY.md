# Study: driving-mode bundle and lane turn desire (2026-10-04)

Read from our own code first; the other forks were only the starting idea (see `FORK_FEATURE_REVIEW.md`).

## 1. Driving-mode bundle

### What drives "style" today (NGP10 code)

| Knob | Where it is read | How often | Effect |
|---|---|---|---|
| Acceleration profile `ngp_lon_accel_profile` (normal / eco / sport) | `longitudinal_params.load_accel_profile` → `get_max_accel` | every 2 s | ceiling on commanded acceleration by speed |
| Personality `LongitudinalPersonality` (aggressive / standard / relaxed) | `selfdrived.params_thread`, every 0.1 s; also the steering-wheel distance button cycles it | 0.1 s | follow time gap (`get_T_FOLLOW`) and jerk factor (`get_jerk_factor`) |
| Adaptive following gap `ngp_lon_adaptive_gap` | `longitudinal_params.load_adaptive_gap_enabled` | every 2 s | shrinks/grows the gap from the lead's motion |
| Speed offset `ngp_lon_speed_offset_kph` | `plannerd`, once at start | start only | constant offset on the set speed |

So all three style knobs are already live-readable: a bundle only has to write them, with no restart and a worst-case 2 s delay.
Speed offset is read once and is a speed choice, not a style, so it stays out of the bundle.

### The numbers (computed from the code, not estimated)

Maximum acceleration (m/s²), `acceleration_profile_limit` with the planner's speed breakpoints:

| Profile | 5 m/s | 15 m/s | 25 m/s | 35 m/s |
|---|---|---|---|---|
| eco | 1.05 | 0.80 | 0.60 | 0.47 |
| normal | 1.40 | 1.07 | 0.80 | 0.67 |
| sport | 1.80 | 1.47 | 1.20 | 0.93 |

Following distance behind a lead at the same speed, `t_follow * v + 6 m stop distance` (stock time gaps 1.25 / 1.45 / 1.75 s; jerk factor 0.5 for aggressive, 1.0 otherwise):

| Personality | 10 m/s | 20 m/s | 30 m/s |
|---|---|---|---|
| aggressive (1.25 s) | 18.5 m | 31.0 m | 43.5 m |
| standard (1.45 s) | 20.5 m | 35.0 m | 49.5 m |
| relaxed (1.75 s) | 23.5 m | 41.0 m | 58.5 m |

### Benefit

- Today a driver sets two or three things independently: 3 profiles × 3 personalities × adaptive gap on/off = 18 combinations, most of
  them incoherent (sport acceleration behind a relaxed 58 m gap at 30 m/s; eco acceleration with a 43 m gap that eco braking cannot
  hold). A bundle reduces that to three coherent presets plus "custom".
- The steering-wheel button still changes the personality; the bundle must treat that as the driver leaving the preset, so the
  applier reports `custom` when the live values no longer match, instead of silently overwriting them.
- Risk: sport = a gap 4–6 m shorter than standard at 20–30 m/s and 0.4 m/s² more acceleration. That is a driver choice, but it is
  new behaviour with no road validation, so the default stays `custom` (nothing is written) and the code never selects sport on its own.

### Design chosen

- Pure `nagaspilot/controls/ngp_drive_mode.py`: presets and `detect(...)`; no Params access.
  - eco: profile eco, relaxed, adaptive gap off
  - normal: profile normal, standard, adaptive gap off
  - sport: profile sport, aggressive, adaptive gap off (adaptive gap stays an explicit opt-in)
- `nagaspilot/runtime/drive_mode.py`: applies a preset by writing the three params, only when the mode param changes.
- One hook in `plannerd` (NGP10) that calls the applier in its loop. Param `ngp_lon_drive_mode` (`custom` default). ExoPilot
  uses its own keys (`EOPAccelerationProfile`, `EOPAdaptiveGapEnabled`) through the same pure presets.

## 2. Lane turn desire

### What the code says

- The driving model takes an 8-wide `desire` input (`ModelConstants.DESIRE_LEN = 8`), one slot per `Desire` enumerant:
  none, turnLeft, turnRight, laneChangeLeft, laneChangeRight, keepLeft, keepRight.
- `modeld` feeds it as a **pulse on the rising edge** (`new_desire = where(desire - prev_desire > 0.99, ...)`); holding a desire
  constant therefore sends one pulse, and the model itself decides when the action is complete.
- **NGP10:** `DesireHelper.DESIRES` only maps the lane-change states, so the `turnLeft`/`turnRight` slots were never used.
- **EOP10 already has it:** `DesireHelper` sends `turnLeft`/`turnRight` whenever one signal is on below the lane-change speed and the
  car is not stopped (`TURN_DESIRES`, commented as a FrogPilot pattern). It is always on, has no parameter, and does not require lateral
  to be active. We missed this in the first pass of the fork review; reading our own code first found it.
- The model reports `meta.desirePrediction` for all slots, so a response to a turn pulse can be measured on a replay.
- NGP10 runs upstream's v0.10 model; the ExoPilot branches run the Bukapilot KA2 RKNN pair. Whether either model was trained to react
  to a turn pulse is **not knowable from our code**.

### Decision

- NGP10 gets a stricter, opt-in version (`ngp_turn_desire.py`): exactly one signal, lateral active, no lane change running, speed
  between 2 m/s and a driver-chosen ceiling; `ngp_lat_turn_desire_mph` = 0 (off) by default.
- EOP10 keeps its existing always-on behaviour untouched. It is *not* refactored onto the NGP10 core: the two differ on purpose
  (EOP does not require lateral active), and changing an always-on path without a replay is the wrong trade. The README therefore marks
  the feature "basic, optional" on NagasPilot and "always on" on ExoPilot, not "extended".
- Open question for a replay: should EOP10 also require lateral active and no lane change, as NGP10 does? Compare `desirePrediction[turn]`
  and path curvature with and without the pulse on drives with turns.
