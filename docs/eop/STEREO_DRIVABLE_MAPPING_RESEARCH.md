# EOP10 stereo drivable mapping research

Research date: 2026-10-18. Proposal only; no runtime behavior changed.

## Recommendation

Use GPU stereo depth plus a rolling 2.5D elevation/occupancy grid, fused with
the existing camera-card semantic segmentation. CornerPilot may supply moving
object tracks, but geometry must retain all obstacles regardless of velocity.
A parked car removes space from the traversable corridor without needing an
object identity. A moving object that stops must remain occupied too.

The map should distinguish observed traversable surface, occupied space, and
unknown/occluded space. Missing stereo returns are not evidence of free road.
Road semantics alone must never clear a geometric obstacle. Planner clearance
must include vehicle footprint, uncertainty, and a configurable margin.

## What exists in this branch

- `nagaspilot/daemons/stereod/stereod.py` publishes float32 disparity and
  confidence; its 20 Hz rate and 50 ms SGM limit are targets, not measurements.
- `nagaspilot/daemons/stereod/sgm.py` explicitly implements NumPy CPU SGM.
  Acquiring an ACL client does not make this implementation GPU accelerated.
- Rectification maps and Q are initialized to None in StereoD; the inspected
  run path does not load them before remapping. Fix calibration ownership first.
- `nagaspilot/daemons/gridd/gridd.py` already lazily reprojects disparity.
  Audit its Q interpretation against homogeneous Q multiplication: it extracts
  focal length from Q[2,2], whereas standard OpenCV Q stores it at Q[2,3].
  Its sampling mask also does not implement the documented every-fourth-pixel
  sampling: offsets two and three survive.
- `nagaspilot/daemons/segd/drivable_bev.py` projects card segmentation onto
  z=0. This is useful semantic evidence, but does not measure terrain height.
- `nagaspilot/daemons/gridd/drivable_fusion.py` already fuses semantic evidence
  with log odds and decay, but only translates forward; turning compensation
  is required before accumulating geometry.
- Shared BEV_GRID is 150 by 60 cells at 0.5 m, covering -20 to +55 m forward
  and -15 to +15 m lateral. Reuse its layout initially; evaluate finer near-field
  resolution for curbs and narrow clearances separately.
- OpenCL reprojection and occupancy source kernels exist in
  `system/inferenced/kernels/`. Presence does not prove runtime dispatch.
  Occupancy writes to the float grid are not atomic, row/column conventions
  differ from BEV_GRID, and decay toward zero must not mean decay toward free.
  These kernels need correction and parity tests before reuse.

## Algorithm and compute placement

1. Synchronize exposure timestamps/frame identities; load measured intrinsics,
   distortion, baseline and extrinsics. Reject stale/mismatched pairs.
2. Rectify and compute Census + four-path SGM through an actual OpenCL backend
   managed by InferenceD. Keep image and intermediate buffers on device.
3. Reject inconsistent left/right matches, low texture, speckles and invalid
   disparities. Confidence needs calibration, not just a positive-value test.
4. Reproject only useful pixels with the correct resolution-specific Q, then
   transform optical coordinates into vehicle x-forward/y-left/z-up.
5. Estimate a local ground surface using robust tiled height statistics and
   neighborhood slope/step checks. Avoid a single flat plane on hills; reject
   unsupported ground estimates and never average car roofs into road height.
6. Reduce ground support and obstacle evidence into cells using integer atomic
   counters or deterministic reductions, then one update per cell. Preserve
   uncertainty, observation age and visibility independently of semantics.
7. Use visibility-aware rays/bins for free-space updates. Stereo camera rays
   have height: do not blindly copy a planar lidar ray-clearing rule. Space
   hidden by a parked car stays unknown until observed.
8. Warp the rolling map with timestamp-aligned ego pose, including yaw, and
   propagate pose/depth uncertainty. Decay stale evidence toward unknown.
9. Fuse card semantics at matched camera pose/time. Emit a traversable corridor
   and obstacle cost into the existing gridd/pathd interface. Confirm downstream
   unknown-cell behavior before enabling control authority.

Use existing odometry/IMU to align frames first. Full-cloud ICP, global SLAM,
TSDF meshing and an ever-growing averaged cloud are unnecessary for this local
driving map. Reconsider registration only if replay shows pose alignment is
insufficient. Fuse per-cell evidence after motion compensation rather than
averaging unaligned XYZ samples.

## Rockchip budget and alternatives

GPU is the preferred stereo target; CPU handles orchestration and the small
grid until profiling justifies moving grid operations. Keep the PCIe card on
semantics and RKNN workloads separate. No target-device speed claim is made.
Profile with the UI, camera preprocessing and normal inference running: low
animation load does not establish memory-bandwidth or thermal headroom.

Benchmark 640x360 and 640x480, 64 disparities, four paths, at 10 then 20 Hz.
These are experiments, not a selected operating point. Choose resolution and
disparity range from calibrated near-distance coverage and far-distance error:
Z=fB/d and sigma_Z approximately Z^2 sigma_d/(fB). Reducing maximum disparity
can lose nearby obstacles; reducing image resolution damages far-range depth.
At 640x480x64, one uint8 cost volume is 18.75 MiB and one uint16 aggregate
volume is 37.5 MiB, before outputs and backend-specific working memory.
Prefer bounded buffers, ROI/tiled processing where mathematically valid, and
measure transfers plus kernels, not kernels alone.

| Reference | Useful idea | EOP10 decision |
| --- | --- | --- |
| Autoware probabilistic occupancy | Angular bins, ray visibility, Bayesian updates and unknown space | Adapt the algorithm to stereo; no full ROS dependency |
| ANYbotics elevation_mapping | Local elevation with pose/measurement uncertainty | Borrow the representation; full package integration is unnecessary initially |
| stereo-sgm-opencl | OpenCL SGM, four paths and subpixel disparity | Benchmark candidate; desktop results do not establish Mali performance; README reports broken diagonal paths |
| Fixstars libSGM / nvblox | Accelerated stereo / volumetric reconstruction | CUDA implementations are not direct Mali backends |

Audit dependency licenses, device workgroup/local-memory assumptions and
driver support before importing any implementation. ACL availability alone
does not supply an SGM algorithm; CLBlast GEMM is not a substitute for SGM.

## Implementation and validation order

First repair calibration, pairing, Q reprojection and truthful backend status.
Then benchmark a four-path OpenCL backend against recorded stereo and a CPU
reference. Add geometric ground/occupancy fusion and full ego-motion warping
only after depth passes metric checks. Finally integrate semantic evidence
and replay the resulting corridor before enabling planner influence.

Required cases: parked cars on either side, a stopped former moving track,
curves, slopes, speed bumps, curbs, thin obstacles, occlusion, textureless/wet
road, glare/night, missing frames, calibration mismatch and GPU timeout.
Measure metric depth error versus range, obstacle recall, false-free cells,
corridor clearance/stability, p50/p95/p99 latency, dropped frames, memory,
CPU/GPU utilization, UI frame time and sustained thermals on each target SoC.
Do not infer all-around static coverage from front stereo: side/rear geometry
or sufficiently fresh observed map coverage is needed beyond its field of view.

## Primary references

- [Autoware pointcloud occupancy](https://autowarefoundation.github.io/autoware_universe/main/perception/autoware_probabilistic_occupancy_grid_map/pointcloud-based-occupancy-grid-map/)
- [ANYbotics elevation mapping](https://github.com/ANYbotics/elevation_mapping)
- [OpenCL SGM implementation and known issues](https://github.com/siposcsaba89/stereo-sgm-opencl)
- [Fixstars CUDA libSGM](https://github.com/fixstars/libSGM)
- [NVIDIA nvblox](https://nvidia-isaac.github.io/nvblox/public/index.html)
- [Arm Compute Library](https://github.com/ARM-software/ComputeLibrary)
