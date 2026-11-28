# EOP10 MetaDrive 3D Behavioral Verification Log

- **Branch**: `dev/EOP10` (RK3588 / native C++ Qt / 1024×600; linear descendant of `dev/NGP10`, 128/128 portable modules match)
- **Date**: 2026-11-28
- **Test Script**: `exopilot/tests/test_eop10_metadrive_loop.py`
- **Host / Simulator**: Linux WSL2 (AMD Ryzen 7 7840HS + NVIDIA RTX 4050 EGL + PoCL OpenCL CPU), Python 3.12.14, MetaDrive `0.4.2.4`, `pytest 9.2.0`
- **Result**: **3 / 3 MetaDrive suites PASSED (64.8 s)** + **4 / 4 boundary checks PASSED** + **`./test.sh` gate PASSED**

## Real Product Bugs Exposed & Fixed During Verification

1. **`selfdrive/controls/lib/aeb.py` (P0 AEB premature brake release before `AEBState.HOLD`)**:
   - `_entry_plausible()` still applied `MIN_EGO_SPEED_MS (2.0 m/s)` and `MIN_CLOSING_SPEED_MS (1.0 m/s)` when `active=True`, and `_state_braking()` treated `ttc > TTC_RELEASE (4.5 s)` as `"Threat receding"` when ego slowed near a stationary obstacle (`d / v_ego > 4.5 s`), releasing brakes right in front of a stopped vehicle instead of completing the stop into `AEBState.HOLD`.
   - **Fix**: Keep active AEB tracking below `2.0 m/s` down to `< 0.5 m/s -> AEBState.HOLD`, transition directly to `HOLD` on `< 0.5 m/s` with threat present, and guard `TTC_RELEASE` when a stationary obstacle (`v_x < 0.5 m/s`) is within `12.0 m`.
2. **`selfdrive/controls/lib/radar_zones.py` (`controlsd` `TypeError` crash on live capnp `stereoObjects`)**:
   - `int(obj.laneZone)` raised `TypeError: ... not 'capnp.lib.capnp._DynamicEnum'` on real pycapnp `CameraObject` messages (masked previously by integer mocks).
   - **Fix**: Added `_lane_zone_int(val)` reading `int(val.raw)` for pycapnp enums and `int(val)` for plain ints.
3. **`selfdrive/controls/lib/longitudinal_planner.py` (`plannerd` `AttributeError` crash on close TLSC red light)**:
   - `_with_virtual_stop_lead()` returned a `SimpleNamespace` without `modelProb`, causing `long_mpc.py:449` (`radarstate.leadOne.modelProb > 0.9`) to raise `AttributeError` on close red-light approaches.
   - **Fix**: Added `modelProb=0.0` to the virtual stop-line lead.
4. **`selfdrive/controls/lib/sqsc.py` (17–28 s rough-road activation lag)**:
   - `_phase_fusion()` ramped down from the `200.0 kph` inactive sentinel at `4.5–7.5 kph/s` instead of seeding from current vehicle speed, burning 340–570 m before slowing the car.
   - **Fix**: Seed `current_limit_kph` from `max(v_ego_kph, target_limit_kph)` when entering an active cap from inactive.

## Scenario-by-Scenario Test Design & Measured Telemetry

| # | Test Function & Feature | Test Design & MetaDrive 3D Stimulus | Expected Behavior | Measured Result | Status |
|---|---|---|---|---|---|
| 1 | `test_eop10_metadrive_curve_vtsc_dlon_dlat_and_camera_sim`<br>**VTSC + DLON + DLAT + `CameraSim` (`v4l2d` OpenCL NV12 VisionIPC)** | PGMap `straight(60m) -> curve(R=60m, 60°) -> straight(240m)`, `v_cruise=54 km/h`, lane fade steps 80..139, live 3D `RGBCameraRoad(1928x1208)` via PoCL OpenCL NV12 -> `v4l2d` VisionIPC. | 100% on-lane; VTSC slows `< 12.5 m/s` on curve & recovers `> +1.5 m/s` on exit; DLON detects sharp curve; DLAT switches laneful <-> laneless; 16 valid NV12 frames. | `on_lane=320/320`, `vtsc_ticks=79`, `v_curve_min=8.65 m/s`, `v_exit_peak=10.38 m/s`, `cam_frames=16` | **PASS** |
| 2 | `test_eop10_metadrive_tlsc_mtsc_mslc_brsc_gridd_and_tesla_can`<br>**TLSC (P0 Red-Light Stop-Line MPC) + MSLC/NSLC + BRSC + Tesla CAN + `GridD` (`leadsV3` & `DrivableFusion`)** | 60 m red light -> green release; `MSLC=50` vs `NSLC=36 km/h` `SpeedPolicy.LOWEST`; BRSC `-v[0]` roughness + `v[2]` rejection + IMU dropout + unthrottled brake release; `SimulatedCar` Tesla CAN checksum round-trip (`CarState.update`); `GridD._fuse_modeld_detections` y-flip + ego-motion `DrivableFusion` shift. | Full stop before 60 m stop line (`v < 0.5 m/s`) + immediate green acceleration; `LOWEST` caps ~10 m/s; BRSC slows without false `v[2]` trigger; Tesla CAN decodes speed/blinker/cruise within `0.5 m/s`; `GridD` flips modelV2 `-2.5 right -> +2.5 left` and shifts drivable grid by live speed. | `tlsc_stop_x=41.2 m / 60 m`, `v_stop=0.11 m/s`, `mslc_nslc=8.99 m/s`, `brsc: 18.62 -> 12.10 m/s`, `can_v=18.58 m/s`, `drivable_shift=37` | **PASS** |
| 3 | `test_eop10_metadrive_3d_traffic_perception_aeb_bsd_sqsc_lhlc_rcsc_sam_alcc_pathd_adaptd`<br>**MTSC -> VTSC Handover + SQSC + RCSC + LHLC (500 m) + SAM -> DDSC** | Straight highway: 1 Hz `mapdExtendedOut` `R=55 m` curve overlapping vision VTSC + 3 s silence expiry; real capnp `surfaceStatus` rough (`0.85`) for SQSC; wet (`0.55`) for RCSC; real capnp `enhancedTrajectory` `9.5 m/s` for LHLC; `DriverActivityMonitor` decay -> DDSC -> `steeringPressed` reset. | Each speed controller independently decelerates the real MetaDrive car `>= 1.5 m/s` and recovers cleanly `>= +1.0 m/s` once cleared; MTSC expires after 3 s. | `mtsc: 17.51 -> 6.44 -> 12.03 m/s`, `sqsc: 12.03 -> 6.83 m/s`, `rcsc: 16.01 -> 13.17 m/s`, `lhlc: 16.02 -> 8.96 m/s`, `sam_ddsc: 18.00 -> 15.90 m/s` | **PASS** |
| 4 | `test_eop10_metadrive_3d_traffic_perception_aeb_bsd_sqsc_lhlc_rcsc_sam_alcc_pathd_adaptd`<br>**3D RSS AEB (`77 GHz` Radar Authority + Driver Override) + Pixel-to-Brake TLSC (`BGR -> HSV -> monod -> GridD -> Planner`) + 3D TJA Cut-In + `PathD/DPP`** | Camera-only (`radar=False`) rejected by AEB; real 3D stopped obstacle `47 m` ahead at `14 m/s` (`radar=True`) -> AEB stops MetaDrive car into `HOLD`; `5.0 Nm` steering override cancels AEB. Synthetic red-lamp BGR image -> `monod` HSV `classify()` -> `fill_raw_detections` -> `GridD._fuse_mono_detections` -> `stereoObjects` -> `LongitudinalPlanner` braking. Two 3D vehicles for `TJA` cut-in + `GreenLightNotice` + `LeadDepartureNotice` + `RuleChannelConsumer` (`SUPERVISE` & `PRIMARY_LAT`). | AEB rejects camera-only lead, hits `-3.48 m/s²` EMERGENCY braking and stops safely (`gap > 0.5 m`, `v < 0.4 m/s`), releases on `5 Nm` steering; pixel-to-brake TLSC outputs `< -1.0 m/s²`; 3D TJA clamps cut-in accel; DPP blends curvature. | `aeb_gap=2.11 m`, `aeb_decel=-3.48 m/s²`, `px_tlsc_a=-1.80 m/s²`, `tja_cutin=True`, `dpp_curv=0.0026` | **PASS** |
| 5 | `test_eop10_metadrive_3d_traffic_perception_aeb_bsd_sqsc_lhlc_rcsc_sam_alcc_pathd_adaptd`<br>**3D Blind-Spot Car -> `SideD` BEV + `Radar2D` -> `RadarZoneMonitor` BSD WARNING & LCA Block -> `DesireHelper` + `SOC` + `ALCC` + `RED` + `CAT` + `AdaptD` + `SocketD SafetyManager`** | Real 3D adjacent-lane overtaking vehicle (`16 vs 22 m/s`); `SideD` ground-plane BEV reprojection (`R_cv`); real capnp `stereoObjects` BLE corner track (`encode_corner_track_id`) + `sideDetections` -> `RadarZoneMonitor` -> real capnp `blindSpotAlert` -> `DesireHelper`; plus `SOC`, `ALCC` (3 BrakeModes), `RED`, `CAT`, `BlinkerPause`/`Ramp`, `AdaptD` (8% SOC + 58 °C derating), `SocketD` `SafetyManager` TX block (`0x488`). | SideD BEV puts object behind ego on correct side (`sz ~ 0`); `RadarZoneMonitor` fuses camera+radar (`cameraAssociated=True`), triggers `WARNING` + `lcaBlocked`; `DesireHelper` holds `preLaneChange` then allows `laneChangeStarting` when clear; `SOC` offsets away; `AdaptD` derates `accel_max <= 0.8`; `SafetyManager` blocks unauthorized `0x488` steering TX. | `bsd_warn=True`, `cam_assoc=True`, `lca_block=True`, `soc=+0.005 m`, `adaptd_amax=0.8`, `safety_tx_blocked=True`, `v_ego_bs=16.0 m/s` | **PASS** |

## Reproducer Command

```bash
unset OPENPILOT_PREFIX
export DISPLAY=:0
export OCL_ICD_VENDORS=/home/vcar/exo-sim/pocl/etc/OpenCL/vendors
export PYOPENCL_CTX=0
export PYTHONPATH=$(pwd)
pytest -n 0 -v -s exopilot/tests/test_eop10_metadrive_loop.py
./test.sh
```
