# StereoD to point cloud: upstream geometry and execution contract

Updated 2026-10-19. Owner scope: stereo disparity and metric point-cloud
reconstruction first. GPU failure degrades this source; CPU remains assigned to
ADAS. This is not a completed mapping or vehicle-frame calibration claim.

## Exact primary-source audit

The stereo projection reference is ROS perception, not a claimed Autoware Mali
stereo implementation. Autoware's reviewed point-cloud preprocessor describes
LiDAR input; its occupancy stages remain downstream references. We have not
established an upstream Autoware GPU stereo backend usable on Mali.

Pinned sources inspected in this session:

- ROS image_geometry `50f6f1feccc65dd2ef87f863cb812a8aa0789062`:
  [stereo_camera_model.cpp](https://github.com/ros-perception/vision_opencv/blob/50f6f1feccc65dd2ef87f863cb812a8aa0789062/image_geometry/src/stereo_camera_model.cpp).
  It constructs full Q, including unequal focal lengths and principal points,
  and projects disparity through homogeneous coordinates.
- ROS image_pipeline `b529eb6e043fab6a1218b6f895c21e302cfec1e5`:
  [point_cloud_node.cpp](https://github.com/ros-perception/image_pipeline/blob/b529eb6e043fab6a1218b6f895c21e302cfec1e5/stereo_image_proc/src/stereo_image_proc/point_cloud_node.cpp)
  and [stereo_processor.cpp](https://github.com/ros-perception/image_pipeline/blob/b529eb6e043fab6a1218b6f895c21e302cfec1e5/stereo_image_proc/src/stereo_image_proc/stereo_processor.cpp).
  The pipeline synchronizes image/calibration/disparity, uses camera geometry,
  preserves the disparity header in the cloud, and retains invalid-point state.
  Matching supports OpenCV BM/SGBM; this is not evidence for our OpenCL kernels.
- [Autoware point-cloud preprocessor](https://autowarefoundation.github.io/autoware_universe/latest/sensing/autoware_pointcloud_preprocessor/)
  describes its LiDAR sensing/preprocessing boundary. Do not silently substitute
  stereo depth uncertainty or camera frames for LiDAR contracts.

No upstream implementation was copied into production in this slice. Our OpenCL
kernels are adaptations requiring their own numerical and hardware validation.
Any future source reuse must pin its revision and retain its actual license.

## Required stereo flow

1. Pair captures and validate age/skew. Load measured rectification maps at the
   exact image resolution; do not guess camera intrinsics or nominal baseline.
2. Rectify, then match on GPU. Our disparity is raw `u_left - u_right` in pixels,
   float32. It is not OpenCV's integer disparity scaled by 16. It is also not a
   ROS stereo_msgs disparity already adjusted for `cx_left - cx_right`: never
   subtract that offset and then apply a Q that includes it a second time.
3. Reproject `Q * [u,v,d,1]`, divide XYZ by the homogeneous denominator. Keep
   optical right/down/forward in meters. Preserve unequal focal lengths and Q
   offsets. Invalid confidence/disparity/denominator/range yields NaN in the
   organized GPU output; downstream compaction excludes those samples.
4. Carry capture time and source frame identity forward. Reject stale/future or
   duplicate depth and calibration-resolution mismatch. Missing GPU, missing
   confidence or failed projection produces no replacement cloud.
5. Apply a measured optical-to-vehicle transform before treating points as
   calibrated vehicle geometry. Ground/obstacle classification, visible-space
   evidence and temporal mapping come afterwards, per the separate mapping plan.

The live pointcloudd legacy axis rotation is still a nominal orientation, not a
measured mounting transform. Camera height/pitch/extrinsics and calibration
revision identity across messages remain explicit integration gaps. Do not use
that nominal cloud as validated drivable-space geometry. The older gridd lazy
reprojection and all-return occupancy path still needs its GPU/ground split;
this slice does not claim that path is a correct Autoware-style mapper.

## GPU and degradation implementation

- `InferenceClient.gpu()` supplies a smoke-checked, GPU-only context.
- `OpenCLReprojector` dispatches the full-Q kernel with bounded working memory
  and a completed-frame deadline. OpenCV CPU projection is a host oracle only.
- Pointcloudd no longer falls back to CPU when GPU acquisition or dispatch fails.
  Turning its GPU switch off disables projection. The explicit offline
  DepthPipeline CPU reference is not called by live pointcloudd or StereoD.
- StereoD retries initialization every two seconds; runtime device errors drop
  the backend. Invalid/late output is suppressed, including total capture age.
- Optional stereo GPU loss reports degradation rather than disabling core ADAS.
  Core inference/model fault policies retain their disabling behavior.
- Fusion drops stale/faulted stereo input, clears unsupported source flags and
  ages occupancy every tick toward unknown. It does not multiply confidence of
  healthy independent sensors by an arbitrary factor, or treat absent depth as
  free space. The reduction applies to stereo support and its retained evidence.

## Validation and remaining work

Host validation: matcher API tests, padded-row/pair/calibration checks, retry and
late-output tests, status metadata, complete-Q CPU oracle fixtures, GPU-only
point-cloud rejection, source degradation and occupancy aging. Two stereo
numerical cases plus one full-Q GPU/OpenCV parity case are gated behind the
required real-GPU diagnostic. Its unavailable-device result is failure, not a
CPU success or a silently accepted skip.

Required next: measured mounting transform and calibration revision identity;
GPU port and ground/obstacle split before live gridd integration; ray visibility
and unknown-space semantics; synchronized replay; RK3588/RK3576 target accuracy,
latency and concurrent ADAS/UI/thermal profiling. No full pipeline acceptance is
claimed. The existing full test gate stops at bigmodel_platform.py lineage drift.

Owner clarification: core CPU and RKNN safety-NPU failures retain compute
disengagement authority. Optional grid/RGA faults now degrade alongside stereo,
GPU point-cloud, MPP and camera-card loss. See COMPUTE_FAULT_POLICY.md for the
explicit inference classifier, ARM runtime guard and policy tests.
