# RecordD - Unified Recording Daemon

Consolidates VisionPilot's loopd, impactd, and snapd into a single daemon.

## Features

### 1. Loop Recording (DVR)
- **Normal mode**: Full framerate (20fps) when ignition ON
- **Parking mode**: Timelapse (1fps) when ignition OFF
- **Event mode**: High-quality recording after trigger
- **Impact mode**: Emergency recording with pre/post buffer
- Hardware H264 encoding via Rockchip MPP (patent-free, H265 requires license)
- Circular buffer with automatic storage management

### 2. Impact Detection
- IMU-based G-force monitoring (LSM6DS3)
- Configurable sensitivity (0-100 scale)
  - 0 = 4G threshold (severe crashes only)
  - 50 = 2G threshold (default)
  - 100 = 1G threshold (minor bumps)
- Pre-impact buffer: Always recording to RAM
- Post-impact recording: Configurable duration
- Cooldown system to prevent multiple triggers
- Manual impact marking

### 3. Snap Capture
- On-demand snapshots via service call
- Saves image + IMU history (configurable ms)
- Auto-trigger on high G-force
- JPEG + JSON output with IMU stats

## Directory Structure

```
/data/media/0/dashcam/
├── normal/          # Regular loop recordings (auto-deleted)
├── parking/         # Parking mode timelapse (auto-deleted)
├── events/          # Impact events with pre-impact video (preserved)
└── snap/            # Manual snapshots
```

## Configuration

Environment variables:
- `RECORD_ROOT`: Base directory (default: /data/media/0/dashcam)
- `RECORD_NORMAL_FPS`: Normal mode framerate (default: 20)
- `RECORD_PARKING_FPS`: Parking mode framerate (default: 1)
- `RECORD_SEGMENT_S`: Segment duration in seconds (default: 60)
- `RECORD_MAX_GB`: Max storage in GB (default: 8)
- `RECORD_PRE_BUFFER_S`: Pre-impact buffer seconds (default: 15)
- `RECORD_POST_BUFFER_S`: Post-impact recording seconds (default: 30)

Params (UI configurable):
- `EOPRecorddImpactSensitivity`: 0-100
- `EOPRecorddPreBufferSec`: Seconds before impact
- `EOPRecorddPostBufferSec`: Seconds after impact
- `EOPRecorddParkingEnabled`: Enable parking mode
- `EOPRecorddParkingHours`: Max parking record time
- `EOPRecorddQuality`: low/medium/high
- `EOPRecorddSnapImuMs`: IMU history for snapshots
- `EOPRecorddSnapAutoG`: Auto-trigger threshold (0=off)

## Cereal Messages

### Subscribes
- `deviceState` - Device status
- `pandaState` - Vehicle state
- `accelerometer` - IMU accel (for impact detection)
- `gyroscope` - IMU gyro (for impact detection)

### Publishes
- `recorddState` - Daemon state and statistics

## Services (Internal RPC)

```python
mark_event(reason)              # Mark current time as event
set_impact_sensitivity(0-100)   # Set IMU sensitivity
enable_parking_mode(bool)       # Enable/disable parking mode
trigger_snap(reason)            # Capture snapshot
get_storage_info()              # Get storage usage
get_clips()                     # Get recorded clips list
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        RecordD                               │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ Loop Record │  │Impact Detect│  │ Snap Capture│         │
│  │  (DVR)      │  │  (IMU/G)    │  │  (On-demand)│         │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘         │
│         │                │                │                 │
│         └────────────────┴────────────────┘                 │
│                          │                                  │
│                   ┌──────┴──────┐                          │
│                   │  Pre-impact │  ← Always buffering       │
│                   │    Buffer   │     15s in RAM            │
│                   └──────┬──────┘                          │
│                          │                                  │
│                   ┌──────┴──────┐                          │
│                   │    MPP      │  ← Hardware H264          │
│                   │  Encoder    │                          │
│                   └─────────────┘                          │
└─────────────────────────────────────────────────────────────┘
```

## VisionPilot Feature Parity

| Feature | VisionPilot | RecordD |
|---------|-------------|---------|
| Recording modes | 5 modes | ✅ 5 modes |
| Quality presets | low/medium/high | ✅ low/medium/high |
| Impact sensitivity | 0-100 scale | ✅ 0-100 scale |
| Pre/post buffer | ✅ | ✅ |
| Parking mode | ✅ | ✅ |
| Timelapse | ✅ | ✅ |
| Clip tracking | ✅ | ✅ |
| Storage info | ✅ | ✅ |
| Manual event mark | ✅ | ✅ |
| IMU history in snap | ✅ | ✅ |
| Auto-trigger snap | ✅ | ✅ |

## Dependencies

- numpy: IMU calculations
- opencv-python: Image conversion
- ffmpeg: Video encoding (h264_rkmpp for hardware, hevc_rkmpp optional with license)
