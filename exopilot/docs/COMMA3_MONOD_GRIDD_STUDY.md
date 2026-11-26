# Comma 3 MonoD and GridD port study

## Decision

Do not port EOP10 `monod` or `gridd` as daemons. They target RK3576/RK3588,
RKNN, RGA, ExoPilot HAL geometry, and a calibrated same-FOV stereo pair. The
original comma 3 instead provides Qualcomm/Tinygrad and two road cameras with
different optics: 1.71 mm wide (`ecam`, about 567 px focal length) and 8.0 mm
narrow (`fcam`, about 2648 px). EOP10's 80 mm baseline describes its dedicated
3.6 mm stereo pair and is not evidence of the comma 3 wide/narrow baseline.

The comma 3 driving model deliberately consumes both road-camera images, but
its calibration warps do not expose stereo disparity or metric object depth.
ExoPilot must not manufacture a stereo `Q` matrix or copy EOP10's 80 mm
default.

## Reuse assessment

| Source | Treatment | Reason |
|---|---|---|
| Tinygrad `examples/yolov8.py` | Adapt architecture and post-processing | Native Tinygrad reference; Tinygrad is MIT licensed |
| `modeld` VisionIPC/Tinygrad preprocessing | Reuse patterns, not its process | Correct zero-copy NV12 handling, camera SOF metadata, QCOM execution, and calibrated warps |
| EOP10 camera synchronizer | Rewrite | It timestamps after receipt instead of using `timestampSof` |
| EOP10 `monod` fusion | Reject | Wide camera performs no YOLO detection; absent metric depth defaults to 50 m |
| EOP10 `calibration_fusion.py` | Reject | RK HAL dependency, placeholder intrinsics, undefined state, and invalid 4x4 live-calibration assumption |
| EOP10 `gridd` lazy reprojection | Reject for comma 3 | Requires calibrated disparity and a stereo `Q` matrix |
| EOP10 probabilistic grid/filter concepts | Consider later | Useful only after validated metric observations exist; SOC does not need a dense grid initially |

## Minimal comma 3 design

Implement a ExoPilot-owned, default-off, **log-only** `monod` first:

1. Consume `VISION_STREAM_WIDE_ROAD` independently at 5 Hz. Preserve frame ID,
   `timestampSof`, source camera, and transform metadata.
2. Run only a nano detector at 320x320 using this branch's pinned Tinygrad/QCOM
   stack. Detect vehicle classes; never block or share the `modeld` process.
3. Publish 2D boxes, class probabilities, and inference health. Do not publish a
   metric range merely from class or box size.
4. Estimate a provisional ground contact only from the box bottom-center ray,
   the camera's own intrinsics, `liveCalibration.rpyCalib`, and calibrated camera
   height. Reject horizon, truncated, non-ground-contact, stale, or
   high-uncertainty observations.
5. Use `modelV2` lane lines only to assign an observation to left/right adjacent
   lane. Feed accepted metric observations into the existing non-actuating
   `AdjacentVehicleTracker` with explicit covariance.

The narrow camera may later classify distant objects independently and
cross-check track identity after per-camera projection is validated. It must not
be treated as a disparity partner without measured relative pose, per-device
stereo calibration, rectification, synchronization, and an error study. A dense
GridD/BEV should remain out of scope until the sparse detector has recorded-route
accuracy, latency, thermal, and calibration evidence.

## Activation gates

- benchmark with driver camera both enabled and disabled;
- no `modeld` latency or dropped-frame regression;
- validate detector weights, redistribution license, and immutable hash;
- compare projected positions with manually synchronized video/CAN evidence;
- measure longitudinal/lateral error by range and camera region;
- shadow-log complete pass lifecycles before any SOC steering connection;
- fail closed: missing camera, calibration, inference, or confidence means zero
  camera-derived offset.

Until these gates pass, BYD BSM remains the only live SOC trigger and the
camera tracker remains non-actuating.

## Status on `dev/EDP10`

No `monod` daemon, `ngpMonoDetections`/`ngpMonoStatus` messages, or `ngp_monod`
param exist on `dev/EDP10`; MonoD/GridD work belongs to `dev/EOP10` and its
descendants (`dev/01M`, `dev/02M`).

