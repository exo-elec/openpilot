# NGP10 MetaDrive 3D Behavioral Verification Log

- **Branch**: `dev/NGP10`
- **Date**: 2026-11-28
- **Test Script**: `exopilot/tests/test_ngp10_metadrive_loop.py`
- **Host / Simulator**: Linux WSL2 (AMD Ryzen 7 7840HS + NVIDIA RTX 4050 EGL + PoCL OpenCL CPU), Python 3.12.14, MetaDrive `0.4.2.4`, `pytest 9.2.0`
- **Result**: **3 / 3 MetaDrive suites PASSED (38.9 s)** + **765 passed, 4 skipped host unit/boundary tests (14.62 s)**

## Scenario-by-Scenario Test Design & Measured Telemetry

| # | Test Function & Feature | Test Design & MetaDrive 3D Stimulus | Expected Behavior | Measured Result | Status |
|---|---|---|---|---|---|
| 1 | `test_ngp10_metadrive_curve_vtsc_dlon_dlat_and_camera_pipeline`<br>**VTSC + DLON + DLAT + Camerad OpenCL NV12 VisionIPC** | PGMap `straight(60m) -> curve(R=60m, 60°) -> straight(240m)`, `v_cruise=54 km/h`, `FeatureFlags.VTSC`, simulated lane-line fade (`lane_prob=0.15` steps 80..139), live 3D `RGBCameraRoad(1928x1208)` via PoCL OpenCL NV12 -> VisionIPC. | Stay 100% on-lane; VTSC slows `< 12.5 m/s` on curve and recovers `> +1.5 m/s` on exit; DLON detects sharp curve; DLAT switches laneful <-> laneless; all 16 NV12 frames valid. | `on_lane=320/320`, `vtsc_ticks=74`, `v_curve_min=8.71 m/s`, `v_exit_peak=10.81 m/s`, `cam_frames=16` | **PASS** |
| 2 | `test_ngp10_metadrive_tlsc_mtsc_mslc_brsc_sam_ddsc_cutin_pathd_alcc`<br>**TLSC (P0 Red-Light Virtual Stop-Line MPC + Green Release)** | Straight highway cruise `14 m/s`, confirmed red traffic light `60 m` ahead for 220 steps (11 s), then switches to confirmed green (`0.95`). | Smooth jerk-limited full stop BEFORE `60 m` stop line (`v < 0.5 m/s`, never creeping past line); immediate `_light=None` release on green and acceleration `> 4.5 m/s`. | `tlsc_stop_x=41.1 m / 60 m`, `v_stop=0.00 m/s`, immediate green release | **PASS** |
| 3 | `test_ngp10_metadrive_tlsc_mtsc_mslc_brsc_sam_ddsc_cutin_pathd_alcc`<br>**MSLC + NSLC (`SpeedPolicy.LOWEST`)** | Straight highway (`v_cruise=72 km/h`), simultaneous `mapdOut.speedLimit = 50 km/h` and `navInstruction.speedLimit = 36 km/h (10 m/s)` for 180 steps, then raise both to `72 km/h`. | `SpeedPolicy.LOWEST` selects `36 km/h (10 m/s)` and caps MetaDrive speed near `10 m/s`, then recovers `> +2.5 m/s` on limit raise. | `mslc_nslc_mean=9.23 m/s`, smooth recovery after raise | **PASS** |
| 4 | `test_ngp10_metadrive_tlsc_mtsc_mslc_brsc_sam_ddsc_cutin_pathd_alcc`<br>**BRSC (Vertical `-v[0]` IMU + `v[2]` Rejection + Dropout + Unthrottled Brake Release)** | Cruise `18 m/s`: longitudinal `v[2]` sine (`±4 m/s²`), then vertical `-v[0]` washboard (`±4.8 m/s²`), then IMU dropout (`valid=False`), then smooth recovery; plus brake release from `-2.5 m/s²`. | Zero false trigger on longitudinal axis; slowdown floored at `BRSC_MIN_SPEED_MS (8.3 m/s)`; instant clear on IMU dropout; brake release never held negative. | `brsc: 19.62 -> 11.98 m/s`, zero `v[2]` false trigger, dropout reset, brake release `>= 0.0` | **PASS** |
| 5 | `test_ngp10_metadrive_tlsc_mtsc_mslc_brsc_sam_ddsc_cutin_pathd_alcc`<br>**SAM (`DriverActivityMonitor`) -> DDSC Speed Cap -> `steeringPressed` Reset** | Cruise `20 m/s` engaged: driver inactive -> SAM awareness decays -> DDSC slows MetaDrive car -> `driver_engaged=True` resets SAM and car recovers. | Decelerate `>= 1.5 m/s` while awareness decays, then accelerate `>= +1.0 m/s` on driver steering activity. | `sam_ddsc: 20.14 -> 13.75 m/s`, clean recovery on `driver_engaged=True` | **PASS** |
| 6 | `test_ngp10_metadrive_3d_traffic_mtsc_rcsc_tja_red_alcc_cat_lca_and_notices`<br>**MTSC -> VTSC Handover (`blend_mtsc_vtsc`) + 3 s Expiry + RCSC Wet Slowdown** | Cruise `17.5 m/s`: 1 Hz `mapdExtendedOut` `R=55 m` curve at `240 m` overlapped with vision `R=55 m` curvature (steps 60..109), then silence `> 3 s`. Next feed `surfaceStatus` wet score `0.55` -> dry `0.05` (`RCSCEnabled=True`). | Overlap handover active; MTSC expires cleanly after `> 3 s`; RCSC steps immediately to `12.0 m/s` (no legacy `0.3 m/s` filter dip), slows MetaDrive car, and recovers on dry road. | `mtsc: 17.51 -> 6.67 -> 13.79 m/s`, `handover=True`, `rcsc: 13.79 -> 9.23 -> 14.72 m/s` | **PASS** |
| 7 | `test_ngp10_metadrive_3d_traffic_mtsc_rcsc_tja_red_alcc_cat_lca_and_notices`<br>**Real 3D Lead ACC Stop-and-Go + `GreenLightNotice` + 3D `LeadDepartureNotice` + 3D TJA + `MonoD CutInSpeed` + `PathD/DPP` (`SUPERVISE` & `PRIMARY_LAT`)** | Real 3D `DefaultVehicle` lead (`48 m`) brakes `11 -> 0 m/s` then departs `0 -> 6 m/s`; two real 3D vehicles (crawl lead + adjacent-lane `cutin_car` merging into ego lane) feeding `TJA` & `MonoD CutInSpeed`; `RuleChannelConsumer` in `SUPERVISE` and `PRIMARY_LAT` + driver override. | Safe 3D stop (`min_gap > 2 m`, `v < 0.4 m/s`); `GreenLightNotice` & 3D `LeadDepartureNotice` fire cleanly; `TJA` clamps accel & `CutInSpeed` brakes for 3D cut-in; `DPP PRIMARY_LAT` smoothly ramps curvature and yields to policy on override. | `min_gap=8.00 m`, `v_stop=0.17 m/s`, `gln=True`, `ld=True`, `tja_cutin=True`, `cutin_braked=True`, `dpp_curv=0.0026` | **PASS** |
| 8 | `test_ngp10_metadrive_3d_traffic_mtsc_rcsc_tja_red_alcc_cat_lca_and_notices`<br>**ALCC (3 BrakeModes) + RED (Warning/Critical Override) + BlinkerPause/Ramp + CAT + DesireHelper/TurnDesire + 3D `LCAdjacentLeadHandoff` + 3D `SOC`** | Exercise `ALCC` `MAINTAIN` / `PAUSE` / `DISENGAGE`+re-arm + override; `RED` right edge `0.75 m` (`WARNING`) & `0.25 m` (`CRITICAL` path shift); `CAT` `SteerRatioFilter` (35 ticks + manual SR); `BlinkerPause` + `SteeringResumeRamp`; `DesireHelper` blindspot & `TurnDesire`; real 3D adjacent-lane car (`22 m` ahead at `6 m/s`) driving `LCAdjacentLeadHandoff` and highway `SOC` offset. | All 3 ALCC brake modes & override exact; RED nudges left (`+curv`) & shifts path `-y`; CAT adapts smoothly; 3D adjacent car slows ego during `laneChangeStarting` (`>= 2.5 m/s`) and triggers bounded away-from-threat `SOC` offset. | `alcc_modes=3/3`, `red_nudge=+0.00006`, `cat_sr=14.71`, `3d_lc_handoff: 15.01 -> 8.94 m/s`, `3d_soc=+0.015 m` | **PASS** |

## Reproducer Command

```bash
unset OPENPILOT_PREFIX
export DISPLAY=:0
export OCL_ICD_VENDORS=/home/vcar/exo-sim/pocl/etc/OpenCL/vendors
export PYOPENCL_CTX=0
export PYTHONPATH=$(pwd)
pytest -n 0 -v -s exopilot/tests/test_ngp10_metadrive_loop.py
pytest -q --ignore=exopilot/tests/test_ngp10_metadrive_loop.py exopilot/tests/
```
