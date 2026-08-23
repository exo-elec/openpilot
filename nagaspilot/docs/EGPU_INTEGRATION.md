# eGPU (ASM2464PD) integration — design notes (2026-08-17, updated 2026-08-19, 2026-08-23)

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

## Why the original design pass (below) had no code in it

**Update 2026-08-23: this section describes the state as of 2026-08-19.**
Code now exists — see "What was implemented (2026-08-23)" further down.
Blocker 2 (pipeline decision) is resolved; blocker 1 (no hardware) still
applies to everything written since, which is why it's all still provably
inert. Kept as-written below for the historical reasoning.

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

## What was implemented (2026-08-23): big-model tier scaffolding

**Decision made:** option 2 above ("reimplement the additive-tier pattern
natively against NGP10's existing `ModelState`"), on explicit user
direction — port Chestnut support into the v0.10.0-era pipeline as-is,
using the already-pinned tinygrad `v0.13.0`, rather than first migrating
NGP10's model-running pipeline to upstream's current shape. Verified the
specific tinygrad APIs this needs (`Device["AMD"].iface.dev_impl.smu`,
`PPSMC_MSG_*`/`TABLE_SMU_METRICS` constants, USB bridge-chip register
access via `iface.pci_dev.usb`) all exist at `v0.13.0` by reading
`tinygrad_repo`'s own source directly before writing code against them —
not assumed from upstream's newer pin.

`ModelState(context, usbgpu: bool = False)` now resolves `big_`-prefixed
pkl paths for the eGPU variant, using the exact same `pickle.load()`
pattern this branch's own artifacts already use (deliberately not
upstream's newer out-of-band buffer format via `load_oob()`, which needs
artifacts saved in that specific layout — this branch's tooling doesn't
produce that).

`main()` now has the full `load_big()`/`small_model` dual-load pattern:
background thread with a timeout (`EGPU_LOAD_TIMEOUT`), gated on **both**
`EGPU` hardware presence **and** a new `EgpuDrivingEnabled` Param
(`PERSISTENT`, defaults off — hardware presence alone is not sufficient
opt-in, matches `dev/EOP10`'s `ChestnutDrivingEnabled` gate exactly).
`model.run()` is wrapped in try/except: on failure while the big model is
active, soft-disables (`EgpuDrivingActive` → false), falls back to
`small_model`, and is never auto-retried onroad (only re-evaluated at the
next modeld restart) — same failover contract as `dev/EOP10`'s
`ChestnutDrivingRunner`. `EgpuDrivingLoading`/`EgpuDrivingActive` Params
(`CLEAR_ON_MANAGER_START`) track state the way `UsbGpuLoading`/
`UsbGpuActive` do upstream.

`ChestnutState` was ported and **renamed to `EgpuState`**, on explicit user
direction: this branch supports both our own flashed firmware and comma's
Chestnut firmware on the same physical ASM2464PD bridge chip (confirmed:
`CHESTNUT_USB_IDS` in upstream's `common/hardware/usb.py` is byte-identical
to this branch's `EGPU_VID_PIDS`, distinguished only by USB product
string), so the telemetry class/message shouldn't be named after one
specific firmware. Added a new `EgpuState` cereal struct + `egpuState`
service (renamed from `chestnutState`). All AMD SMU/bridge-register access
stays defensively wrapped exactly as upstream's own `ChestnutState.send()`
already does, so any remaining tinygrad-version API drift in the
peripheral telemetry fields (a couple of exact method names weren't
independently re-verified, e.g. `usb.control_read`) degrades gracefully —
that field's telemetry is just unavailable, it can't block driving.

**`_egpu_present()` was extended for dual-firmware detection** (moved out
of `modeld.py` into its own `selfdrive/modeld/egpu_detect.py` module, so
it's unit-tested — `selfdrive/modeld/tests/test_egpu_detection.py`, 7
cases — without needing modeld's full hardware-oriented import chain).
Recognizes our own firmware's product string (unchanged, still
primary/default) **and** comma's Chestnut firmware string
(`f"custom {CHESTNUT_FW_VERSION}-CLEAN"`, matching upstream's
`common/hardware/usb.py` exactly) on the same VID:PID pair — per user
direction: "we do not just use chestnut we use asm2464pd too... i want my
own EGPU to flash not chestnut but both support."

**What this doesn't change:** none of the above is reachable yet. No
`big_driving_{vision,policy}_tinygrad.pkl` exists anywhere (the old
`big_driving_vision.onnx`/`big_driving_policy.onnx` symlinks noted above
are a different, unrelated artifact — not compiled tinygrad pickles, and
the new code doesn't reference them), so `ModelState(cl_context,
usbgpu=True)` fails at `open()` and `load_big()` catches that, leaving
`model = small_model` always — identical to today's on-road behavior for
every real car, same reasoning as the 2026-08-19 device-selection change's
safety argument.

**Explicitly documented, not silently skipped:** no `warmup()` equivalent.
Upstream's `ModelState.warmup()` runs a dummy inference with plain numpy
frames before trusting a loaded big model; NGP10's `run()` needs real
`VisionBuf` camera objects bound to the CL context (`self.frames[name]
.prepare(bufs[name], ...)`), so a synthetic warmup can't be written and
verified without real hardware from a dev PC. Flagged inline in
`modeld.py` for whoever wires in a real compiled artifact — "do not trust
'it loaded' as proof 'it runs'."

**Bonus find, fixed:** `cereal/log.capnp` referenced
`Car.RadarData.ErrorDEPRECATED`, a type opendbc renamed to plain `Error`
(opendbc commit `ff2ac79e`); this branch's own `opendbc_repo` pin already
had the rename, so `import cereal` failed outright at schema-compile time
— pre-existing (predates this branch's fork entirely), already known and
worked around in a couple of test files rather than fixed (see
`NGP10_FEATURE_MATRIX.md`). Verified `EXO-ELEC/opendbc`'s actively
maintained `master` (checked same-day) also calls it plain `Error` before
making this the actual fix, not a guess.

**Tinygrad pin:** stayed on `v0.13.0` (this branch was previously on a much
older `c30a113b`, from 2025-08-17) — `EOP10` was also moved to `v0.13.0` in
the same session so both branches track the same commit. Considered
bumping further to match real upstream's current pin (`8611fe22a`, needed
for its newer fused-model Chestnut architecture) but reverted on user
direction to stay on the stable `v0.13.0` tag; see
`NGP10_CHESTNUT_MIGRATION_PLAN.md` for the fuller reasoning on why that
newer pin isn't actually required for this branch's own (older-generation,
option-2) eGPU pipeline.

## Not yet done

- **Get a real big-model asset.** Still nothing above can produce one —
  ML training/porting question, out of scope for BSP-level code
  adaptation. This remains the actual, single blocker to any of the above
  becoming reachable.
- **No hardware exists yet to verify any of this against** — same
  blocker #1 from the top of this doc, unchanged.
- `usbgpu_compiled()` equivalent (a readiness check for whether a compiled
  big-model artifact is actually present) — not added; right now
  `load_big()`'s own `try/except FileNotFoundError`-via-`open()` serves
  that purpose implicitly. Worth a named helper once a real artifact
  exists and this gets exercised for real.
- `selfdrived.py` soft-disable wiring (`bigModelLoading`/`bigModelFailed`
  events) — not done. `modeld.py`'s own `EgpuDrivingActive` Param already
  carries the state; `selfdrived.py` doesn't consume it yet.
- Real `warmup()` validation (see above) — needs real hardware to write
  and verify, can't be done from a dev PC.
- ASM2464PD flashing tooling for this branch/device (exopilot doc §8, §10
  — likely reuse `tinygrad_repo`'s own `extra/usbgpu/patch.py` rather than
  porting comma's `flash.py` orchestration wholesale). Unchanged from
  2026-08-19.
- The pipeline-migration question (option 1 above) is not closed forever —
  it was deferred, not rejected, for this pass specifically because the
  goal was Chestnut capability on the current stable v0.10.0 foundation
  without disturbing what's already shipping.
