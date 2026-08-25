# EOP10 feature-port audit

- Status: decision recorded for EDP10; NGP10 is the separate trial line for
  EOP10-derived DLAT/DLON work
- Compared branches: `dev/EOP10` at `22a0f3aff` and `dev/EDP10` at `a2394317d`
- Audit recorded: 2026-07-31

## Decision

Do not replace DragonPilot's lateral or longitudinal features with EOP10 DLAT
or DLON. The EOP10 implementations are more elaborate on paper, but they do not
provide a safer or more effective baseline for a minimal BYD Atto 3 port on
original comma 3 hardware.

Retain DragonPilot AEM internals and LCA/road-edge lane-change protection. Present
the always-on lane-centering feature to drivers as ALCC. Port no
EOP10 control feature until the basic BYD lateral port and Panda safety model
are validated.

For `dev/NGP10`, DLAT and DLON may be prototyped as isolated, comma-3-safe
features. They must not be copied wholesale: keep the v0.10.0 schema/API,
preserve driver monitoring, and gate control changes with unit tests and
recorded-route replay before enabling vehicle output.

## DLAT finding

EOP10 DLAT claims to arbitrate between lane-line following and an end-to-end
path. Its implementation does not currently change the path used for steering:

- `selfdrive/controls/lib/dlat.py` reads `predictedPath` and
  `predictedPathStd`, but those fields are absent from the EOP10 and EDP10
  `ModelDataV2` schemas;
- the returned `dlat_use_laneless` state is consumed by EOP road-edge gating
  and debug publication, not by the desired-curvature calculation;
- steering continues to use `modelV2.action.desiredCurvature` in both states;
- EOP10 contains no focused DLAT unit tests.

Consequently, importing DLAT would add parameters, schema fields and
`controlsd` integration without selecting a different lateral trajectory.

If the concept is revisited, first implement a NagasPilot-only, non-controlling
confidence logger using existing lane-line and road-edge outputs. Recorded-route
evidence must show a useful decision boundary before it can affect curvature.

## DLON finding

EOP10 names its dynamic longitudinal feature DLON. Unlike DLAT, it genuinely
selects the planner's ACC or blended mode. It considers model stop prediction,
lead speed, low speed, turn signals, curves, navigation and MPC collision state,
then applies transition hysteresis.

It is not suitable for the initial BYD port:

- phase one leaves acceleration, braking and AEB under the stock BYD ACC, so
  the openpilot longitudinal planner output is not transmitted;
- its traffic-control heuristic is model `shouldStop`, no lead and low speed;
  it is not a verified traffic-light or stop-sign detector;
- the optional force-stop path can turn that heuristic into a stop request;
- the included tests use mocked messages and do not cover false-stop routes,
  stale inputs, actuator limits or transition timing;
- DLON, its integration and its tests entered EOP10 in one broad controls
  commit, limiting independent validation history.

DragonPilot AEM is the preferred minimal baseline. It switches ACC/blended mode
using the model's near-term throttle intent, has a small reviewable surface, and
its ten focused unit tests pass in this checkout. DLON's transition-hysteresis
idea may later be reimplemented around AEM after route-replay validation; the
force-stop and broad trigger collection should not be copied.

For BYD openpilot longitudinal, NagasPilot therefore fixes AEM on and removes
the AEM, ACM, and APM choices from the user interface. Factory BYD longitudinal
does not transmit openpilot's AEM output. ACM stays off because it suppresses
mild planner deceleration and its activation, camera-lead behavior, and stopping
distance have not been validated on the BYD test car.

## Feature disposition

| EOP10 feature | Value on comma 3 | Decision |
|---|---|---|
| DLAT | No effective path selection in its current integration | Reject current implementation |
| DLON | Contextual longitudinal mode switching | Defer; consider hysteresis only after BYD longitudinal |
| VTSC | Uses native model orientation prediction to reduce speed for curves | Designed (see "Minimal VTSC disposition" below), **not implemented** -- see correction note under that heading |
| TJA | Smooth start ramp and standstill hold timeout | Reimplement after longitudinal and standstill validation |
| Lane-change lead handoff | Uses native camera leads for target-lane following | Experimental; defer until longitudinal and LCA are stable |
| CAT | Wraps already learned `liveParameters` with another filter/persistence layer | Reject; retain upstream `paramsd` |
| RED | Can add curvature using EOP perception inputs | Reject; retain DragonPilot's conservative lane-change block |
| ALCC/LCA | Broad independent lateral state and event manipulation | Present the shared user-facing feature as ALCC; retain DragonPilot internal compatibility names |
| AEB/RCD/BSD perception | Additional perception-driven interventions | Reject for phase one; retain stock BYD AEB and decoded BSM |
| LatNudge/LonNudge | Depends on EOP dedicated stereo/PathD | Reject for comma 3 wide/narrow cameras |
| BRSC | Vertical-IMU roughness slowdown, no camera/map dependency | Ported: pure policy module is identical on all three branches, only the DPFlags/plannerd glue differs |
| TripD | Non-controlling trip statistics | Optional after vehicle support; not part of the control port |

## Reuse order

1. Complete BYD identification, passive state, lateral messages and Panda
   safety while stock ACC/AEB remain authoritative.
2. Validate ordinary lateral operation, then DragonPilot ALKA/LCA/road-edge
   compatibility.
3. Collect recorded routes and evaluate confidence signals without changing
   steering or acceleration.
4. Design and validate BYD openpilot longitudinal as a separate safety project.
5. Build, then validate, the selectable minimal VTSC described below; only
   then consider AEM hysteresis, TJA, or camera lead handoff.

## Minimal VTSC disposition

**Correction, 2026-08-26**: this section previously read as a status report
("VTSC is user-selectable and defaults off...") and the Feature disposition
table above previously said "Ported." Neither was true. Checked directly
against the tree at this commit: no `vtsc`-named file, no `SCC`/curve-speed
policy module, and no panel toggle anywhere in `dp_panel.cc` or elsewhere.
`nagaspilot/docs/MIGRATION_PLAN.md`'s "Deferred Work" section (last touched
2026-08-04, four days after this audit) correctly lists VTSC as not a next
task, to be revisited only after the BYD passive/safety/longitudinal gates
are complete -- that document is accurate; this one wasn't. Contrast with
BRSC below, which really is implemented and tested
(`nagaspilot/controls/ngp_brsc.py` + `nagaspilot/tests/test_ngp_brsc.py`
both exist) -- the same "Ported" language was used for both entries in the
table above despite only one being true. This is the exact failure mode
"Audit evidence" below warns about ("a status label is not accepted as
validation evidence"), and it happened inside the document that coined that
rule. The design intent below is retained as a design, not a status:

NagasPilot VTSC would use Sunnypilot SCC-V's state machine, driver override,
p97 model prediction, and smooth entering/turning/leaving acceleration
policy. It would retain the fixed mild/medium/sharp comfort buckets that
EOP10 attributes to FrogPilot, and would deliberately exclude EOP10 map
handover, GPS curve databases, learned driver speeds, self-calibration,
schema expansion, and adjustable comfort parameters.

If built, it should be user-selectable and default off, shown only for BYD
with openpilot longitudinal selected, with factory BYD ACC never consuming
its target -- none of this is built yet.

## BRSC disposition

Unlike every other entry in this audit, BRSC did not originate as an EOP10
feature being evaluated for portability after the fact -- it was designed from
the start to be identical across `dev/EOP10`, `dev/NGP10`, and `dev/EDP10`. The
policy (`nagaspilot/controls/ngp_brsc.py`, class `NGPBRSC`) has zero
`cereal`/`Params`/messaging imports, so the file itself needed no adaptation for
this branch -- it was copied byte-identical from `dev/EOP10`.

What differs per branch is only the glue:
- **EOP10**: reads `EOPAdaptiveGapEnabled`-style cached `Params()` polling; applies
  via the shared `_apply_speed_limit()` helper that SQSC/RCD/TLSC also use.
- **EDP10** (this branch): no `_apply_speed_limit()` helper exists here, so the
  reduction is applied with a plain `min()` at the same point `force_slow_decel`
  zeroes `v_cruise`; the accel cap is applied the same way TJA's `accel_scale` is,
  at the bottom of `update()`. Enablement follows this branch's existing
  `DPFlags` bitmask pattern (`DPFlags.BRSC`, read once in `plannerd.py` from
  `ngp_lon_brsc`) rather than EOP10's per-frame Params cache, since that's the
  idiom `dp_lon_acm`/`dp_lon_aem` already use here.
- The param itself stays `ngp_lon_brsc` (not translated to a `dp_lon_*` name) on
  purpose -- it is the one identity meant to be shared verbatim, unlike this
  branch's own `dp_*` toggles.

capnp fields (`ngpBrscActive`/`ngpBrscSpeed`/`ngpBrscRoughness`) were added at `@40`-`@42`
on this branch's `LongitudinalPlan` (next free after `allowBrake @39`) -- a
**different field number range than EOP10's `@66`-`@68`**, since the two
branches' schemas have diverged independently and have no common merge base.
Field numbers are per-branch; only the semantic field *names* are shared.

BRSC is user-selectable (`ngp_lon_brsc`, default on) via `dp_panel.cc`,
alongside ACM/AEM.

## Audit evidence

The decision was based on direct branch inspection rather than the EOP10 status
dashboard. That dashboard marks many features complete, while EOP10's own
`docs/upstream-audit/CONTROLS_AUDIT.md` records critical controls defects during
its history. A status label is therefore not accepted as validation evidence.

Required evidence for any future feature consists of focused unit tests,
recorded-route replay, invalid/stale-input tests, bounded output tests and
hardware-in-the-loop results appropriate to the feature's authority.
