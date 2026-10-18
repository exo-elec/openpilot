# StereoD: GPU stereo depth

StereoD publishes pixel disparity and normalized confidence through stereoDepth,
and health through stereoStatus. Matching requires a HAL-owned OpenCL GPU; it
never switches to CPU matching when GPU setup or execution fails. Object
detection remains in monod/sided/reard, and camera-card segmentation in segd.

NGP10 provides the optional shared `common/gpu_compute.py` context. EOP10 adds
`InferenceClient.gpu()`, `system/inferenced/opencl_stereo.py` and its CL1.2
kernels. Exopilot owns GPU drivers, the ICD loader and factory camera calibration.
No library-presence check, NPU sample or display demo establishes GPU readiness.

The GPU backend implements 5x5 census/Hamming cost, four independent horizontal
and vertical SGM paths with P1/P2 penalties, subpixel selection, optional median
filtering and left/right consistency. Output is float32 disparity in pixels,
confidence in [0,1], with rejected samples zeroed. Its 128 MiB working-set cap
rejects oversized input before device allocation. It does not silently resize
images or change calibration. Accuracy and timing are unverified on target.

Input requirements:

- Paired rectified grayscale views of equal dimensions. StereoD extracts NV12
  luma using the actual stride and applies measured factory rectification maps.
- Maps must match the capture resolution; Q and translation must agree on metric
  scale and forward-positive depth. Census windows crossing map holes are invalid.
- Nonzero monotonic capture times: at most 5 ms skew, 150 ms input age and no
  duplicate/old pair. Published depth retains the source frame ID and capture time.
- Completed GPU work exceeding 50 ms or output older than 200 ms is discarded.
  These are provisional engineering limits, not measured hardware guarantees.

A failed initialization retries at most once every two seconds. Device loss
releases the matcher/context; three consecutive compute failures also reset it.
Faults clear only after usable matching output. Status still publishes through
missing/invalid inputs and retry backoff. OpenCL offers no portable way to
preempt a hung driver; process supervision and hardware stress tests remain needed.
Rectification and optional reference Q reprojection currently run on CPU.

`SGM(target="cpu")` and `DepthPipeline(use_gpu=False)` explicitly select the older
NumPy offline reference. They must not be used as production fallback. The
DepthPipeline helper uses complete Q for its optional CPU numerical projection;
it does not implement the live geometric occupancy or drivable-area pipeline.

## Verification

Host checks, in an interpreter with NumPy and OpenCV:

```sh
python nagaspilot/daemons/stereod/test_sgm.py
python nagaspilot/daemons/stereod/test_stereo_runtime.py
clang -x cl -cl-std=CL1.2 -fsyntax-only system/inferenced/kernels/stereo_sgm.cl
```

The host runtime suite skips three explicitly gated GPU cases, including full-Q
point-cloud parity with ROS/OpenCV geometry. Require them on
the target (missing GPU or failed execution is an error):

```sh
python -m hal.gpu --smoke-gpu
python nagaspilot/daemons/stereod/test_stereod_integration.py
```

Record real-camera depth error, false-free/obstacle recall, frame age, GPU memory,
CPU overhead, concurrent ADAS/UI latency and sustained thermals before acceptance.
Known-distance and calibration fixtures must precede live grid integration.
See `docs/eop/STEREO_PIPELINE_INTEGRATION_PLAN.md` for remaining mapping tasks.

Optional stereo GPU loss reports degraded depth; it does not by itself disable
core ADAS. See `docs/eop/STEREO_POINTCLOUD_UPSTREAM_CONTRACT.md` for pinned
upstream geometry, the no-CPU-fallback policy and remaining mapping gaps.
