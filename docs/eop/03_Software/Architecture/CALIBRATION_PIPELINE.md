# EOP Calibration Pipeline

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ See implementation |

---


## Overview

This document describes the complete camera calibration pipeline for EnhancedOpenPilot (EOP), including both runtime calibration and factory calibration workflows.

## Calibration Types

### 1. On-Road Calibration (Dynamic)

**Purpose**: Continuously refine camera extrinsics while driving  
**When**: Every drive, when conditions are suitable  
**What it calibrates**: 
- Roll, Pitch, Yaw (RPY) of road camera relative to vehicle
- Height of camera above ground
- Wide road camera alignment (relative to road camera)

**Storage Format**: Binary Cap'n Proto (cereal)  
**Storage Location**: Params database (`CalibrationParams`)

### 2. Factory Calibration (Static)

**Purpose**: Precise intrinsics and extrinsics measured in factory/shop  
**When**: Once during manufacturing or major hardware changes  
**What it calibrates**:
- Camera intrinsics (focal length, principal point, distortion)
- Precise extrinsics (all cameras relative to vehicle frame)
- Stereo baseline measurement
- Lens characteristics

**Storage Format**: YAML (human-readable)
**Storage Location**: `/data/params/calibration/camera_calibration.yaml`

## Factory vs Runtime Calibration Separation

**IMPORTANT**: Factory and runtime calibration are kept separate to protect intrinsics.

| Parameter | Type | Purpose | Modified By |
|-----------|------|---------|-------------|
| `FactoryCalibrationParams` | IMMUTABLE | Intrinsics (fx, fy, cx, cy, distortion) | Factory only |
| `CalibrationParams` | Runtime | Extrinsics (pitch, yaw, height) | calibrationd |
| `CameraCalibrationParams` | Runtime | Multi-camera extrinsics | camera_calibrationd |

**Factory Intrinsics (NEVER modified by runtime):**
- Focal lengths (fx, fy)
- Principal point (cx, cy)
- Distortion coefficients (k1, k2, p1, p2, k3)
- Image dimensions

**Runtime Extrinsics (refined while driving):**
- Roll, Pitch, Yaw (RPY)
- Camera height above ground
- Cross-camera alignment

## Storage Format Comparison

| Aspect | OpenPilot Native | EOP Extension | VisionPilot |
|--------|------------------|---------------|-------------|
| **Format** | Binary (capnp) | Binary + YAML | YAML |
| **Speed** | Fast (native) | Fast + readable | Readable |
| **Use Case** | Runtime | Factory + Runtime | Factory |
| **Compatibility** | OpenPilot only | OpenPilot + EOP | VisionPilot |
| **Human Readable** | No | Yes (YAML) | Yes |
| **Intrinsics** | No | Yes (protected) | Yes |
| **Multi-camera** | Partial (2 cameras) | Full (5 cameras) | Full (5 cameras) |

## Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FACTORY CALIBRATION (ONE-TIME)                       │
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │ ChArUco      │    │ camera_      │    │ camera_      │                  │
│  │ Pattern      │───▶│ calibrator.  │───▶│ calibration  │                  │
│  │ (Printed)    │    │ py           │    │ .yaml        │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
│                                                   │                          │
│                                    ┌──────────────┴──────────────┐          │
│                                    ▼                              ▼          │
│                      ┌────────────────────────┐    ┌────────────────────┐  │
│                      │ FactoryCalibrationParams│    │ CalibrationParams  │  │
│                      │ (INTRINSICS - IMMUTABLE)│    │ (Initial Extrinsics)│  │
│                      │ fx, fy, cx, cy, k1-k3   │    │ RPY, Height         │  │
│                      └────────────────────────┘    └────────────────────┘  │
│                               🔒 LOCKED                    │                │
└─────────────────────────────────────────────────────────────┼────────────────┘
                                                              │
                                                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         RUNTIME CALIBRATION (CONTINUOUS)                     │
│                                                                              │
│  ┌──────────────┐    ┌──────────────────┐    ┌──────────────┐              │
│  │cameraOdometry│    │  calibrationd /  │    │liveCalibration│             │
│  │(model output)│───▶│camera_calibrationd│───▶│  message     │              │
│  └──────────────┘    └──────────────────┘    │  (4 Hz)      │              │
│                               │               └──────────────┘              │
│                               │                      │                      │
│                               ▼                      ▼                      │
│                      ┌──────────────────────────────────────┐               │
│                      │      CalibrationParams (Runtime)      │               │
│                      │  ✓ Updates: RPY, Height (extrinsics)  │               │
│                      │  ✗ Never modifies: Intrinsics         │               │
│                      └──────────────────────────────────────┘               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MERGED CALIBRATION (RECOMMENDED)                     │
│                                                                              │
│   CalibrationStorage.get_merged_calibration()                               │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  Factory Intrinsics (protected)  +  Runtime Extrinsics (refined)    │   │
│   │  ─────────────────────────────     ──────────────────────────────   │   │
│   │  fx, fy, cx, cy, k1-k3            RPY (pitch/yaw from driving)     │   │
│   │  (from ChArUco calibration)        Height (from road geometry)      │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Calibration Parameters

### Intrinsics (Factory Only)

```yaml
intrinsics:
  width: 1920          # Image width in pixels
  height: 1080         # Image height in pixels
  fx: 1050.0          # Focal length X (pixels)
  fy: 1050.0          # Focal length Y (pixels)
  cx: 960.0           # Principal point X (pixels)
  cy: 540.0           # Principal point Y (pixels)
  distortion:
    k1: 0.01          # Radial distortion coefficient 1
    k2: -0.02         # Radial distortion coefficient 2
    p1: 0.0           # Tangential distortion coefficient 1
    p2: 0.0           # Tangential distortion coefficient 2
    k3: 0.0           # Radial distortion coefficient 3
```

### Extrinsics (On-Road + Factory)

```yaml
extrinsics:
  rotation:            # 3x3 rotation matrix (camera to vehicle)
    - [1.0, 0.0, 0.0]
    - [0.0, 1.0, 0.0]
    - [0.0, 0.0, 1.0]
  translation:         # Translation vector in meters (camera in vehicle frame)
    - 0.0              # X: forward from rear axle
    - 0.0              # Y: left from center
    - -1.22            # Z: up from ground (negative = camera position)

# Or equivalently as RPY + height:
rpy: [0.0, 0.02, -0.01]  # Roll, Pitch, Yaw (radians)
height: 1.22              # Meters above ground
```

### Coordinate Frames

**World/Vehicle Frame (ISO 8855)**:
- X: Forward (along vehicle longitudinal axis)
- Y: Left (vehicle lateral, positive to left)
- Z: Up (vertical, positive upward)
- Origin: Ground level, vehicle center

**Camera Frame (OpenCV)**:
- X: Right (image right)
- Y: Down (image down)
- Z: Forward (optical axis)
- Origin: Camera optical center

**Image Frame**:
- u: X coordinate (0 = left, width-1 = right)
- v: Y coordinate (0 = top, height-1 = bottom)
- Origin: Top-left corner

## Platform-Specific Configurations

### EXO1 (RK3588) - 4 Cameras

```yaml
camera_array:
  platform: rk3588
  stereo_baseline_mm: 80
  cameras:
    road:
      position: [0.0, 0.0, 0.0]      # Origin (reference)
      lens: 8.0mm
      fov: ~60°
    wide_road:
      position: [0.0, 0.08, 0.0]     # 80mm left
      lens: 1.7mm
      fov: ~120°
    stereo_left:
      position: [0.0, 0.08, -0.04]   # 80mm left, 40mm down
      lens: 3.6mm
    stereo_right:
      position: [0.0, 0.0, -0.04]    # Under road, 40mm down
      lens: 3.6mm
```

### EXO2 (RK3576) - 5 Cameras — VisionPilot reference only, not supported by openpilot

```yaml
camera_array:
  platform: rk3576
  stereo_baseline_mm: 160
  cameras:
    wide_road:
      position: [0.0, 0.08, 0.0]     # 80mm left
      lens: 1.7mm (150° FOV)
    road:
      position: [0.0, 0.0, 0.0]      # Origin (reference)
      lens: 8.0mm (60° FOV)
    tele_road:
      position: [0.0, -0.08, 0.0]    # 80mm right
      lens: 16.0mm (30° FOV, 250m)
    stereo_left:
      position: [0.0, 0.08, -0.04]   # Under wide_road, 40mm down
      lens: 3.6mm
    stereo_right:
      position: [0.0, -0.08, -0.04]  # Under tele_road, 40mm down
      lens: 3.6mm
```

## On-Road Calibration Conditions

Calibration updates only occur when ALL conditions are met:

1. **Speed**: > 15 MPH (steady driving)
2. **Straight**: Low yaw rate (< 2°/s)
3. **Certain**: Low velocity angle std (< 0.25°)
4. **Consistent**: Multi-camera agreement within threshold

## Calibration Status

```python
enum Status:
    uncalibrated     # Not enough data collected
    calibrated       # Valid calibration active
    invalid          # Calibration out of valid range
    recalibrating    # Reset due to mounting change
```

## Usage Examples

### Factory Calibration Workflow

```bash
# 1. Print ChArUco pattern
python tools/calibration/camera_calibrator.py --generate-pattern

# 2. Calibrate each camera (use the actual /dev/videoN nodes on your target)
python tools/calibration/camera_calibrator.py --device /dev/video-road --camera road
python tools/calibration/camera_calibrator.py --device /dev/video-wide --camera wide_road

# 3. Batch calibrate all cameras (ExoPilot 01M)
python tools/calibration/camera_calibrator.py --batch --platform rk3588 \
    --road /dev/video-road --wide_road /dev/video-wide \
    --stereo_left /dev/video-stereo-left --stereo_right /dev/video-stereo-right

# 4. Import to system
python -c "
from selfdrive.locationd.calibration_storage import CalibrationStorage
 CalibrationStorage.import_from_factory('/data/calibration/factory.yaml')
"
```

### Runtime Access

```python
from openpilot.selfdrive.locationd.calibration_storage import CalibrationStorage
from openpilot.selfdrive.gridd.camera_geometry import CameraArrayGeometry

# RECOMMENDED: Get merged calibration (factory intrinsics + runtime extrinsics)
calib = CalibrationStorage.get_merged_calibration()

# Alternative: Load only runtime calibration
calib = CalibrationStorage.load_from_params(multi_camera=True)

# Alternative: Load only factory calibration (intrinsics)
factory_calib = CalibrationStorage.load_factory_calibration()

# Get camera geometry with calibration
geometry = calib.to_geometry()

# Project points
u, v = geometry.world_to_image('road', np.array([50.0, 2.0, 0.0]))

# Export for sharing
CalibrationStorage.export_for_sharing('/data/share/my_calibration.yaml')
```

### Cross-Platform Compatibility

```python
# Load VisionPilot calibration into EOP
from selfdrive.locationd.calibration_storage import CalibrationStorage

calib = CalibrationStorage.load_from_yaml('/visionpilot/calibration.yaml')
CalibrationStorage.save_to_params(calib)
```

## File Locations

| File | Path | Purpose |
|------|------|---------|
| **Factory intrinsics** | `/data/params/FactoryCalibrationParams` | IMMUTABLE intrinsics (binary) |
| Runtime extrinsics | `/data/params/CameraCalibrationParams` | Multi-camera extrinsics (binary) |
| Legacy runtime | `/data/params/CalibrationParams` | Backward compatibility |
| Factory YAML | `/data/params/calibration/camera_calibration.yaml` | Human-readable factory |
| Calibration tool | `/data/openpilot/tools/calibration/camera_calibrator.py` | Factory calibration |
| Storage bridge | `/data/openpilot/selfdrive/locationd/calibration_storage.py` | Format conversion |
| Geometry module | `/data/openpilot/selfdrive/gridd/camera_geometry.py` | Coordinate transforms |

## Calibration Quality Metrics

### Reprojection Error
- **Good**: < 0.5 pixels
- **Acceptable**: 0.5-1.0 pixels
- **Poor**: > 1.0 pixels (recalibrate)

### Calibration Spread
- **Stable**: < 0.5° spread over 50 blocks
- **Warning**: 0.5-2° spread
- **Reset**: > 2° spread (mounting change detected)

### Cross-Camera Consistency
- **Good**: < 1° difference between cameras
- **Warning**: 1-2° difference
- **Error**: > 2° difference (check mounting)

## Troubleshooting

### Calibration Incomplete
- Drive on straight highway > 15 MPH for 5+ minutes
- Ensure lanes are clearly visible
- Avoid sharp turns

### Calibration Invalid
- Check camera mounting is secure
- Verify pitch limits for platform
- Reset calibration: `rm /data/params/CameraCalibrationParams`

### Cross-Camera Inconsistency
- Verify stereo baseline measurement
- Check camera synchronization
- Ensure all cameras have same firmware version

## References

- [OpenCV Calibration Tutorial](https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html)
- [ChArUco Pattern](https://docs.opencv.org/4.x/df/d4a/tutorial_charuco_detection.html)
- VisionPilot Camera Calibration
