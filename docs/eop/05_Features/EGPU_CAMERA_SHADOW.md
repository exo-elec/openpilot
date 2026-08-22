# USB eGPU camera shadow validation

## Scope

Openpilot has two independent optional eGPU camera workloads:

| Owner | Cameras | Model ID | Artifact | Parameter |
|---|---|---|---|---|
| `sided` | `side_left`, `side_right` | `side_yolo_egpu` | `models/onnx/yolo_side.onnx` | `EOPSideEGPUMode` |
| `reard` | `rear` | `rear_yolo_egpu` | `models/onnx/yolo_rear.onnx` | `EOPRearEGPUMode` |

These are separate model sessions and validation streams. Left and right share
one side model because they use the same camera class and mirrored mounting;
the rear camera does not share that model or its scheduling state.

The corner radars are owned by `../visionpilot`. They do not enter openpilot's
eGPU camera path and are not part of these model inputs.

## Safety state

Only `off` and `shadow` modes are accepted. Both default to `off`.

In shadow mode:

- Hailo/local detections remain authoritative and continue through existing tracking and publication.
- eGPU results are compared by class and bounding-box IoU and logged every 50 successful results.
- Invalid output, unavailable hardware, timeout, or USB failure cannot replace or suppress the authoritative result.
- A bounded exponential backoff prevents repeated failures from consuming the camera loop.

There is intentionally no `primary` mode yet.

## Runtime and ownership

`inferenced` is the sole eGPU owner. `sided` and `reard` submit through IPC with
direct HAL fallback disabled, so they cannot independently open the USB GPU.
Each camera daemon keeps at most one shadow request in flight. `sided`
alternates between available left and right frames; `reard` uses its own queue.

The ONNX input is resized to 640x640, RGB, NCHW, normalized, and transported as
FP16. The eGPU backend casts it in VRAM to the model-declared dtype. One tensor
is 2,457,600 bytes, half the FP32 transfer size.

Tinygrad is pinned as the official `v0.13.0` submodule release. Do not float the
submodule on `master`; certify later releases per model and GPU.

## Model preparation

`models/download_models.sh dev-pc` exports the generic YOLOv8n validation model
and creates distinct `yolo_side.onnx` and `yolo_rear.onnx` artifacts. They begin
with identical weights only to validate the hardware and data paths. Replace
and checksum them independently when viewpoint-specific datasets are ready.

Enable one path at a time for initial bench work:

```bash
params put EOPSideEGPUMode shadow
params put EOPRearEGPUMode off
```

Then validate rear independently before enabling both.

## Promotion prerequisites

Before adding a primary mode, collect per-camera replay metrics for precision,
recall, false negatives, IoU, p50/p95/p99 latency, missed deadlines, frame age,
hot-unplug recovery, night/rain/glare performance, and calibrated distance
error. Promotion also requires hardware soak testing and a defined hot local
fallback. Public message-schema changes require explicit approval.

