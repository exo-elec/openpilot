# NGP10 Chestnut/eGPU — Plan (revised)

Status: **scoping only, no code changed on NGP10 yet**. Revised 2026-08-23
after clarifying the actual product goal: NGP10 ships a stable product on
real comma 3 hardware, running the openpilot v0.10.0-era model plus a suite
of NagasPilot add-ons (DLAT/DLON, road-edge detection, etc.). The ask is to
add Chestnut/eGPU capability so the product reaches comma-4-like capability
(or better, via the add-ons) — not to experiment with an architecture that
might destabilize what's already shipping.

## The decisive fact that changes everything

EOP10's `ChestnutDrivingRunner.load()` **unconditionally raises** — even
with a compiled artifact on disk, it's deliberately stubbed shut pending
transport + replay/HIL gates (`selfdrive/modeld/runners/chestnut_driving_runner.py:61-65`).
So "port EOP10's eGPU" would only port scaffolding, not a working feature —
EOP10 doesn't have one yet either.

*But* EOP10's blocker (no compiled tinygrad artifact) exists **specifically
because EOP10 runs on non-comma hardware** — a homegrown RK3588 board with no
path into comma's model-distribution pipeline. **NGP10 runs on real comma 3
hardware.** If NGP10 pairs with comma's actual official Chestnut USB-GPU
accessory and tracks upstream openpilot closely enough, the compiled model
artifact plausibly comes down through comma's normal OTA/model channels —
the same way the small model already does. In that scenario the "hard
blocker" that stops EOP10 dead may simply not apply to NGP10 at all.

This is the real fork in the road, and it's what your last message is
pointing at. Two genuinely different paths:

## Path A — Bolt a fail-closed Chestnut stub onto the current foundation

Port EOP10's pattern (`DrivingRunner` contract, `ChestnutDrivingRunner`,
`factory.py` selection, Params state machine) onto NGP10 as-is, wrapping the
existing v0.10.0 split model as the "small" runner. Add dual eGPU firmware
detection (your own flashed firmware + comma's, per your earlier answer).

- **Pro:** Small, safe, doesn't touch the shipping v0.10.0 model or
  DLAT/DLON at all. Buildable in a normal session.
- **Con:** Chestnut still can't actually run afterward — same blocker as
  EOP10, now inherited by NGP10 too. You'd be shipping scaffolding, not
  capability, until the artifact problem is separately solved (self-compile,
  or some other channel).
- **Effort:** days, not weeks. Everything needed already exists as a working
  pattern in EOP10 to adapt.

## Path B — Sync NGP10's foundation closer to current upstream openpilot

Update NGP10's base openpilot version toward current `master`
(`commaai/openpilot@b7c333cf`, 2026-08-12, or whatever the latest stable
release tag is by the time this starts), which has a real, working Chestnut
implementation designed to run on comma's actual hardware/distribution — then
re-layer the four NagasPilot integration points (found in the earlier
architecture comparison, still valid) on top of the new foundation.

- **Pro:** The only path that plausibly gets you *real*, comma-supported
  Chestnut without solving model compilation yourselves. Also picks up
  whatever else upstream has improved since your current base — genuinely
  "reaching comma-4-like capability" is much more credible this way.
- **Con:** This is a real fork-sync/rebase project, not a feature port. Every
  divergence found in the first pass of this investigation has to be
  resolved, not just Chestnut-related ones:
  - `ModelConstants` restructured (`MODEL_FREQ`→`MODEL_RUN_FREQ`/`MODEL_CONTEXT_FREQ`,
    not a rename — the shape changed)
  - Message/service renames: `roadCameraState`→`narrowRoadCameraState`,
    `liveCalibration`→`extrinsicsCalibration`, `liveDelay`→`lateralDelay`
  - Whatever else has moved between your v0.10.0-era base and current
    upstream in `selfdrive/controls/`, `cereal/`, `opendbc` submodule pin,
    etc. — this investigation only looked at `modeld.py` so far; a real sync
    touches far more surface area.
  - Re-verifying DLAT/DLON, road-edge detection, and every other NagasPilot
    feature still behaves correctly against the new foundation — this is
    safety-critical, on-road behavior, not a refactor you can validate from
    a dev PC.
- **Effort:** genuinely weeks, likely needs staged rollout (shadow-mode
  testing before it's trusted on a paying customer's car).

## Recommendation for next session

Don't decide this from a plan doc alone. Before committing to either path:

1. **Confirm what "comma's official Chestnut hardware" actually requires** —
   does it need stock/near-stock openpilot to get OTA model updates, or does
   comma's model distribution work independent of how customized the rest of
   the fork is? This fact alone determines whether Path A's blocker is
   real for NGP10 or not. (Not something I can verify without checking
   comma's actual update/model-distribution mechanism — worth a targeted
   look before choosing.)
2. If Path B: scope the *full* diff (not just `modeld.py`) between NGP10's
   current base and target upstream version, so the size of the sync is
   known before starting, not discovered mid-way.
3. If Path A: it's ready to start as soon as you want — nothing further to
   discover, the pattern is proven in EOP10 today.

Both paths are compatible with each other long-term — Path A's scaffolding
isn't wasted if you later do Path B; the `DrivingRunner` abstraction pattern
would still be the right shape either way.

---

*Original architecture-comparison detail (NGP10 vs. upstream `modeld.py`,
file-by-file inventory, NagasPilot integration points that must survive any
port) preserved below for reference.*

## Architecture comparison: NGP10 today vs. upstream `master`

| | NGP10 today | Upstream `master` (b7c333cf) |
|---|---|---|
| Model shape | Split vision + policy, two tinygrad-pickle graphs | One fused JIT graph, action head built in |
| Output | `plan` array → `get_accel_from_plan`/`get_curvature_from_plan` | `action` output directly (`model_output['action']`) when present |
| Model source | `driving_vision_tinygrad.pkl` / `driving_policy_tinygrad.pkl`, loaded via raw `pickle.load` | Compiled via `compile_modeld.py` → `modeld_pkl_path(usbgpu)`, loaded via `load_oob()`/`open_file_chunked` |
| "Big" model | **Fake** — `big_driving_vision.onnx`/`big_driving_policy.onnx` are symlinks aliasing the small model | Real second compiled graph, same contract, bigger weights |
| eGPU role | Same small model, just runs faster on `Device=AMD` | Genuinely different (bigger) compiled graph |
| Telemetry | None | `ChestnutState` — AMD SMU metrics + ASM2464PD bridge-chip registers (`0xB450` PCIe LTSSM, voltage/current) over `chestnutState` message |
| Failover | N/A (no real big model to fail from) | try/except around `model.run()`, `UsbGpuActive` Param flips to `False`, falls back to `small_model`, no auto-retry (Param only cleared at restart) |
| `ModelConstants` | `MODEL_FREQ`/`HISTORY_FREQ`/... (old shape) | `MODEL_RUN_FREQ`/`MODEL_CONTEXT_FREQ` (new shape) — not additive, a rename/restructure |
| Camera/calib services | `roadCameraState`, `liveCalibration`, `liveDelay` | `narrowRoadCameraState`, `extrinsicsCalibration`, `lateralDelay` |

The `EGPU_VID_PIDS`/`EGPU_PRODUCT` detection NGP10 already has targets the
**same physical ASM2464PD USB-to-PCIe bridge chip** that upstream's
`ChestnutState` reads registers from (`asm.iface.pci_dev.usb`, same `0xB450`
register) — strong evidence it's the same hardware, different firmware
images. Supports dual detection either way (keep your own flashed firmware
as primary, detect comma's Chestnut firmware alongside it).

## NagasPilot-specific logic that must survive either path

Currently lives inline in `modeld.py`'s main loop — this is the real
value-add over stock openpilot and must not get lost:

- `from nagaspilot.controls.ngp_road_edge import evaluate_road_edges` —
  called with `modelv2_send.modelV2.roadEdgeStds`/`laneLineProbs`, gated by
  `ngp_lat_road_edge_detection` param.
- `from nagaspilot.controls.ngp_dlat import NGPDLAT, DEFAULT_ENTER_THRESHOLD`
  — `NGPDLAT.lane_confidence(...)` feeds `low_lane_confidence` into
  `DH.update(...)`. Explicitly a one-shot static check, distinct from the
  stateful arbiter in `controlsd.py` — do not conflate the two.
- `DesireHelper(ngp_lca_speed_mph=..., ngp_lca_auto_sec=...)` — NGP10's
  `DesireHelper` constructor takes NGP-specific params upstream's doesn't.
- `DH.update(..., left_edge_detected=, right_edge_detected=,
  low_lane_confidence=)` — extra kwargs vs. upstream's plain
  `DH.update(carState, latActive, laneChangeProb)`.

## Files: exists / missing / needs restructure (for Path A or B)

| File | NGP10 | Upstream | Note |
|---|---|---|---|
| `selfdrive/modeld/modeld.py` | old architecture, NagasPilot integration baked in | new architecture | Path A: leave as-is, add second runner. Path B: rewrite, re-wire NagasPilot hooks |
| `selfdrive/modeld/helpers.py` | missing | `usbgpu_present`, `usbgpu_compiled`, `modeld_pkl_path`, `get_tg_input_devices`, `load_oob` | Needed for Path B only |
| `selfdrive/modeld/compile_modeld.py` | missing | Compiles ONNX → tinygrad JIT pickle | Needed for Path B only |
| `cereal/services.py` | has `drivingModelData`, no `chestnutState` | has both | Needed either path, once a real Chestnut runner exists |
| `tinygrad_repo` pin | `c30a113b` (2025-08-17) | `8611fe22a` (2026-08-10) | Path A: bump only as far as EOP10's proven `v0.13.0` needs. Path B: bump to match target upstream version exactly |
