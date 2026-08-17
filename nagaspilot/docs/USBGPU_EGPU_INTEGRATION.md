# USB eGPU (ASM2464PD) integration — design notes, no code changed (2026-08-17)

**Source of truth for hardware/firmware:** `exopilot`'s
`docs/02-HARDWARE/EGPU_ASM2464PD.md` documents the full USB-port wiring on
ExoPilot 01M/02M, the ASM2464PD firmware-flashing path, and the tinygrad
fork audit. This file records what that means for `dev/NGP10`'s
`selfdrive/modeld/modeld.py` specifically — read the exopilot doc first for
the hardware/firmware background, which is not repeated here.

## What this is

ExoPilot is building a custom eGPU board (ASM2464PD bridge chip + a
desktop-class AMD Radeon GPU purchased separately, not comma's bundled RX
9060) and wants an **additive** inference tier on it: the local driving
model stays always-loaded and authoritative, a bigger model on the eGPU
runs opportunistically on top, and the system soft-disables back to the
local model if the eGPU is absent or fails — never a bare replacement with
no fallback. This matches upstream openpilot's own `modeld.py` design
(`ChestnutState`, `load_big()`/`small_model`, `bigModelFailed` soft-disable
event in `selfdrived.py`), not something ExoPilot invented.

## Why this doc has no code in it

Two independent blockers, not a scope decision:

1. **No hardware exists yet.** The ASM2464PD ships in a ROM-bootloader
   state and must be flashed before it's usable (exopilot doc §8). Every
   branch of eGPU logic — big-model load, timeout, fallback-on-exception,
   telemetry — would be dead code that cannot be exercised, in the file
   that runs the car's driving model. That is the highest-risk, lowest-
   verification kind of change available on this branch, so it isn't being
   made speculatively.
2. **NGP10's model-running pipeline predates the pattern being ported.**
   See below — this isn't a small backport, it's blocked on a design
   decision about how far to follow upstream's pipeline changes.

## What NGP10 already has, vs. current upstream

Diffed `dev/NGP10:selfdrive/modeld/modeld.py` against
`~/pilot/ext_gpu/openpilot-upstream`'s current `modeld.py` (comma's actual
Chestnut port). NGP10 is not starting from zero — it already has:

- `USBGPU = "USBGPU" in os.environ` env-var gate, and sets
  `os.environ['DEV'] = 'AMD'` / `os.environ['AMD_IFACE'] = 'USB'` when set.
- A direct `tinygrad` import and a `TICI`-vs-`USBGPU` branch for vision
  input handling (`qcom_tensor_from_opencl_address` vs. a generic `Tensor`
  path).
- `selfdrive/modeld/models/big_driving_policy.onnx` and
  `big_driving_vision.onnx` already checked in, plus
  `runners/tinygrad_helpers.py`.

This is inherited from an **older generation** of upstream's USBGPU
support — it predates the current Chestnut-era architecture. What's
missing, present in current upstream but not here:

- `ChestnutState` (a small, mostly self-contained telemetry class reading
  `Device["AMD"].iface.dev_impl.smu` for temperature/power/clock — the one
  piece of the upstream diff that doesn't depend on the pipeline shape
  below, so it could in principle be added standalone once hardware exists).
- The `load_big()` / `small_model` dual-load pattern in `main()`, run on a
  worker thread with a timeout, plus the `model = small_model` fallback and
  `params.put_bool("UsbGpuActive", ...)` state tracking.
- `usbgpu_present()` / `usbgpu_compiled()` presence/readiness gating
  (upstream's `selfdrive/modeld/helpers.py` — **confirmed this file does
  not exist on NGP10 at all**, `git show dev/NGP10:selfdrive/modeld/helpers.py`
  fails).
- The `chestnutState` cereal service and its publish cadence.
- Soft-disable wiring in `selfdrived.py` (`bigModelLoading`/`bigModelFailed`
  events) — not checked in detail here, blocked on the same pipeline
  question below.

## The actual blocker: NGP10's `ModelState` predates upstream's pipeline

The eGPU pieces above are not separable from a **pipeline-generation
upgrade**. NGP10's `ModelState` and upstream's current `ModelState` are
different classes:

| | NGP10 (current) | Upstream (current) |
|---|---|---|
| Constructor | `ModelState(context: CLContext)` | `ModelState(cam_w, cam_h, usbgpu)` |
| `run()` signature | `run(bufs, transforms, inputs, prepare_only)` | `run(bufs, transforms, inputs)` |
| Model artifacts | separate `driving_vision_tinygrad.pkl` / `driving_policy_tinygrad.pkl`, loaded via `pickle.load()` | one combined JIT blob via `modeld_pkl_path()` / `load_oob()` |
| Vision→policy path | two separate `vision_run()` / `policy_run()` calls, glue code combines `vision_output`/`policy_output` dicts | a single `warp()` + `run_policy()` call chain, `Tensor.from_blob` + an input-queue cache |
| Output contract | `combined_outputs_dict` from two slice dicts | one `model_output` array with an `action` key baked in |
| Relevant cereal fields | `liveCalibration`, `roadCameraState`, `liveDelay` | `extrinsicsCalibration`, `narrowRoadCameraState`, `lateralDelay` |

`load_big()` in upstream constructs `ModelState(w, h, True)` and calls
`m.warmup()` — neither exists on NGP10's class. The fallback path assigns
`model = small_model` and reads `model.usbgpu` — also not on NGP10's
class. **The additive-tier logic cannot be lifted onto NGP10 as-is.**

## Two implementation options (decision needed before writing code)

1. **Migrate NGP10's `ModelState`/pipeline to upstream's current shape**
   first, as its own change, independent of eGPU. Then the eGPU pieces
   port over close to verbatim. Larger diff, touches the live driving path
   for every car on this branch (not just eGPU-equipped ones), but keeps
   NGP10 aligned with upstream going forward and makes future upstream
   backports easier.
2. **Reimplement the additive-tier pattern natively against NGP10's
   existing `ModelState`** — add a second `ModelState` instance built the
   NGP10 way (own `CLContext`, own pkl paths pointed at the `big_*.onnx`-
   derived pkls once compiled), with the same load-on-worker-thread /
   timeout / fallback-on-exception shape but against NGP10's actual
   constructor and `run()` signature. Smaller diff, isolated to the eGPU
   path, but is NGP10-specific code that upstream doesn't have and won't
   match if/when NGP10 does eventually pick up the pipeline upgrade for
   other reasons.

**Third option surfaced (2026-08-17), not yet evaluated:** tinygrad
natively loads `.onnx` graphs (`extra/onnx_helpers.py`, confirmed by
example scripts in `~/pilot/tinygrad`) — it does not require the
pkl/JIT-blob compilation step upstream's current `modeld.py` uses
(`modeld_pkl_path()`/`load_oob()`). Since `big_driving_vision.onnx` and
`big_driving_policy.onnx` are already `.onnx` files, a `ModelState` variant
that hands them to tinygrad's ONNX frontend directly could sidestep
needing upstream's specific offline compilation tooling under either
option 1 or option 2 above. Not evaluated for correctness or performance —
recorded as a variable in the decision, not a resolved third path. Full
context: exopilot's `docs/02-HARDWARE/EGPU_ASM2464PD.md` §13 (also records
why ONNX Runtime's ROCm execution provider was ruled out as an alternative
runtime — the ASM2464PD never exposes a kernel-visible PCI device in the
plain-USB3 mode this whole design depends on, so ROCm has nothing to bind
to; tinygrad's own USB3 backend exists specifically to route around that).

Per ExoPilot's decision (2026-08-17): EOP10's eventual eGPU tier will reuse
NGP10's `big_driving_vision.onnx`/`big_driving_policy.onnx` models rather
than sourcing new ones — whichever option is chosen here, keep those model
files as the shared artifact between the two branches' eGPU tiers.

## What was done instead (see `dev/EOP10`, not this branch)

A presence-detection-only `HardwareBackend` stub
(`system/inferenced/usb_gpu.py`) was added on `dev/EOP10`: it probes for
the post-flash ASM2464PD USB VID:PIDs and reports unavailable if absent,
with no driving-path code and no WorkloadClass tier wiring. That pattern
doesn't apply here — NGP10 has no `system/inferenced/` HAL (confirmed via
`git ls-tree`, 25 files / 6420 lines present on EOP10 only) — NGP10's
model selection lives entirely inside `modeld.py`'s `USBGPU` env-var gate,
which is exactly the surface this doc is about.

## Not yet done

- Decide between the two options above.
- Once decided and hardware exists: port/reimplement `ChestnutState`,
  `load_big()`/`small_model`, `usbgpu_present()`/`usbgpu_compiled()`
  equivalents, the `chestnutState` cereal service, and `selfdrived.py`
  soft-disable wiring.
- Compile `big_driving_vision.onnx`/`big_driving_policy.onnx` into
  whatever pkl/blob format the chosen `ModelState` shape expects.
- ASM2464PD flashing tooling for this branch/device (exopilot doc §8, §10
  — likely reuse `tinygrad_repo`'s own `extra/usbgpu/patch.py` rather than
  porting comma's `flash.py` orchestration wholesale).
