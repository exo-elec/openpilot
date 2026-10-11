# Big model on the phone over USB (Jetlink port)

Status 2026-10-11: **comma side implemented on NGP10 and carried to EOP10, 01M and 02M (branches `jetlink/*`), default off; nothing built, replayed or run on a device.** Hardware side (USB-C device-mode overlays, eGPU-xor-phone switch) is in `exopilot` branch `feat/phone-bigmodel-usb-port`. Branch
`jetlink/ngp10-bigmodel` off `origin/dev/NGP10`. Base unchanged: official
openpilot v0.10.0 `c085b8af1` plus NGP10 commits (decision: keep the base, port
the features; no rebase onto newer upstream).

Reference: [`exo-elec/jetlink`](https://github.com/exo-elec/jetlink) `main` v0.8.5
(MIT; link protocol 3, comma-side `jetlink/comma` + `jetlink/openpilot`). Phone
side lives in NavPilot (`navpilot/docs/architecture/BIGMODEL_WIRED_PLAN.md`);
the phone is the only model host. Jetson, Mac and PC hosts are out of scope.

Related: [`EGPU_INTEGRATION.md`](EGPU_INTEGRATION.md) (the Chestnut tier; shares
the big-model contract work), [`BOUNDARIES.md`](BOUNDARIES.md),
[`DEPENDENCY_POLICY.md`](DEPENDENCY_POLICY.md),
[`CODE_BOUNDARY_TASKS.md`](CODE_BOUNDARY_TASKS.md) (tasks T16 to T17).

## What the port must provide

Jetlink never imports openpilot. The fork implements one adapter
(`jetlink.openpilot.interface.Openpilot`, API 3) and Jetlink calls through it.
On NGP10 that adapter must supply:

| Need | Jetlink side | NGP10 today |
| --- | --- | --- |
| Params and `IsOffroad`, JSON progress/spec keys | `Keys`, `StatusSide.get/put` | `Params` exists; new `ngp_` keys needed |
| Camera geometry and warp location | `camera()`, `warp_path()` | v0.10.0 uses OpenCL `DrivingModelFrame`; no compiled warp pickle |
| Model face: Parser, constants, smoothing, `get_action_from_model` | `ModelFace` | present, but v0.10.0 constants (`MODEL_FREQ`, `FEATURE_LEN` 512, no `ACTION_WIDTH`) |
| `in_control()` | `ModelSide` | derive from `carControl`/`selfdriveState` messages |
| `modelV2.big` and an accelerator state | status to UI/selfdrived | **absent** from cereal |
| Engagement hold while loading/proving; soft-disable on failure | `selfdrived` | **absent** (no big-model events) |
| Chestnut USB IDs to stand aside from | `OwnerConfig.chestnut_ids` | `EGPU_VID_PIDS` in `egpu_detect.py` |

## Architecture rules (house rules applied)

- `selfdrive/` stays near upstream: only hooks in `modeld.py`, `selfdrived.py`,
  `cereal/log.capnp`. Everything else is under `nagaspilot/` (pure cores in
  `nagaspilot/controls`-style modules, adapter in `nagaspilot/runtime/`).
- New behaviour is default off behind `ngp_` params; the small model is always
  loaded and authoritative, the phone model is additive with one-way fallback.
- The model-swap safety rules are kept as Jetlink has them: never swap while
  the plan is steering, a proving window after a swap, held frame on a late
  reply, hand back after 5 late frames in a row or more than 20 in 10 s, lost
  link (0.2 s) hands back, an engaged failure soft-disables.
- Footprint budget is ratcheted per hook (`nagaspilot/footprint.py`); every
  added hook line is named in the budget diff.
- Cereal: ordinals keep the cross-branch policy of T15 (an ordinal means the same on NGP10 and EOP10).
  **Decided:** upstream's own `bigModelLoading @100` and `bigModelFailed @102` take over NGP10's reserved
  placeholders `reserved100DEPRECATED` and `reserved102DEPRECATED` (nothing was ever emitted there), and
  `ModelDataV2.big @27` is free on both branches. No ordinal is added or moved, so EOP10's `@109+` are untouched.
  EOP10, 01M and 02M must carry the same rename (T17).
- Dependency: Jetlink would be a gitlink pinned to an exact public commit
  (policy rules 2 to 4). The comma and the phone's embedded server must pin
  the same Jetlink commit (protocol 3 has no negotiation).
- tinygrad stays the official v0.10.0 gitlink unless a documented reason says
  otherwise (policy rule 5). Jetlink's comma warp needs a compiled
  `driving_warp_{w}x{h}_tinygrad.pkl` and `make_input_queues` from upstream's
  newer pipeline; whether NGP10 can feed Jetlink from its existing OpenCL warp
  instead is T16.1, answered below.

## What is implemented (branch `jetlink/ngp10-bigmodel`, pushed 2026-10-11)

| Task | State |
| --- | --- |
| T16.1 warp source | ☑ spike below; implemented as `nagaspilot/runtime/bigmodel_warp.py` |
| T16.3 modeld | ☑ hook (+12 lines, budgeted) over `nagaspilot/runtime/bigmodel.py`: facade between modeld and Jetlink's joining model, `action_t`, `prepare_only`, `modelV2.big`, handover reset |
| T16.4 cereal | ☑ `bigModelLoading @100`, `bigModelFailed @102` (renamed placeholders), `ModelDataV2.big @27` |
| T16.5 selfdrived | ☑ `ngp_bigmodel_gate.py`: loading blocks entry; failure announces once and soft-disables only if it was driving while engaged; latch clears on disengage |
| T16.6 adapter | ☑ `jetlink_adapter.py` passes Jetlink's `conformance()` and uses Jetlink's optional `warp_source` seam; the Jetlink changes are on `exo-elec/jetlink` branch `static-role-platform` and `jetlink_repo` is pinned to `37e89e32` (see `DEPENDENCY_POLICY.md`) |
| T16.7 setting | ☑ `ngp_bigmodel_link` INT (0 off, 1 USB/Android, 2 iOS) + Jetlink's JSON keys; manager starts `jetlinkd` (TICI only). No UI toggle yet |
| T16.8 footprint | ☑ budget raised by name (events +9, selfdrived +4, modeld +12) |
| T16.2, T16.9 device | ☐ needs a comma 3X |

## Behaviour choices

- **A hand-back soft-disables while engaged, and is one-way for the drive.** Decided by the requirement to behave like
  upstream's Chestnut ([`CHESTNUT_PARITY.md`](CHESTNUT_PARITY.md)): fail, fall back to the small model, never switch back onroad.
  Jetlink's own late join and rejoin is the `JETLINK` policy in `bigmodel.py`, not the default.
- **Settling window: 5 s** after a load ends and after a failure, as upstream's `big_model_settling`: `commIssue`, the
  localizer checks and `frameDropPerc` are suppressed (`NGPBigModelGate.settling()`; see [`CHESTNUT_PARITY.md`](CHESTNUT_PARITY.md) row 13).
- **Dropped camera frames while the big model drives still run it**, as upstream does (it has no `prepare_only`):
  the server's history advances once per frame sent, and skipping one would lose a second frame and leave a
  hand-back one short. On the small model a dropped frame stays prepare-only.
- **`action_t` is built the way upstream builds it** from the raw lateral delay and the longitudinal actuator
  delay plus the *big* model's smoothing (`ngp_bigmodel_action.action_times`), not modeld's small-model values,
  because the model is conditioned on it.
- **`in_control()` fails towards "in control"**: until `carControl` and `selfdriveState` have both been seen and are
  alive it says True, so a swap never lands on a frame nobody can vouch for.
- **A dead `jetlinkd` is a `processNotRunning` event** (NO_ENTRY and SOFT_DISABLE), so the manager only starts it
  when a Jetlink checkout exists (`jetlink_present()`), and `modeld` stays a plain small-model `modeld` without one.
- **Big-model smoothing is upstream's** (lateral 0.0 s, longitudinal 0.3 s), not NGP10's small-model 0.1 s.

## Unverified (needs hardware or a recorded route)

- OpenCL warp vs the warp the big models were trained with (frame parity), and NGP10's `Parser` against a real
  big-model output (slice names/shapes, `action` head units).
- That AGNOS on NGP10 has FunctionFS, configfs and the charger debugfs voter `jetlink-root.sh` uses (comma 3X);
  the original comma 3 is not named by Jetlink.
- Rebuilding `common/params` (new keys) and cereal; `modeld.py` has only been byte-compiled.
- Timing: the facade, shim and frame copy add CPU work on modeld's frame thread; measure against the 50 ms budget.

## T16.1 result: the warp (2026-10-11, read from source, not run)

NGP10's `DrivingModelFrame` (`selfdrive/modeld/models/commonmodel.cc`) already
produces what Jetlink sends. `MODEL_WIDTH 512 x MODEL_HEIGHT 256` gives a frame of
`512*256*3/2 = 196,608` bytes, which is exactly `6 x 128 x 256` (4 Y phases, U, V).
`prepare()` fills `input_frames_cl` with two frames, the older one
`temporal_skip` frames back and the newest (`last_img_cl`) second. Jetlink wants
only the newest frame per camera, stacked narrow + wide as `(2, 6, 128, 256)`
uint8 (`ModelSpec.warped_shape`). So:

- The frame is the second half of `buffer_from_cl()` for `img` and `big_img`; the
  small model already pays for the warp, so the phone path adds no GPU work
  (Jetlink's own comma path runs a second tinygrad warp).
- tinygrad can stay the official v0.10.0 gitlink; no compiled warp pickle and no
  `make_input_queues` are needed on the comma, because the server keeps the frame
  and state queues.
- **Risk, unverified:** the big models were trained against upstream's newer
  warp. NGP10's OpenCL transform is a different implementation and may differ in
  interpolation/rounding. Needs a replay comparison of the same camera frame
  through both warps (device or recorded route) and model-output parity before
  the phone model is allowed to drive.
- Jetlink's lossless MED packing (from its `wifi-lossless` branch) is not needed
  on a USB link.

## Hardware note

The comma 3/3X is Jetlink's native target (`jetlink-root.sh`, FunctionFS gadget,
USB-C PD port handling). Check on a device that this AGNOS has FunctionFS,
configfs and the charger debugfs voter (T16.2). The original comma 3 is not
named by Jetlink.

## Not done / decisions pending

- Own USB PID before any distribution (Jetlink uses the pid.codes test `1209:0001`).
- Model licensing and LFS catalog access for distributed builds.
- No device was available for any of this: nothing is build- or replay-tested.
