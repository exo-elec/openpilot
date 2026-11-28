# EDP10 MetaDrive 3D Behavioral Verification Log

- **Branch**: `dev/EDP10`
- **Date**: 2026-11-28
- **Test Script**: `exopilot/tests/test_edp10_metadrive_loop.py`
- **Host / Simulator**: Linux WSL2 (AMD Ryzen 7 7840HS + NVIDIA RTX 4050 EGL + PoCL OpenCL CPU), Python 3.12.14, MetaDrive `0.4.2.4`, `pytest 9.2.0`
- **Result**: **3 / 3 MetaDrive suites PASSED (45.35 s)** + **74 / 74 host unit/closed-loop tests PASSED (1.96 s)**

## Real Product Bug Exposed & Fixed During Verification

1. **`dragonpilot/selfdrive/controls/lib/acm.py` & `selfdrive/controls/lib/longitudinal_planner.py` (`ACM` coasting brake suppression + yield to speed controllers)**:
   - `self.allowed_brake_val` was initialized to `0.0` and never updated, while `update_a_desired_trajectory()` and `update_output_a_target()` checked `a < 0 and a > self.allowed_brake_val` (`a < 0 and a > 0.0` — mathematically impossible). Fixed to `MIN_BRAKE_ALLOW_VALS[1]` (`-0.5` m/s²).
   - **Scrutinize follow-up**: moved `self.acm.update_states()` after `VTSC`/`MTSC`/`MSLC`/`BRSC`/`force_slow_decel` and pass `user_control or speed_cap_active` so ACM coasts smoothly on open road/downhill but yields immediately whenever any speed controller requests deceleration.
2. **`selfdrive/controls/lib/longitudinal_planner.py` & `selfdrive/controls/controlsd.py`**:
   - Persisted `self.tja_result` from `__init__` / `update()` for consistent planner telemetry inspection, and hardened `BlinkerPauseLateralSpeed` parsing against `"0"` / negative values (`float(val or 0) > 0.0`).

## Scenario-by-Scenario Test Design & Measured Telemetry

| # | Test Function & Feature | Test Design & MetaDrive 3D Stimulus | Expected Behavior | Measured Result | Status |
|---|---|---|---|---|---|
| 1 | `test_edp10_metadrive_vtsc_curve_and_camera_pipeline`<br>**VTSC + Camerad OpenCL NV12 + VisionIPC** | PGMap `straight(60m) -> curve(R=60m, 60°) -> straight(240m)`, `v_cruise=54 km/h`, `DPFlags.VTSC`, live 3D `RGBCameraRoad(1928x1208)` converted via PoCL OpenCL NV12 and published over VisionIPC every 20 steps. | Stay 100% on-lane; VTSC slows ego below `12.5 m/s` inside the 60 m curve, clears on the exit straight, and ego accelerates back `> +1.5 m/s`; all RGB->NV12 frames valid. | `dist=252.2 m`, `on_lane=460/460`, `vtsc_ticks=138`, `v_curve_min=7.80 m/s`, `v_exit=11.36 m/s`, `cam_frames=23` | **PASS** |
| 2 | `test_edp10_metadrive_mtsc_mslc_brsc_and_safety_behaviors`<br>**MTSC (Map Curve Speed + 3s Hold Expiry)** | Straight highway cruise (`72 km/h`), inject 1 Hz `mapdExtendedOut` `R=55 m` curve at `260 m` ahead (steps 200..309), then silence mapd `> 3.0 s`. | Decelerate smoothly `>= 2.0 m/s` before curve, clear `_mtsc_v_target` after `3.0 s` mapd silence, and recover speed `>= +1.5 m/s`. | `v: 17.80 -> 8.60 -> 19.95 m/s`, `_mtsc_v_target=None` after expiry | **PASS** |
| 3 | `test_edp10_metadrive_mtsc_mslc_brsc_and_safety_behaviors`<br>**MSLC (Posted Speed Limit Cap & Release)** | Straight highway (`v_cruise=72 km/h`), inject 1 Hz `mapdOut.speedLimit = 36 km/h (10 m/s)` for steps 0..199, then raise to `65 km/h`. | Settle MetaDrive speed near `10.0 m/s` (`8.8..11.2 m/s`), then accelerate `>= +2.5 m/s` after limit raise. | `v_capped_mean=10.47 m/s -> 18.57 m/s` | **PASS** |
| 4 | `test_edp10_metadrive_mtsc_mslc_brsc_and_safety_behaviors`<br>**BRSC (Vertical IMU Roughness + Axis Rejection + Dropout)** | Straight highway (`72 km/h`): steps 0..199 longitudinal `v[2]` sine (`4.0 m/s²`); steps 200..319 vertical `-v[0]` rough pavement (`±4.8 m/s²`); step 320 IMU dropout (`valid=False`); steps 321..459 smooth road. | Ignore longitudinal excitation; activate BRSC on vertical roughness without dropping below `BRSC_MIN_SPEED_MS (8.3 m/s)`; clear immediately on IMU dropout; recover on smooth road. | `v: 17.80 -> 14.38 -> 20.33 m/s`, zero false trigger on `v[2]`, immediate reset on dropout | **PASS** |
| 5 | `test_edp10_metadrive_mtsc_mslc_brsc_and_safety_behaviors`<br>**LC Adjacent Lead Handoff + DesireHelper Guards + CAT + BlinkerPause + Ramp** | Cruise `65 km/h` -> initiate `laneChangeStarting` right with target-lane slower lead at `(x=18 m, y=+3.3 m right, v=6 m/s)`; check `DesireHelper` narrow-lane (`2.0 m < 2.5 m`) block vs full `3.6 m` MetaDrive lane allow; check `CAT`, `BlinkerPause`, `SteeringResumeRamp`, `LeadDepartureNotice`. | MPC hands off to adjacent target-lane lead and slows `>= 3.0 m/s`; narrow target lane blocked (`preLaneChange`), `3.6 m` lane allowed (`laneChangeStarting`); CAT finite; BlinkerPause holds then expires; ramp monotonic. | `lc_handoff: 17.22 -> 8.45 m/s`; narrow lane blocked, `3.6 m` lane allowed; CAT/BlinkerPause/Ramp verified | **PASS** |
| 6 | `test_edp10_metadrive_3d_traffic_acc_tja_acm_alka_red_and_lead_departure`<br>**ACM (Downhill Coasting + Safety Pass-Through + 3D Lead TTC Override)** | Phase A: downhill pitch `-0.06 rad`, ego `14.5 m/s` above `v_cruise=12.0 m/s`, no lead. Safety check: `-0.8 m/s²` request. Phase B: spawn real 3D MetaDrive `DefaultVehicle` `24 m` ahead moving at `4.0 m/s` (`TTC < 3.5 s`). | Phase A: ACM activates and suppresses mild braking to `>= 0.0 m/s²`, while leaving `-0.8 m/s²` safety braking untouched. Phase B: ACM disengages and MPC brakes `< -0.5 m/s²`. | `active=True`, mild brake suppressed (`>= 0.0`), strong brake `-0.8` untouched, `lead_disengage=True`, `braked=True` | **PASS** |
| 7 | `test_edp10_metadrive_3d_traffic_acc_tja_acm_alka_red_and_lead_departure`<br>**Real 3D Lead ACC Stop-and-Go + 3D LeadDeparture + `dp_tja` 3D Cut-In + AEM (`acc <-> blended`)** | Phase A: real 3D lead spawned `48 m` ahead cruises at `11 m/s` then brakes to `0 m/s`. Phase B: stopped 3D lead accelerates away to `6 m/s`. Phase C: two real 3D vehicles (lead `24 m` ahead + adjacent-lane car `14 m` ahead merging into ego lane at step 25) with `DPFlags.AEM`, followed by open road recovery. | Phase A: ego stops cleanly without collision (`min_gap > 2 m`, `v < 0.4 m/s`). Phase B: `LeadDepartureNotice` silent while stopped, fires on 3D departure. Phase C: `dp_tja` detects cut-in & clamps positive accel `<= 0.05 m/s²`; `AEM` switches `acc -> blended` in 3D crawl and `blended -> acc` on clear cruising road. | `min_gap=5.99 m`, `v_stop=0.07 m/s`, `ld_fired=True`, `tja_cutin=True`, `aem_blended=True`, `aem_acc=True` | **PASS** |
| 8 | `test_edp10_metadrive_3d_traffic_acc_tja_acm_alka_red_and_lead_departure`<br>**ALKA + BlinkerPause + SteeringResumeRamp + DragonPilot `RoadEdgeDetector` (RED)** | Cruise disengaged (`selfdriveState.active=False`, `cruiseAvailable=True`) at `10 m/s` with 20-step blinker pause + 1.0 s hold + resume ramp in MetaDrive physics; `RoadEdgeDetector` with left road edge (`std=0.1`) & open right lane (`std=0.9`) feeding `DesireHelper`. | ALKA keeps lateral active (`80` ticks), blinker pauses lateral (`40` ticks) while staying in-lane, torque ramps smoothly; DP RED blocks left LCA into road edge (`preLaneChange`) and permits open right LCA (`laneChangeStarting`). | `alka_steps=80`, `pause_steps=40`, `red_left_blocked=True`, `red_right_ok=True` | **PASS** |

## Reproducer Command

```bash
unset OPENPILOT_PREFIX
export DISPLAY=:0
export OCL_ICD_VENDORS=/home/vcar/exo-sim/pocl/etc/OpenCL/vendors
export PYOPENCL_CTX=0
export PYTHONPATH=$(pwd)
pytest -n 0 -v -s exopilot/tests/test_edp10_metadrive_loop.py
pytest -q --ignore=exopilot/tests/test_edp10_metadrive_loop.py exopilot/tests/
```
