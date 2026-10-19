# Shared GPU compute: NGP10 baseline and EOP10 stereo

Updated 2026-10-19.

NGP10 owns `common/gpu_compute.py`: lazy optional OpenCL loading, GPU-only device
selection, an in-order process-local queue, portable CL1.2 compilation, bounded
working memory and explicit kernel/readback smoke validation. CPU OpenCL is
never substituted. Existing modeld, controls, protocol and fallback policy are
unchanged. Exopilot owns BSP/kernel/libmali/ICD installation and diagnostics.

EOP10 inherits this exact shared module and adds the InferenceClient adapter and
StereoD kernels. Do not add stereo daemons or board libraries to NGP10 merely to
provide the optional GPU context. Missing Python bindings, ICD, GPU or kernel
execution must report unavailable; presence of libOpenCL alone proves nothing.

| Status | Task |
| --- | --- |
| Implemented | Shared context, allocation limits and optional real kernel smoke |
| Host tested | GPU-only selection, bad driver, allocation limits and lifecycle |
| Implemented, host tested | HAL-owned four-path census SGM, confidence/LR checks, invalid-output rejection |
| Implemented, host tested | OpenCL rectification, complete-Q projection and measured mounting; calibration identity on the wire |
| Implemented, host tested | GPU ground/obstacle endpoints on the BEV grid; stationary decay, obstacle precedence, source degradation |
| Target pending | Exopilot `python -m hal.gpu --smoke-gpu`, then shared smoke on RK3588 and RK3576 |
| Target pending | Stereo accuracy, CPU overhead, p95/p99 age, memory, concurrent ADAS/UI and sustained thermals |
| Later, explicit limitation | Pose-compensated temporal mapping, connected drivable corridor, synchronized localization. Moving/unknown-pose history is cleared; current-frame stereo still maps. No CPU substitute. |

Python dependency: `pyopencl` and NumPy in the target interpreter, plus the matching
OpenCL ICD loader and vendor runtime from Exopilot. Do not install G610 binaries
on RK3576 or mix GBM/Wayland alternatives. No driver or package is installed by
this module. Diagnostic:

```python
from openpilot.common.gpu_compute import GPUCompute
with_context = GPUCompute()
try:
    with_context.smoke_test()
finally:
    with_context.close()
```

A completed operation can be discarded after a deadline; OpenCL does not provide
portable preemption for a hung driver. Kernel execution time requires measurement,
and daemon/process supervision is still necessary. Host policy tests do not
establish target-board readiness or driving acceptance.
