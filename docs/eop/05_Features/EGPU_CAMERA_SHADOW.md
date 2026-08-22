# USB eGPU camera shadow validation

## Scope

Openpilot has independent optional eGPU inference workloads. The implemented
first step is side/rear detection shadowing; semantic segmentation is the main
planned expansion because it can improve drivable-area, curb, obstacle-boundary
and scene understanding across camera views.

| Owner | Cameras | Model ID | Artifact | Parameter |
|---|---|---|---|---|
| `sided` | `side_left`, `side_right` | `side_yolo_egpu` | `models/onnx/yolo_side.onnx` | `EOPSideEGPUMode` |
| `reard` | `rear` | `rear_yolo_egpu` | `models/onnx/yolo_rear.onnx` | `EOPRearEGPUMode` |

These are separate model sessions and validation streams. Left and right share
one side model because they use the same camera class and mirrored mounting;
the rear camera does not share that model or its scheduling state.

The corner radars are owned by `../visionpilot`. They do not enter openpilot's
eGPU camera path and are not part of these model inputs.

Planned sessions remain separate even when they reuse preprocessing code:

| Camera owner | Detection | Segmentation | Intended use |
|---|---|---|---|
| `modeld`/`monod` front views | existing openpilot models | SceneSeg/PP-LiteSeg eGPU shadow | road, curb, dynamic-scene and obstacle boundaries |
| `sided` | `side_yolo_egpu` | planned `side_seg_egpu` | adjacent-lane, curb and blind-zone context |
| `reard` | `rear_yolo_egpu` | planned `rear_seg_egpu` | rear drivable space and cross-traffic context |

Side and rear must have independent artifacts, class maps, postprocessing,
latency accounting and health state. They are both inference pipelines; neither
is merely a frame relay and neither is folded into a generic rear/side result.

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

## Driving-model boundary

Production driving inference follows openpilot, not the Autoware demonstration
model contracts. `selfdrive/modeld` remains canonical for:

- the vision model and temporal policy model split;
- YUV/current-and-previous-frame preparation;
- desire history, traffic convention, lateral-control parameters, previous
  curvature and feature-history buffers;
- output slicing, `parse_vision_outputs`/`parse_policy_outputs`, and `modelV2`.

A future eGPU implementation may provide an openpilot-compatible model runner,
but it must preserve those inputs, temporal state, output semantics and deadline
behavior. It must not create a second driving/planning interface alongside
`modeld`.

## Autoware ONNX compatibility references

`../autoware_vision_pilot` currently contains three FP32/INT8 model pairs. They
remain useful compatibility and benchmark references. They are lower priority
than segmentation and side/rear inference, are not the production driving-model
direction, and must not be treated as control-authoritative outputs.

| Candidate | Input | Outputs | Present IPC compatibility |
|---|---|---|---|
| AutoSpeed | one FP32 NCHW 512x1024 image | one `[N,8,10752]` tensor | Fits the one-input/one-output transport |
| AutoSteer | one FP32 NCHW 512x1024 image | lane vector and height | Blocked by one-output transport |
| AutoDrive | previous and current FP32 NCHW 512x1024 BEV images | distance, curvature and flag logit | Blocked by one-input/one-output transport |

All operator types used by the six graphs are implemented by tinygrad v0.13.
The three INT8 graphs also parsed and executed end to end on the development CPU
with finite, correctly shaped outputs. That is a graph-compatibility check only:
it does not establish eGPU speed, numerical agreement, calibration quality or
fitness for trajectory planning.

AutoDrive's input is not an ordinary resized camera frame. The source pipeline
warps two consecutive frames into BEV and applies ImageNet normalization.
AutoSteer and AutoSpeed use a resized 0..1 image. These contracts must remain
model-specific even when preprocessing buffers can be shared.

The current cereal request carries one tensor and the result returns only one
output. A private versioned multi-tensor transport (or shared memory) may still
be useful for segmentation graphs and an openpilot-compatible model runner. It
should be justified by those canonical workloads, not added solely for the
Autoware demonstrations. Any public cereal schema change requires approval.

## Scheduling and USB budget

"Parallel models" means concurrent submissions to one owner, not multiple
processes opening independent GPU contexts. The first scheduler should execute
one eGPU job at a time with deadline and priority ordering:

1. Preserve canonical openpilot driving-model deadlines on its assigned backend.
2. Side/rear detection and segmentation safety-advisory deadlines.
3. Front road/wide segmentation shadow comparisons.
4. VisionPilot/Autoware reference experiments at a lower, rate-limited priority.

One FP16 512x1024 RGB tensor is about 3.15 MB. At 10 Hz it consumes about
31.5 MB/s before outputs and protocol overhead. Each segmentation view and each
side/rear inference pass must be included in the budget. Share a preprocessed
tensor only when input contracts are identical; never duplicate a full USB upload
just because two models read the same camera. USB 3.0 Gen1 admission must use
measured sustained bandwidth and frame-age deadlines, not the nominal link rate.

The current `inferenced` worker provides single ownership but its queue does not
yet sort by priority. Correct priority/deadline scheduling is a prerequisite for
enabling these additional models together.

Tinygrad v0.13 requires Python 3.11 or newer; EOP's deployed `.venv` is Python
3.12. System Python 3.10 is not a supported launcher for this path.
