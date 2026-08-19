# eGPU (ASM2464PD) integration — design notes (2026-08-17, updated 2026-08-19)

**Naming (2026-08-19):** this branch's `EGPU` flag/constants/functions were
previously named `USBGPU` (inherited from comma's own variable name).
Renamed to `EGPU` to match the single canonical name now used everywhere
in the ecosystem — `dev/EOP10`, `visionpilot`, `humrobot`
(`BackendType.EGPU`/`EgpuBackend`/`egpu.py`) and exopilot's BSP/DTS layer
(already `egpu` before this change). Naming-only; no behavior change
except the activating env var itself is now `EGPU=1`, not `USBGPU=1` — see
exopilot doc §18 for the full rationale (previously three different names
for the same thing with no single decision behind it).

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

- `EGPU = "EGPU" in os.environ` env-var gate, and sets
  `os.environ['DEV'] = 'AMD'` / `os.environ['AMD_IFACE'] = 'USB'` when set.
- A direct `tinygrad` import and a `TICI`-vs-`EGPU` branch for vision
  input handling (`qcom_tensor_from_opencl_address` vs. a generic `Tensor`
  path).
- `selfdrive/modeld/models/big_driving_policy.onnx` and
  `big_driving_vision.onnx` already checked in, plus
  `runners/tinygrad_helpers.py`.

  **Correction (2026-08-19):** these two files are not real distinct model
  weights. `git cat-file -p` on both shows they are git-stored **symlinks**
  pointing at `driving_vision.onnx` / `driving_policy.onnx` — the same
  small models already in use. `git grep` for `big_driving` across the
  branch's Python source returns nothing: nothing loads them today. There
  is no "big model" asset on this branch — the two options below still
  apply if/when one is trained/ported, but until then there is no distinct
  weight file to compile or point a second `ModelState` at.

This is inherited from an **older generation** of upstream's EGPU
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

**Third option surfaced (2026-08-17):** tinygrad natively loads `.onnx`
graphs via `tinygrad.nn.onnx.OnnxRunner` (confirmed by reading
`tinygrad/nn/onnx.py` directly — `OnnxRunner(model_path)` then
`runner(inputs)`, no pkl/JIT-blob compilation step required, unlike
upstream's current `modeld.py` which depends on `modeld_pkl_path()` /
`load_oob()`). This remains the most promising path **once a real
big-model asset exists** — `big_driving_vision.onnx` /
`big_driving_policy.onnx` turned out to be symlinks to the small models
(see correction above), not something to load today. Not evaluated for
correctness or performance against real weights. Full context: exopilot's
`docs/02-HARDWARE/EGPU_ASM2464PD.md` §13 (also records why ONNX Runtime's
ROCm execution provider was ruled out as an alternative runtime — the
ASM2464PD never exposes a kernel-visible PCI device in the plain-USB3 mode
this whole design depends on, so ROCm has nothing to bind to; tinygrad's
own USB3 backend exists specifically to route around that).

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
model selection lives entirely inside `modeld.py`'s `EGPU` env-var gate,
which is exactly the surface this doc is about.

## What was implemented (2026-08-19): presence-gated device selection

The two blockers above (no big-model asset, no hardware to test the
driving path against) rule out porting the additive **big-model** tier
right now. But they don't block a narrower, honest slice of upstream's
pattern that upstream also relies on independently of `ChestnutState`:
**never let device selection point at hardware that isn't there.**

Before this change, `EGPU = "EGPU" in os.environ"` was a blind env-var
gate — if set without the board physically present, `os.environ['DEV'] =
'AMD'` / `os.environ['AMD_IFACE'] = 'USB'` would still fire, and tinygrad
would go looking for a USB device that doesn't exist. `modeld.py` now
checks real USB VID:PID presence (`EGPU_VID_PIDS`, same post-flash IDs
as exopilot's/EOP10's stubs: `0xADD1:0x0001`, `0x3801:0x0001` — tinygrad
corp's own bridge firmware, not comma's Chestnut build) before switching
`DEV`/`AMD_IFACE`, so an unset, forgotten, or stale env var can never leave
modeld pointed at a missing device. `main()` logs whether the eGPU is
active at startup, matching the file's existing `cloudlog.warning`
transparency pattern (no new cereal service — that's still gated on the
same two blockers as `ChestnutState` below).

This is deliberately **not** the additive big-model tier: it changes
*which device* runs the existing small model (falls back to the unchanged
local `QCOM`/`LLVM` path exactly as before when the eGPU is absent, opted
out, or the env var was never set), not *which model* runs. Every current
real-world car is unaffected — `EGPU` unset means the exact same code
path as before this change. This is safe to land without hardware because
the only branch that changes behavior (`EGPU` set *and* the board
present) is unreachable until a board exists; the reachable branch
(everything else) is provably identical to prior behavior.

**Hardened further (2026-08-19), applied identically on `dev/EOP10`'s
`system/inferenced/usb_gpu.py`:** VID:PID alone only rules out most
false positives, not all — some unrelated device could in principle
reuse `0xADD1:0x0001`. Read `tinygrad/extra/usbgpu/patch.py` directly
(not from a summary) to find the literal USB product string it writes
into the flashed firmware's descriptor: `"USB 3.2 PCIe TinyEnclosure"`
(config1, ASCII bytes at offset 64). Unlike comma's Chestnut firmware,
which embeds a per-build hash (`f"custom {CHESTNUT_FW_VERSION}-CLEAN"`,
requiring a firmware-release process to track), tinygrad's generic
firmware uses this fixed literal — no release process on our side to
version against, so an exact string match is the right check.
`_egpu_present()` now reads `/sys/bus/usb/devices/*/product` and
requires it to equal `EGPU_PRODUCT` alongside the VID:PID check.

## Not yet done

- **Get a real big-model asset.** Nothing above can produce one — this is
  an ML training/porting question, out of scope for BSP-level code
  adaptation. Until a distinct (non-symlink) `big_driving_vision`/
  `big_driving_policy` weight file exists, there is nothing for
  `ChestnutState`/`load_big()`-equivalent code to load, regardless of
  which pipeline option is chosen below.
- Decide between the two pipeline options above (still open, still the
  user's call — unchanged by this update).
- Once decided, a big-model asset exists, and hardware exists to verify
  against: port/reimplement `ChestnutState`, `load_big()`/`small_model`,
  `usbgpu_present()`/`usbgpu_compiled()` equivalents, the `chestnutState`
  cereal service, and `selfdrived.py` soft-disable wiring. `usbgpu_present()`
  can reuse this branch's new `EGPU_VID_PIDS`/`_egpu_present()`
  (`modeld.py`) rather than porting upstream's exact-firmware-version
  string match — we don't have a frozen firmware release process to gate
  against.
- ASM2464PD flashing tooling for this branch/device (exopilot doc §8, §10
  — likely reuse `tinygrad_repo`'s own `extra/usbgpu/patch.py` rather than
  porting comma's `flash.py` orchestration wholesale).
