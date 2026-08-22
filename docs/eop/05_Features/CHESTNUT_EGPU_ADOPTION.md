# Chestnut-pattern eGPU adoption for RK3588

## Decision

EOP will follow current upstream openpilot's Chestnut architecture for any
driving model placed on the external USB GPU, with the RK3588 RKNN model kept
loaded as the on-device safety fallback.

"Chestnut" is comma's ASM2464 external-GPU hardware/release path, not a model
runner class. The upstream pattern was audited at `commaai/openpilot` master
commit `084747c75d2cbd23af65ab7a9e770bbd7b98bac9` (2026-08-21). Upstream
0.11.2 documents an 880M-parameter big model and external-GPU support.

This is an architectural port, not a blind file copy. Upstream uses Qualcomm
devices and lets `modeld` exclusively open Chestnut. EOP uses RK3588 and needs
one external GPU to serve driving, segmentation, side and rear inference, so
`inferenced` remains the sole hardware owner while `modeld` remains the sole
owner of driving-model state and output semantics.

## What upstream actually does

The relevant upstream files are:

- `selfdrive/modeld/modeld.py`: one `ModelState` contract for big and small
  models, big-model load/warmup timeout, preloaded small fallback, finite-output
  check and one-way runtime failover.
- `selfdrive/modeld/compile_modeld.py`: compiles ONNX preprocessing and model
  execution into serialized tinygrad JITs; verifies capture/replay and pickle
  round trips with deterministic randomized inputs.
- `selfdrive/modeld/SConscript`: builds device-specific small/big artifacts,
  places image warping on the on-device accelerator and queues model tensors on
  AMD, chunks large compiled artifacts, and serializes builds touching the USB GPU.
- `selfdrive/modeld/helpers.py`: selects compiled small/big artifacts and accepts
  the device only when VID:PID and firmware product string match.
- `system/hardware/chestnut/flash.py` and `hardwared.py`: validate and flash a
  versioned firmware image only while offroad, with bounded retries and recovery.
- `selfdrive/selfdrived/selfdrived.py`: blocks engagement while the big model is
  loading, applies a settling period, and soft-disables if an active big model fails.

Upstream's runtime sequence is:

1. Require both an exactly recognized flashed device and a compiled big-model artifact.
2. Mark the USB GPU as loading.
3. Load and warm the big model in a bounded background operation (60 seconds upstream).
4. Keep the small model constructed even when the big model succeeds.
5. Publish the normal openpilot model messages from either runner; consumers do
   not receive a second driving API.
6. On an exception, non-finite output, device disappearance or dead model stream,
   mark the big model failed, switch to the small model and do not switch back onroad.
7. If the failure occurs while engaged, request a soft disable and tell the driver
   that the small model remains available for a later engagement.

## EOP adaptation

### Selected model lineage

The two production roles intentionally use different artifacts:

| Role | Selected source | Artifact identity | Notes |
|---|---|---|---|
| external high-capacity model | current official openpilot Chestnut big model | `big_driving_supercombo.onnx`, LFS SHA-256 `a501760a9d1d5fef0eab2b8c5d122d06124fc26dc8e0782e0aa94b82a208f0ff`, 1,757,355,221 bytes at audited upstream commit | Compile with the upstream tinygrad/Chestnut pipeline after stable-tag compatibility is proven |
| local fallback | `../bukapilot` v10.0.5 supercombo | `supercombo.rknn`, SHA-256 `39155c9cf03b5fe8bfc2949192ef954fd8cd325ee6f1442db19db06335fb5e5a` | User-designated proven RKNN fallback; checked binary explicitly targets RK3588 |

Bukapilot commit `0c6977fc6970255b0eb09073c9c4951b8a7448d1` contains a
165,403,347-byte RK3588 `supercombo.rknn` produced with RKNN compiler 2.3.0.
Its source ONNX was previously tracked through LFS as SHA-256
`d21daa542227ecc5972da45df4e26f018ba113c0461f270e367d57e3ad89221a`
(51,461,700 bytes). The checked metadata SHA-256 is
`45d370eb8f7ed618f6e5c2592e5c1e471a99f65eed08977621b8e474d9520421`.

The Bukapilot graph has nine inputs—two 12-channel temporal YUV image tensors,
desire, traffic convention, lateral-control parameters, previous curvature,
navigation features, navigation instructions and a 99x512 feature buffer—and
one 6,504-float output. Its metadata slices the normal openpilot plan, lane-line,
road-edge, lead, desire, pose, curvature and hidden-state outputs. This makes it
a suitable local fallback behind the openpilot parser; it is not compatible
with the present one-input/one-output inference IPC without a runner-specific
multi-tensor path.

The RK3588 RKNN binary must not be reused on RK3576. Its embedded metadata says
`target_platform: rk3588`, and RKNN reports target-platform mismatch for an
incompatible binary. Materialize the exact Bukapilot source ONNX and create two
independently checked artifacts:

- `supercombo_bukapilot_rk3588.rknn`, target `rk3588`;
- `supercombo_bukapilot_rk3576.rknn`, target `rk3576`.

Record the ONNX hash, conversion script hash, rknn-toolkit version, quantization
settings, calibration dataset hash, target SoC and final RKNN hash for each.
RK3576 is not considered proven until replay parity and real-hardware timing pass.

### Same-model parity mode

Before attempting the 1.76 GB upstream big graph, compile the exact Bukapilot
source ONNX for tinygrad/eGPU and compare it against the SoC-specific RKNN
conversion on identical prepared inputs. This `egpu_parity` mode isolates backend,
preprocessing, transport and quantization differences while model semantics are
held constant.

The eventual `egpu_big` mode uses the official upstream big model. It cannot be
expected to numerically match the older Bukapilot fallback because it is a
different model generation. Compatibility is instead defined at the parsed
openpilot output/message contract, followed by replay behavior and safe-transition
tests. A big-to-local fallback while engaged still soft-disables; it does not
silently continue controlling through a model-generation discontinuity.

### One driving contract

`selfdrive/modeld` continues to own:

- camera-frame selection and synchronization;
- calibrated image transforms;
- desire, traffic-convention, action-delay, feature-history and previous-action state;
- output slicing and `Parser` processing;
- `modelV2`, `drivingModelData` and `cameraOdometry` publication.

Both executors must satisfy one internal openpilot driving-runner contract:

| Executor | Hardware | Purpose |
|---|---|---|
| external big runner | ASM2464 + AMD GPU via tinygrad | Optional higher-capacity model |
| local fallback runner | RK3588 RKNN NPU | Always-available small/openpilot model |

Both selected artifacts are monolithic supercombo graphs. EOP's current split
vision + temporal-policy implementation must not be treated as the final fallback
contract; port the Bukapilot monolithic RKNN runner behind the canonical parser.
The adapter boundary is the parsed openpilot output dictionary, not an Autoware
trajectory, AutoSteer lane vector or AutoDrive curvature tuple.

Temporal state must live above the hardware executor or be updated consistently
for both paths. The preloaded RKNN fallback must never resume with stale desire,
feature or previous-action history.

### Single hardware owner

Upstream says only `modeld` may access Chestnut. EOP preserves the important
invariant—exactly one process opens the USB GPU—but assigns that responsibility
to `inferenced` because side, rear and segmentation jobs also use it.

`modeld` submits critical driving work and retains all driving semantics.
`inferenced` owns device lifetime, compiled artifacts, serialization, deadlines
and health. No camera daemon may instantiate an AMD/tinygrad USB device directly.

### Local preprocessing and bounded USB traffic

Follow upstream's split between local warp and external model execution:

- perform decode, resize/warp, normalization and packing on RK3588 using the
  appropriate RGA/Mali/CPU path;
- upload the model-ready FP16 tensor, not a raw camera frame;
- retain it on the external GPU for model stages that share an identical input
  contract;
- never share a tensor merely because two models use the same camera when their
  color space, geometry, normalization or resolution differs.

The present cereal job messages copy one tensor in and one tensor out. That is
acceptable for detection shadow bring-up, but it is not the production driving
transport. Use a private versioned shared-memory/multi-tensor transport before
adding driving or high-rate segmentation. Do not change public cereal schemas
without explicit approval.

### Compiled artifacts, not dynamic ONNX for driving

The current EOP `EgpuBackend` dynamically constructs `OnnxRunner` and is suitable
only for early shadow compatibility work. Driving should follow upstream by:

- compiling and warming device-specific tinygrad JIT artifacts offroad;
- storing model hash, tinygrad commit/tag, firmware identifier, input/output
  metadata and camera-resolution compatibility with the artifact;
- testing deterministic compile/replay and serialization round trips;
- refusing activation if any part of that identity does not match;
- using an exclusive compile/device lock.

EOP remains pinned to official tinygrad `v0.13.0`. The audited upstream commit
pins tinygrad `138fb4a783d82f4e877ad2fe3692aaf8d1de2e46`, which is 948 commits
after v0.13.0 and is not the selected stable tag. Therefore the architecture may
be ported now, but EOP must not claim Chestnut compile/JIT compatibility until
the v0.13.0 path is tested. Do not float to upstream tinygrad `master`; evaluate
the next official release tag separately.

## Failover state machine

```text
BOOT / OFFROAD
  ├─ no exact firmware, no compiled artifact, or load/warmup fails
  │    └─ warm RKNN → RKNN_ACTIVE
  └─ exact eGPU + compiled artifact
       ├─ warm RKNN fallback
       ├─ load and warm eGPU within deadline
       └─ eGPU_ACTIVE

eGPU_ACTIVE
  ├─ valid finite output before deadline → remain active
  └─ exception / timeout / non-finite / hot-unplug / stale stream
       ├─ discard failed frame
       ├─ atomically mark eGPU failed
       ├─ switch to already-warm RKNN
       ├─ soft-disable if controls are engaged
       └─ no onroad eGPU retry; retry after offroad/ignition restart
```

Activation must fail closed. A missing Params key, stale health value, partial
artifact or unrecognized product string means RKNN, never eGPU.

## Other camera workloads and fallback

Segmentation is the main expandable eGPU workload, with detection also running
for side and rear. These paths remain independently schedulable:

| Workload | External path | On-device fallback | Failure authority |
|---|---|---|---|
| openpilot driving | official upstream big model compiled for tinygrad; Bukapilot ONNX first for same-model parity | preloaded/warm, SoC-specific Bukapilot supercombo RKNN | soft-disable if active external model fails; continue later on RKNN |
| front segmentation | eGPU SceneSeg/PP-LiteSeg | existing RKNN segmentation | keep local mask; never disturb driving |
| side detection + segmentation | independent side eGPU sessions | Hailo when present, then compact side RKNN | advisory unavailable/degraded if all fail |
| rear detection + segmentation | independent rear eGPU sessions | Hailo when present, then compact rear RKNN | advisory unavailable/degraded if all fail |

Side and rear do not share model IDs, artifacts, class maps, postprocessing,
deadlines or health. A side failure must not evict rear work, and neither camera
failure may trigger the driving-model fallback state machine.

## Scheduler policy

The external GPU worker is single-owner and non-preemptive initially. Admission
must reserve the driving deadline before accepting optional work:

1. External driving model, when explicitly activated and validated.
2. Side/rear inference with safety-advisory deadlines.
3. Front/side/rear segmentation shadow or promoted jobs.
4. Autoware compatibility experiments and logging workloads.

If the measured remaining USB and compute budget cannot meet a job's deadline,
reject it before upload and run its local fallback. Queue priority alone is not
enough; admission must account for transfer time, queued execution and frame age.

## Required validation before implementation is promoted

- Golden-route parity of small RKNN output before and after introducing the runner interface.
- Exact-input parity between Bukapilot source ONNX on eGPU and each SoC-specific
  Bukapilot RKNN artifact, with tolerances justified for conversion/quantization.
- Separate RK3588 and RK3576 artifact inspection; reject target-platform mismatch.
- Big-eGPU versus expected openpilot output-shape, units, parser and message tests.
- Forced load timeout, inference exception, non-finite output, deadline miss,
  USB reset and hot-unplug tests.
- Proof that RKNN is loaded, warmed and temporally current before eGPU activation.
- Proof that engaged failure produces one soft-disable and never oscillates back to eGPU.
- Offroad firmware backup, image validation, bounded retry and ROM-recovery tests
  before adopting automatic flashing.
- Sustained USB 3.0 Gen1, thermal and multi-workload soak tests on RK3588 hardware.
- Replay/HIL/closed-course gates before any external driving result gains authority.

## Upstream references

- [openpilot 0.11.2 release notes](https://github.com/commaai/openpilot/blob/master/RELEASES.md)
- [upstream modeld Chestnut selection and failover](https://github.com/commaai/openpilot/blob/master/openpilot/selfdrive/modeld/modeld.py)
- [upstream tinygrad compile/build rules](https://github.com/commaai/openpilot/blob/master/openpilot/selfdrive/modeld/SConscript)
- [upstream model helper and exact-device checks](https://github.com/commaai/openpilot/blob/master/openpilot/selfdrive/modeld/helpers.py)
- [upstream hardware flashing lifecycle](https://github.com/commaai/openpilot/blob/master/openpilot/system/hardware/hardwared.py)
- [upstream control-state failure handling](https://github.com/commaai/openpilot/blob/master/openpilot/selfdrive/selfdrived/selfdrived.py)
