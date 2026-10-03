# Design Document: CALIBRATIOND (Camera Calibration Service)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/locationd/calibrationd.py` |

---


## 1. Objective

CALIBRATIOND is the camera calibration service for EOP. It determines the relationship between camera frames and the vehicle coordinate system through two complementary processes:
- **Factory Calibration**: One-time precise measurement using ChArUco patterns
- **Runtime Calibration**: Continuous refinement of extrinsics while driving

## 2. Calibration Types

### 2.1 Factory Calibration (Intrinsics - IMMUTABLE)

**Purpose**: Measure camera optical properties that don't change during operation.

| Parameter | Description | Typical Values |
|-----------|-------------|----------------|
| `fx, fy` | Focal lengths (pixels) | 800-2000 |
| `cx, cy` | Principal point (pixels) | image_center ± 50 |
| `k1-k3` | Radial distortion | -0.5 to 0.5 |
| `p1, p2` | Tangential distortion | -0.01 to 0.01 |

**Storage**: `EOPFactoryCalibrationParams` - **NEVER modified by runtime processes**

### 2.2 Runtime Calibration (Extrinsics - REFINED)

**Purpose**: Continuously adjust camera mounting angles based on driving data.

| Parameter | Description | Range |
|-----------|-------------|-------|
| Roll | Rotation around X-axis | ±5° |
| Pitch | Rotation around Y-axis | -9° to +5° |
| Yaw | Rotation around Z-axis | ±4° |
| Height | Camera height above ground | 1.0-1.8m |

**Storage**: `CalibrationParams` / `EOPCameraCalibrationParams`

## 3. Technical Architecture

### 3.1 Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    FACTORY CALIBRATION                       │
│                                                              │
│   ChArUco → camera_calibrator.py → FactoryCalibrationParams │
│                                        🔒 LOCKED             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    RUNTIME CALIBRATION                       │
│                                                              │
│   modeld/cameraOdometry → calibrationd → CalibrationParams  │
│                                ↓                             │
│                        liveCalibration                       │
│                           (4 Hz)                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Daemon Variants

| Daemon | Cameras | Platform | Use Case |
|--------|---------|----------|----------|
| `calibrationd` | Road + Wide road | All | Stock OpenPilot |
| `camera_calibrationd` | All 5 | EXO2/EXO3 | Enhanced multi-camera |

### 3.3 Runtime Calibration Algorithm

```python
# Conditions for calibration update
if (speed > 15 MPH and
    yaw_rate < 2°/s and
    velocity_angle_std < 0.25°):

    # Compute observed RPY from camera odometry
    observed_pitch = -arctan2(trans_z, trans_x)
    observed_yaw = arctan2(trans_y, trans_x)

    # Moving average with linear decay
    new_rpy = (idx * prev_rpy + (BLOCK_SIZE - idx) * observed_rpy) / BLOCK_SIZE

    # Sanity clip to valid range
    new_rpy = clip(new_rpy, limits)
```

### 3.4 Multi-Camera Consistency (EXO2)

For multi-camera systems, `camera_calibrationd` performs cross-camera validation:

```python
# Check all cameras agree within threshold
for cam1, cam2 in combinations(cameras, 2):
    rpy_diff = abs(cam1.rpy - cam2.rpy)
    if rpy_diff > MAX_INTER_CAMERA_SPREAD:  # 1.5°
        flag_inconsistency()
```

## 4. File & Class Management

### 4.1 Core Files

| File | Purpose |
|------|---------|
| `selfdrive/locationd/calibrationd.py` | Stock single/dual camera daemon |
| `selfdrive/locationd/camera_calibrationd.py` | Enhanced multi-camera daemon |
| `selfdrive/locationd/calibration_storage.py` | Format bridge (Binary ↔ YAML) |
| `tools/calibration/camera_calibrator.py` | Factory calibration tool |

### 4.2 Related Files

| File | Purpose |
|------|---------|
| `selfdrive/gridd/camera_geometry.py` | Coordinate transformations |
| `selfdrive/monod/calibration_fusion.py` | Multi-source object fusion |
| `tools/factory_calibration/calibrate_stereo.py` | Stereo baseline calibration |

## 5. Param Keys

| Key | Type | Description |
|-----|------|-------------|
| `EOPFactoryCalibrationParams` | BYTES | Immutable factory intrinsics |
| `CalibrationParams` | BYTES | Runtime extrinsics (legacy) |
| `EOPCameraCalibrationParams` | BYTES | Multi-camera runtime extrinsics |
| `EOPFactoryCalibrated` | BOOL | Device has factory calibration |
| `EOPMultiCameraCalibEnabled` | BOOL | Use enhanced daemon |

## 6. API Usage

### 6.1 Recommended: Merged Calibration

```python
from openpilot.selfdrive.locationd.calibration_storage import CalibrationStorage

# Get factory intrinsics + runtime extrinsics (RECOMMENDED)
calib = CalibrationStorage.get_merged_calibration()

# Access camera parameters
road_cam = calib.cameras['road']
fx, fy = road_cam.focal_x, road_cam.focal_y  # Factory intrinsics
pitch, yaw = road_cam.rpy[1], road_cam.rpy[2]  # Runtime extrinsics
```

### 6.2 Factory Import (After ChArUco Calibration)

```python
# Import factory calibration - protects intrinsics
CalibrationStorage.import_from_factory('/data/params/calibration/camera_calibration.yaml')

# This:
# 1. Saves to FactoryCalibrationParams (immutable)
# 2. Saves to CalibrationParams (initial extrinsics)
# 3. Sets EOPFactoryCalibrated = true
```

### 6.3 Runtime Reset (Preserves Factory)

```python
# Reset only runtime calibration (pitch/yaw)
# Factory intrinsics are preserved!
params.remove("CalibrationParams")
params.remove("CameraCalibrationParams")
# FactoryCalibrationParams is NOT removed
```

## 7. UI Integration

The EOP Settings panel provides:
- Factory calibration status indicator
- Multi-camera calibration toggle
- Reset runtime calibration button
- Import/Export calibration buttons

## 8. Quality Metrics

### 8.1 Factory Calibration

| Reprojection Error | Quality |
|--------------------|---------|
| < 0.5 pixels | ✅ Good |
| 0.5-1.0 pixels | ⚠️ Acceptable |
| > 1.0 pixels | ❌ Recalibrate |

### 8.2 Runtime Calibration

| Metric | Threshold | Action |
|--------|-----------|--------|
| Pitch spread > 4° | Reset | Camera mount changed |
| Yaw spread > 2° | Reset | Camera mount changed |
| Cross-camera diff > 1.5° | Warning | Check mounting |

## 9. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Factory calibration tool | ✅ Done | `camera_calibrator.py` with ChArUco |
| Runtime calibrationd | ✅ Done | Stock OpenPilot integration |
| Multi-camera calibrationd | ✅ Done | `camera_calibrationd.py` |
| Factory/runtime separation | ✅ Done | `EOPFactoryCalibrationParams` immutable |
| Calibration storage bridge | ✅ Done | `calibration_storage.py` |
| EOP UI integration | ✅ Done | Road-camera factory/runtime calibration section in settings |
| Stereo calibration | ✅ Done | `calibrate_stereo.py` |
| Cross-platform YAML export | ✅ Done | EOP YAML format |
| Side/rear calibration (backend) | ✅ Done | `side_rear_calibration.py` — see `CLAUDE.md` New Features |
| Side/rear first-use UI + reset trigger | ✅ Done | Section 10 below |

---

## Implementation

### Types

| Calibration | Method | Storage |
|-------------|--------|---------|
| Factory | ChArUco board | FactoryCalibrationParams |
| Runtime | Online from driving | CalibrationParams |

### Output

- liveCalibration - Camera extrinsics


## 10. First-Use Calibration Settings Screen (2026-09-28)

**Status**: ✅ Done. Tracked as task #6/#7 in this session's work; `task.md`
has the log entry.

**Problem**: side/rear calibration (`side_rear_calibration.py` /
`SideRearCalibration`, see New Features in `CLAUDE.md`) runs automatically
while driving, but had no UI: no way to see per-camera status from the
settings screen, and no way to force a recalibration after a mount is
physically adjusted (e.g. after service work bumps a side camera). Section 7
above ("UI Integration") describes this for the *road-camera* factory/runtime
split only, predating side/rear entirely.

**Built**:

- **Backend (`dev/EOP10`, not UI — rebased into `dev/01M`/`dev/02M`)**:
  - `SideRearCalibration.reset(camera: str | None = None)`: drops the named
    camera's (or every camera's) `ExtrinsicCalibrator` and cached `_saved`
    entry, so the next `add_frame()` starts a fresh convergence. Does not
    touch the persisted `sensors_tf.yaml` link — `_maybe_save()` overwrites
    it once the camera reconverges, so a reset that never drives again keeps
    the last-good calibration rather than silently losing it.
  - `camera_calibrationd.py`'s `main()` polls `EOPSideRearCalibReset` at the
    same 4 Hz cadence it already publishes at; on a set trigger it calls
    `side_rear.reset()` and clears the param.
  - `common/params_keys.h`: `EOPSideRearCalibReset` (`CLEAR_ON_MANAGER_START`,
    not `PERSISTENT` — same reasoning as `DoReboot`/`DoShutdown`: a stuck
    "true" across a manager restart would reset calibration every boot).

- **UI (`dev/01M` + `dev/02M` identically)**: originally planned as a new
  "Camera Calibration" section in `views/panels/exopilot.py` reading live
  `calibrationState` via the shared `UIState`/`Snapshot` (`state.py`).
  Building that turned up a better fit already in the codebase: the
  road-camera "Reset Calibration" row in `views/panels/device.py` reads its
  status *lazily from Params on demand* (`calibration_description()`, built
  only when the row's description is tapped open), not via live cereal —
  `state.py`'s `UIState`/`Snapshot` mechanism is onroad-only (HUD/sidebar),
  and no settings panel touches messaging today. Side/rear's persisted state
  is a YAML file rather than a Params blob, so the same lazy-read shape
  applies via `hal.calibration.store.load_links()` instead:
  - `device.py` gained a "Side/Rear Camera Calibration" `ButtonControl`
    right below "Reset Calibration", guarded by the same `_guarded()`
    engagement check (stays enabled onroad, same policy as the existing row
    -- "guards itself on engagement, which is the condition that actually
    matters").
  - `side_rear_calibration_description()`: per-camera calibrated / not-yet
    line from `load_links()`, "not available" when `hal.calibration` isn't
    installed (dev PC) -- same lazy-import-with-fallback shape
    `side_rear_calibration.py` itself uses.
  - `EOPSideRearCalibReset` is a one-shot daemon trigger, not a real toggle,
    so it is excluded from the generic descriptor renderer with a reason in
    `settings/exclusions.py` rather than added to `settings/descriptor.py`.
  - Tests: `selfdrive/ui/tests/test_device_panel.py` (7 tests, run against
    the real `hal.calibration` package via the same `../exopilot/hal`
    sys.path fixture `test_side_rear_calibration.py` uses).

- **Not in scope for this pass**: an onboarding/wizard flow (drive N
  minutes, guided prompts) — this is a status-and-reset row, not a guided
  first-drive experience. `x`/`y` translation still cannot be solved from
  motion (per the New Features note) and stays nominal regardless of this
  screen.

## 11. References

- [CALIBRATION_PIPELINE.md](../../03_Software/Architecture/CALIBRATION_PIPELINE.md) - Complete pipeline documentation
- [OpenCV Calibration](https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html)
- [ChArUco Pattern](https://docs.opencv.org/4.x/df/d4a/tutorial_charuco_detection.html)
