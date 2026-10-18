# Compute fault policy: CPU/RKNN critical, optional accelerators degraded

Owner decision: core CPU ADAS execution and the Rockchip RKNN safety NPU retain
compute-related disengagement authority. GPU stereo/point-cloud, grid fusion,
RGA, MPP and camera-tier cards degrade their capabilities when unavailable.
Missing optional evidence does not become CPU fallback, free space or fresh
stereo confidence. This policy covers compute component faults; normal vehicle,
driver, communication, valid-model/control-output and platform health guards
continue to apply.

| Fault | Response |
| --- | --- |
| Core CPU task deadline/process/health failure | Existing critical CPU/control health and validity guards |
| RKNN unavailable, released or unhealthy | inferenceFault: immediate disable and no entry |
| RKNN mono inference fault | Existing monoFault immediate disable and no entry |
| GPU/stereo loss | Stereo degradation alert; drop geometry and reduce stereo evidence |
| Grid fusion exception | Grid degradation alert; affected source unavailable |
| RGA unavailable | Acceleration degradation alert |
| MPP or point-cloud recording loss | Recording unavailable alert |
| Camera-tier Hailo/DX-M1 segmentation loss | Semantic source degradation; no substitute core-inference fault |

Implementation:

- `nagaspilot/manager/compute_fault_policy.py` defines the critical inference
  classifier. A generic `gpu_failed` or card failure does not become an
  inference disengagement when RKNN remains available. Core CPU failure reasons
  and RKNN failure reasons are explicit; absence of RKNN cannot be hidden by a
  legacy false aggregate-fault bit.
- InferenceD publishes live RKNN readiness and backend-health state. GPU/card
  presence does not mask RKNN loss. A failed RKNN health probe is treated as
  unhealthy, not silently ignored.
- On ARM/Rockchip, missing RKNNLite fails backend initialization. Offline x86
  mock support is retained for development and never used as target readiness.
- `eop_events.py` gives optional stereo/grid/RGA faults permanent degradation
  alerts without disable/no-entry event types. Core inference/mono alerts and
  CPU/control health guards retain their prior disabling authority.
- Selfdrived consumes the explicit critical-inference classifier. Optional
  status channels remain excluded from aggregate communication-failure checks.

Validation: nine focused policy/producer/runtime cases and eleven existing
stereo point-cloud/degradation cases pass. Focused ruff passes. The full test.sh
still stops at the previously recorded bigmodel_platform.py lineage mismatch.
Target fault injection, compute-process termination and driving/replay
acceptance remain pending; host fixtures do not establish hardware readiness.
