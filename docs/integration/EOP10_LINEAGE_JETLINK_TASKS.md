# NGP10 baseline, JetLink v3 and EOP10 stereo task ledger

Updated: 2026-10-18. Current work: lineage detection and implementation planning.
Runtime migration has not started. Keep this ledger current with subsequent
SSH-signed commits; implement shared behavior on NGP10 before descendants.

Latest status: owner selected the reviewed linear rebase. Candidate replay is
complete; all 19 remaining tree differences were reconciled to saved EOP tip
fe47dc146a2d6c225697ef9a558f9e53ea6454e1, including merge-only fixes and docs.
Before these documentation updates, the candidate index matched that entire
saved tree exactly. See [resolution log](EOP10_REBASE_RESOLUTIONS.md).
No Chestnut/eGPU driving-model path or setting is retained. Onboard Mali GPU
compute remains required for stereo; optional generic external compute is a
separate future capability, not a restoration of that driving-model path.

## Owner decisions for this work

- Adopt upstream JetLink wire protocol v3 under `nagaspilot/jetlink`, starting
  on NGP10. EOP10 inherits that integration rather than independently forking it.
- PLP remains the separate companion protocol and runs alongside JetLink.
- PLP's full service contract starts on NGP10 over wired USB. EOP10 adds
  Bluetooth transport for those same services. USB must offer at least the
  Bluetooth feature set, with richer payloads or higher rates where supported;
  negotiate limits by transport, not Bluetooth-only service implementations.
- NavPilot must support both links and USB/Bluetooth service parity. A separate
  agent is updating its task/design documentation while preserving pre-existing
  uncommitted app changes; native transport implementation remains open.
- Exopilot is the HAL/device-layer source of truth. Audit its camera, compute,
  USB and Bluetooth contracts before implementing platform-specific adapters.
- NGP10/EOP10 own inference selection and fallback. USB timeout/disconnect,
  invalid replies or remote inference failure must return modeld to local
  vision/policy inference, retaining normal published outputs. The phone does
  not decide vehicle control authority.
- EOP10 adds GPU stereo depth, geometric point-cloud mapping and semantic fusion.
  Sustained stereo compute must not silently fall back to the ADAS CPU.
- Finish the lineage/protocol plan, then return to stereod point-cloud work.
- Update Markdown task documents along the way, sign commits and push with the
  SSH key, and keep EOP10 based on the reviewed NGP10 baseline.

## Verified branch lineage

Fetched `dev/NGP10` and `dev/EOP10` directly from exo-elec/openpilot over SSH.
At inspection NGP10 is `314d96386c6756dc173ace6c47db3fc535b7b217` and
EOP10 is `d3a740f7fefd78209621ba2201ccb97708de36f9`.
The merge base equals the NGP10 tip; left/right counts are **0 / 89**.
There are no NGP10-only commits to integrate at this snapshot. Rebase onto
this exact parent is therefore already satisfied; do not force-push or replay
the entire EOP history to manufacture another rebase.

Execution note: a plain `git rebase dev/NGP10` nevertheless attempted to flatten
the merged EOP history (67 replay steps) and conflicted at the first old README
commit, 3196f8285. It was aborted successfully, restoring 4caf1784f. The earlier
expectation that ancestry would make this command a no-op was incorrect. No
history rewrite was retained. Future baseline updates require an explicit
merge-preserving strategy and isolated validation, not a blind plain rebase.

Owner follow-up requests a review of conflicts and a choice of linkage/rebase
strategy rather than stopping at the aborted trial. See
[concrete conflict review and choices](EOP10_REBASE_CONFLICT_REVIEW.md).
Its candidate-based linear-rebase recommendation supersedes the earlier
assumption that no ancestry cleanup could be useful. The owner subsequently
selected that recommended strategy. The isolated replay is complete, with
saved-tree parity checked before new documentation updates.

The trees differ in 1,739 paths (including docs, dependencies, hardware and
legacy removals). Those paths are not all stereo features or shared policy.
Do not restore entire modeld/control files from one branch onto the other.

`python3 nagaspilot/lineage.py --parent dev/NGP10 --child HEAD` checks only
Python source in controls/runtime/mapd. It finds 95 parent files, 94 identical,
one changed file, and seven allowed child additions. It does not audit modeld,
controlsd, schemas or the full hardware pipeline.

| Classification | Evidence | Action |
| --- | --- | --- |
| Shared portable baseline | 94 matching protected files; mapd source matches | Keep byte-identical; shared policy changes originate on NGP10 |
| Explicit platform adapter | runtime/bigmodel_platform.py differs in device detection and USB platform (`agnos` vs `static`); its own docstring permits branch rewriting | Resolve guard vs adapter contract on the baseline; do not copy Qualcomm detection onto Rockchip just to pass |
| Allowed EOP additions | controls/eop_curvature.py, eop_lane_change.py, eop_profile.py, eop_soc.py; runtime/eop_runtime_policy.py, eop_speed_limit_resolver.py, eop_utils.py | Review input and output contracts; presence alone does not prove behavior parity |
| Shared inference interface, different execution backend | NGP modeld uses TinygradModel; EOP modeld has RKNN adaptation; both publish modelV2/drivingModelData/cameraOdometry | Preserve outputs and fallback semantics with platform-specific execution |
| Shared monocular perception/planning | NGP process registry has modeld, runtime.monod, runtime.gridd and runtime.pathd | NGP already has a monocular loop; earlier docs saying pathd/gridd begin only on EOP are stale |
| EOP stereo addition | No tracked selfdrive/stereod or daemons/stereod subtree on NGP tip; EOP manager registers stereod and richer gridd/segd | Stereo and extra sensor acquisition stay on EOP |
| Mixed shared/hardware integration | modeld differs by 606 added/348 removed lines; controlsd 324/120; longitudinal_planner 491/212 | Compare behavioral slices, not whole-file replacement |

Some shared runtime docstrings describe older tracker ownership. The live NGP
manager registers runtime.gridd for tracking monod detections; use actual
registrations and call paths rather than assuming all prose is current.

## NavPilot and upstream JetLink reconciliation

Inspected `../navpilot`, branch master at `252e033`, and `../jetlink`, the
exo-elec fork at `a34ad5f3c38546d0915b112939f51a8cb4f57b18`.
Upstream zoompilot/jetlink main resolved remotely to
`491cf1ef8d6786ed51cf995e18fc8721389066c6`; the local fork is a separate
revision. Pin a reviewed upstream revision, not a mutable main branch.

Current Python `nagaspilot/bigmodel/protocol.py` and NavPilot
`packages/npbm/lib/src/protocol.dart` both use NPBM v1, 20-byte headers.
The NGP/EOP bigmodel and PLP package trees currently match between branches.
There is no tracked `nagaspilot/jetlink` integration in EOP at inspection.
NavPilot's BIGMODEL_WIRED_PLAN explicitly superseded the earlier v3 plan with
NPBM on 2026-10-12. BIGMODEL_LAYERS still borrows JetLink controller concepts,
but directs replacing its wire format with NPBM. **The owner decisions above
supersede that NPBM direction for future work; existing code is not yet v3.**

Upstream source confirms JLNK magic, wire version 3 and a 32-byte header.
Its engine negotiation, upload and inference messages differ from NPBM, so
renaming files or changing the version byte cannot provide compatibility.
The application controller command/event API and the inference wire are
distinct contracts. Preserve upstream wire behavior while adapting the
NavPilot native controller bridge and local modeld adapter.

A sparse upstream checkout at the exact main SHA above also confirms adapter
API 3 (`jetlink/openpilot/__init__.py`), separate from wire protocol v3.
`interface.py` supplies `ModelSide.in_control()`; joining.py asks it on the
frame thread before promotion. model_state.py limits held remote frames and
joining.py contains demotion/reset handling. Preserve protocol compatibility
while implementing and testing the owner-selected local fallback/re-entry
policy in NGP's adapter; do not assume upstream's default promotion policy is
identical to NGP's existing one-way policy.

Primary upstream references:

- [Protocol source](https://github.com/zoompilot/jetlink/blob/491cf1ef8d6786ed51cf995e18fc8721389066c6/jetlink/protocol.py)
- [Link and USB design](https://github.com/zoompilot/jetlink/blob/491cf1ef8d6786ed51cf995e18fc8721389066c6/docs/link-protocol.md)
- [Openpilot integration](https://github.com/zoompilot/jetlink/tree/491cf1ef8d6786ed51cf995e18fc8721389066c6/jetlink/openpilot)

Local design evidence:

- NavPilot docs/architecture/BIGMODEL_WIRED_PLAN.md and BIGMODEL_LAYERS.md.
- NavPilot docs/architecture/PLP_V5_SPEC.md and packages/npbm protocol source.
- Openpilot nagaspilot/runtime/bigmodel.py and bigmodel/remote_state.py.

## Fallback and parallel-link contracts to implement

Modeld remains the producer of modelV2, drivingModelData and cameraOdometry
through both local and remote operation. Keep a validated local model loaded;
determine how its recurrent history stays ready and measure transition delay
on each backend. Merely keeping weights resident does not establish a valid
first local output. Never use stale remote output indefinitely as fallback.

The shared NGP policy decides deadline/missed-frame limits, invalid/non-finite
output handling, failed-engine handling, local warm-up, state reset and whether
remote re-entry is permitted. Upstream timeout mechanics are a starting point,
not permission for the phone to change selection. EOP supplies an RKNN local
model adapter with equivalent published contracts. If local inference itself
fails, normal model/control validity handling applies; no fabricated output.

Use one owner of the physical USB gadget. iPhone JetLink TCP and PLP TCP can
share the cable network on distinct ports; upstream uses 5599 for the JetLink
cable endpoint while current NPBM uses 5600 and PLP proposes 5601. Validate
firewall/binding and control-port roles before changing them. Android PLP
needs a separately identified interface/endpoint contract alongside JetLink;
the existing PLP draft says that second interface is not built. Test descriptor
discovery because a second vendor interface can become ambiguous. Never mix
PLP frames into JetLink inference messages or run competing gadget owners.

## Task order and acceptance

| State | Task | Completion evidence |
| --- | --- | --- |
| Done | Fetch and detect lineage | Exact refs, merge base, counts and protected-file comparison recorded above |
| Done | Reconcile NavPilot design with owner v3 choice | NPBM incompatibility and superseded design direction explicit |
| Open | Baseline lineage guard/adapter boundary | NGP-defined explicit adapter contract; regression test rejects actual shared-policy drift |
| Open | Review/pin upstream implementation | Exact upstream/fork diff and adapter API reviewed; MIT notices retained |
| Open | Introduce nagaspilot/jetlink on NGP10 | Protocol conformance fixtures against pinned upstream; no NPBM bytes on v3 link |
| Open | NGP-owned local fallback | Timeout, unplug, corrupt reply, non-finite output, engine failure and reconnect tests preserve valid local model outputs |
| Open | NavPilot native v3 bridge and PLP coexistence | Matching iOS/Android native wire; independent PLP traffic; one gadget owner; frame-latency measurements |
| Open | Carry reviewed baseline into EOP10 | Fresh lineage check, adapter parity, RKNN fallback fixtures; rebase only when parent advances |
| Done | Reviewed linear EOP rebase | Published a5c813933 with explicit lease; before subsequent runtime work, only two task/resolution docs differed from saved EOP tip |
| Partial | NavPilot reconciliation and PLP TCP | 572a481 on NavPilot master; existing work reused, real 5601 PLP transport and six loopback cases; all 25 PLP tests pass, app/provider and native JetLink work remain |
| Partial | Exopilot GPU contract/readiness | aae97cf on codex/gpu-stereo-readiness; GPU enumeration and kernel smoke diagnostic, seven host tests pass; target-board kernel/thermal tests remain |
| Partial | Metric stereo projection reference | Complete Q, true strided sampling and invalid-depth rejection tested in isolation; eight geometry cases plus lineage unit case pass. Live integration awaits ground/obstacle separation and measured calibration |
| Next | Resume EOP stereod point-cloud implementation | Follow metric-depth and geometric mapping slices in the stereo plan; preserve baseline control outputs |

The JetLink work is a shared-baseline dependency track. EOP stereo analysis
and isolated geometry work may proceed without waiting for a phone integration;
it must consume modeld's established outputs in both local and remote modes.

Validation so far: required ./test.sh stops at the existing protected-file
mismatch in runtime/bigmodel_platform.py. This is a lineage failure, not a
timestamp failure. No claim of hardware, road, v3 or seamless-fallback validation.


### Stereo GPU follow-up, 2026-10-19

Origin fetched first: NGP10 4728d8add and EOP10 222a8edba matched local tips.
Shared NGP GPU source remains byte-identical in EOP. New EOP recovery and
point-cloud work follows pinned ROS image_geometry/OpenCV full-Q geometry;
custom OpenCL kernels remain target-unverified, not claimed as Autoware code.
Production GPU loss drops depth/cloud output and degrades stereo fusion without
CPU fallback. Optional stereo loss no longer causes immediate disengagement;
core model/inference fault handling remains. See
`docs/eop/STEREO_POINTCLOUD_UPSTREAM_CONTRACT.md` for exact pins, conventions and
unfinished calibration/frame/mapping contracts. Host tests pass; target GPU
and full-gate acceptance remain pending.
