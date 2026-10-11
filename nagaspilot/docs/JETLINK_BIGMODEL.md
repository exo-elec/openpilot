# Big model on the phone over USB (Jetlink port)

Status 2026-10-11: **design and task list only, no code yet.** Branch
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
- Cereal: new enumerants keep the cross-branch ordinal policy of T15 (an
  ordinal means the same on NGP10 and EOP10). **Open:** upstream uses
  `bigModelLoading @100`, `bigModelFailed @102`, which collide with the
  reserved slots NGP10/EOP10 already hold at `@98` to `@102`. Decide before
  touching `log.capnp`.
- Dependency: Jetlink would be a gitlink pinned to an exact public commit
  (policy rules 2 to 4). The comma and the phone's embedded server must pin
  the same Jetlink commit (protocol 3 has no negotiation).
- tinygrad stays the official v0.10.0 gitlink unless a documented reason says
  otherwise (policy rule 5). Jetlink's comma warp needs a compiled
  `driving_warp_{w}x{h}_tinygrad.pkl` and `make_input_queues` from upstream's
  newer pipeline; whether NGP10 can feed Jetlink from its existing OpenCL warp
  instead is T16.1, answered below.

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

- Ordinal choice for the two big-model events (above).
- Own USB PID before any distribution (Jetlink uses the pid.codes test `1209:0001`).
- Model licensing and LFS catalog access for distributed builds.
- No device was available for any of this: nothing is build- or replay-tested.
