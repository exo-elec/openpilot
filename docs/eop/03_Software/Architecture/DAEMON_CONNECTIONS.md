# Daemon Connections - Message Flow Architecture

---

## Overview

This document describes how daemons connect and communicate via **msgq** (message queue).

**Key Principle**: 
- **msgq** = For inter-daemon communication (sensors, cameras, control)
- **Library calls** = For hardware acceleration (HAL, compute)

---

## Message Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           SENSOR LAYER (system/)                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ imud     │  │ v4l2d    │  │ socketd  │  │ubloxd/   │  │bluetoothd│         │
│  │ (IMU)    │  │ (camera) │  │ (CAN)    │  │pigeond   │  │ (BLE)    │         │
│  │          │  │          │  │          │  │ (GPS)    │  │          │         │
│  └───┬──────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘         │
│      │              │             │             │             │                │
│      ▼              ▼             ▼             ▼             ▼                │
│  sensorEvents    frames       carState      gpsLocation   bluetooth            │
│  temperature     cameraState   can           gnssMeasurements                  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼ msgq
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         PERCEPTION LAYER (selfdrive/)                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐       │
│  │                         modeld                                      │       │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │       │
│  │  │  Vision     │  │  Policy     │  │   Pose      │                  │       │
│  │  │  Model      │  │  Model      │  │   Est.      │                  │       │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                  │       │
│  │         │                │                │                         │       │
│  │         └────────────────┴────────────────┘                         │       │
│  │                          │                                          │       │
│  │                    modelV2 (msgq)                                   │       │
│  └──────────────────────────┼──────────────────────────────────────────┘       │
│                             │                                                   │
│                             ▼                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐       │
│  │                        stereod                                      │       │
│  │  ┌─────────────────┐  ┌─────────────────┐                           │       │
│  │  │   SGM Depth     │  │   Depth Fusion  │                           │       │
│  │  │   (GPU/CPU)     │  │                 │                           │       │
│  │  └────────┬────────┘  └────────┬────────┘                           │       │
│  │           │                    │                                    │       │
│  │           └────────────────────┘                                    │       │
│  │                    │                                                │       │
│  │              disparity (msgq)                                       │       │
│  └────────────────────┼───────────────────────────────────────────────┘       │
│                       │                                                         │
│                       ▼                                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐       │
│  │                         gridd                                       │       │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │       │
│  │  │  Occupancy      │  │  Multi-Camera   │  │  Grid Fusion    │      │       │
│  │  │  Grid           │  │  Fusion         │  │                 │      │       │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘      │       │
│  │           │                    │                    │               │       │
│  │           └────────────────────┴────────────────────┘               │       │
│  │                               │                                     │       │
│  │                         grid (msgq)                                 │       │
│  └───────────────────────────────┼─────────────────────────────────────┘       │
│                                  │                                              │
│                                  ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐       │
│  │                         monod                                       │       │
│  │  ┌─────────────────┐  ┌─────────────────┐                           │       │
│  │  │  Object Detect  │  │  Object Track   │                           │       │
│  │  │  (NPU)          │  │                 │                           │       │
│  │  └────────┬────────┘  └────────┬────────┘                           │       │
│  │           │                    │                                    │       │
│  │           └────────────────────┘                                    │       │
│  │                    │                                                │       │
│  │           objectDetection (msgq)                                    │       │
│  └────────────────────┼───────────────────────────────────────────────┘       │
│                       │                                                         │
└───────────────────────┼─────────────────────────────────────────────────────────┘
                        │
                        ▼ msgq
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          PLANNING LAYER (selfdrive/)                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐       │
│  │                         pathd                                       │       │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │       │
│  │  │  Path Planning  │  │  OSM Fusion    │  │  Hybrid A*      │      │       │
│  │  │                 │  │                │  │                 │      │       │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘      │       │
│  │           │                    │                    │               │       │
│  │           └────────────────────┴────────────────────┘               │       │
│  │                               │                                     │       │
│  │                         navPath (msgq)                              │       │
│  └───────────────────────────────┼─────────────────────────────────────┘       │
│                                  │                                              │
└──────────────────────────────────┼──────────────────────────────────────────────┘
                                   │
                                   ▼ msgq
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          CONTROL LAYER (selfdrive/)                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐       │
│  │                        controlsd                                    │       │
│  │                                                                     │       │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐  │       │
│  │  │  modelV2    │  │  navPath    │  │   grid      │  │ carState  │  │       │
│  │  │  (input)    │  │  (input)    │  │  (input)    │  │ (input)   │  │       │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────┬─────┘  │       │
│  │         │                │                │               │        │       │
│  │         └────────────────┴────────────────┴───────────────┘        │       │
│  │                            │                                        │       │
│  │                     ┌──────┴──────┐                                 │       │
│  │                     │   Fusion    │                                 │       │
│  │                     │   & Control │                                 │       │
│  │                     └──────┬──────┘                                 │       │
│  │                            │                                        │       │
│  │                     carControl (msgq)                               │       │
│  └────────────────────────────┼────────────────────────────────────────┘       │
│                               │                                                 │
└───────────────────────────────┼─────────────────────────────────────────────────┘
                                │
                                ▼ msgq
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          OUTPUT LAYER (system/selfdrive/)                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                          │
│  │  carControl  │──┤   socketd    │──┤  Vehicle     │                          │
│  │  (consume)   │  │  (CAN send)  │  │  (actuation) │                          │
│  └──────────────┘  └──────────────┘  └──────────────┘                          │
│                                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                          │
│  │   recordd    │  │   loggerd    │  │     ui       │                          │
│  │  (DVR/save)  │  │  (log data)  │  │  (display)   │                          │
│  └──────────────┘  └──────────────┘  └──────────────┘                          │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## HAL Integration (Library Calls, NOT msgq)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    HAL USAGE (Direct Library Calls)                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────┐      ┌─────────────────────────────────────────────────────┐  │
│  │   modeld    │─────▶│  from openpilot.system.hardware import get_hal      │  │
│  │             │      │                                                     │  │
│  │  npu = hal  │      │  hal = get_hal()                                    │  │
│  │  .get_      │      │  npu = hal.get_backend(BackendType.NPU)            │  │
│  │  backend(   │      │  result = npu.infer('model', inputs)               │  │
│  │  NPU)       │      │                                                     │  │
│  └─────────────┘      └─────────────────────────────────────────────────────┘  │
│                                │                                                │
│                                │ Library call (no msgq)                         │
│                                ▼                                                │
│  ┌─────────────┐      ┌─────────────────────────────────────────────────────┐  │
│  │  stereod    │─────▶│  from openpilot.system.hardware import select_for_sgm│  │
│  │             │      │                                                     │  │
│  │  backend =  │      │  backend = select_for_sgm(width=1920, height=1080)  │  │
│  │  select_    │      │  result = backend.infer('sgm', {...})               │  │
│  │  for_sgm()  │      │                                                     │  │
│  └─────────────┘      └─────────────────────────────────────────────────────┘  │
│                                │                                                │
│                                │ Library call (no msgq)                         │
│                                ▼                                                │
│  ┌─────────────┐      ┌─────────────────────────────────────────────────────┐  │
│  │   v4l2d     │─────▶│  rga = get_hal().get_backend(BackendType.RGA)      │  │
│  │             │      │  result = rga.infer('resize', {...})                │  │
│  └─────────────┘      └─────────────────────────────────────────────────────┘  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Message Types by Daemon

### Publishers

| Daemon | Publishes | Frequency | Consumers |
|--------|-----------|-----------|-----------|
| **imud** | `accelerometer`, `gyroscope`, `temperature` | 100Hz | controlsd |
| **v4l2d** | `frame` (VisionIPC), `cameraState` | 20-30Hz | modeld, stereod, gridd, monod |
| **socketd** | `can`, `carState` | 100Hz | controlsd, loggerd |
| **ubloxd** | `gpsLocation`, `gnssMeasurements` | 10Hz | pathd, locationd |
| **bluetoothd** | `bluetooth` | Event | ui |
| **modeld** | `modelV2` | 20Hz | controlsd, ui |
| **stereod** | `disparity` | 20Hz | gridd, controlsd |
| **gridd** | `grid` | 10Hz | pathd, controlsd |
| **monod** | `objectDetection` | 10Hz | controlsd |
| **pathd** | `navPath` | 5Hz | controlsd |
| **controlsd** | `carControl` | 100Hz | socketd, ui |
| **loggerd** | `logMessage` | - | athena |

### Subscribers

| Daemon | Subscribes | Purpose |
|--------|------------|---------|
| **modeld** | `frame` (VisionIPC) | Run neural networks |
| **stereod** | `frame` (VisionIPC) | Compute stereo depth |
| **gridd** | `frame`, `disparity` | Build occupancy grid |
| **monod** | `frame` | Object detection |
| **pathd** | `grid`, `gpsLocation` | Plan path |
| **controlsd** | `modelV2`, `navPath`, `grid`, `carState` | Control vehicle |
| **socketd** | `carControl` | Send CAN commands |
| **ui** | `modelV2`, `carState`, `carControl` | Display |

---

## Service Configuration

```python
# cereal/services.py
SERVICE_LIST = {
    # Sensors
    'sensorEvents': {'frequency': 100.0},
    'temperature': {'frequency': 10.0},
    
    # Cameras
    'cameraState': {'frequency': 20.0},
    'frame': {'frequency': 20.0},  # VisionIPC
    
    # Vehicle
    'can': {'frequency': 100.0},
    'carState': {'frequency': 100.0},
    'carControl': {'frequency': 100.0},
    
    # Perception
    'modelV2': {'frequency': 20.0},
    'disparity': {'frequency': 20.0},
    'grid': {'frequency': 10.0},
    'objectDetection': {'frequency': 10.0},
    
    # Navigation
    'gpsLocation': {'frequency': 10.0},
    'navPath': {'frequency': 5.0},
    
    # System
    'logMessage': {'frequency': 0.0},  # Asynchronous
}
```

---

## Key Design Principles

1. **msgq for inter-daemon**, library calls for hardware
2. **VisionIPC** for camera frames (zero-copy shared memory)
3. **HAL** for compute acceleration (direct library calls)
4. **100Hz** for control loop (sensors, CAN, carControl)
5. **20Hz** for perception (cameras, models)
6. **5-10Hz** for planning (path, grid)

---

## See Also

- HAL.md - Hardware Abstraction Layer
- V4L2D - Camera daemon
- MODELD - Model daemon
- SOCKETD - CAN daemon
