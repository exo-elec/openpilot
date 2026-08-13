# System Daemon Architecture

## Clean Separation of Responsibilities

### Overview

This document describes the consolidated daemon architecture following OpenPilot's pattern of minimal processes with clear responsibilities.

**Key Principle**: System daemons provide HAL (Hardware Abstraction Layer), SelfDrive daemons implement algorithms.

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────────────────┐
│  APPLICATION LAYER (selfdrive/)                                         │
│  - modeld, controlsd, voiced, waked, etc.                               │
│  - Algorithms, perception, planning, control                            │
│  - Use HAL via cereal messages                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼ (cereal/msgq)
┌─────────────────────────────────────────────────────────────────────────┐
│  HAL LAYER (system/) - Daemons                                          │
│  - Hardware abstraction and device drivers                              │
│  - Publish sensor data, accept commands                                 │
│  - BSP-specific implementations for RK3588                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼ (sysfs, I2C, SPI, etc.)
┌─────────────────────────────────────────────────────────────────────────┐
│  HARDWARE (BSP) - Linux Kernel                                          │
│  - RK3588 SoC drivers                                                   │
│  - Sensors, cameras, PMIC, etc.                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## System Daemons (HAL Layer)

### Foundational Daemons (Always Running)

| Daemon | Hardware | Messages | Responsibility |
|--------|----------|----------|----------------|
| **logmessaged** | - | `logMessage` | System logging |
| **stated** | GPIO ignition | `vehicleState` | Vehicle state, parking mode |
| **thermald** | Thermal zones | `thermalStatus` | Thermal monitoring, fan control |
| **hardwared** | PMIC RK806S | `powerState` | Power monitoring, BSP config |
| **wdgd** | /dev/watchdog | `wdgState` | Hardware watchdog |

### I/O Device Daemons (HAL)

| Daemon | Hardware | Interface | Messages |
|--------|----------|-----------|----------|
| **v4l2d** | Cameras (MIPI CSI) | V4L2 | `frame` (VisionIPC) |
| **imud** | LSM6DS3 | I2C | `sensorEvents` |
| **micd** | Microphone | I2S | `audio` |
| **spkd** | Speaker | I2S | - (accepts commands) |
| **rtcd** | PCF8563 | I2C | `clock` |
| **pigeond** | u-blox GPS | UART | `gpsLocation` |
| **rtkd** | NTRIP RTK | Network | `gnssMeasurements` |
| **bluetoothd** | BT module | HCI/USB/UART | `bluetooth` |
| **networkd** | WiFi + EC25 4G | NetworkManager | `networkState` |
| **socketd** | CAN + TC275 | SocketCAN | `can`, `carState` |

### Storage Daemons

| Daemon | Hardware | Responsibility |
|--------|----------|----------------|
| **loggerd** | eMMC/SD | Data logging |
| **deleter** | eMMC/SD | Log rotation |
| **uploader** | Network | Data upload |
| **mcapd** | eMMC/SD | MCAP format logging |

---

## SelfDrive Daemons (Application Layer)

### Perception

| Daemon | Input | Output | HAL Dependencies |
|--------|-------|--------|------------------|
| **modeld** | Camera frames | `modelV2` | v4l2d |
| **stereod** | Stereo pair | `disparity` | v4l2d |
| **gridd** | Cameras, depth | `grid` | v4l2d |
| **monod** | Camera | `objectDetection` | v4l2d |
| **surfaced** | Stereo depth | `surface` | v4l2d, stereod |

### Planning & Control

| Daemon | Input | Output |
|--------|-------|--------|
| **pathd** | Grid, GPS | `navPath` |
| **controlsd** | Model, path | `carControl` |
| **selfdrived** | Vehicle state | Engagement state |
| **plannerd** | Model, radar | Trajectory |

### Voice & AI (Optional)

| Daemon | Input | Output | HAL Dependencies |
|--------|-------|--------|------------------|
| **waked** | Audio | Wake events | micd |
| **voiced** | Audio | `voiceIntent` | micd |
| **soundd** | - | Audio output | spkd |

### Other

| Daemon | Purpose |
|--------|---------|
| **recordd** | DVR recording |
| **tripd** | Trip statistics |
| **mapd** | OSM map data |
| **navd** | Navigation |

---

## Hardware Abstraction (BSP Pattern)

### Platform Detection

```python
# system/hardware/registry.py
from openpilot.system.hardware import HARDWARE

# Auto-detects platform:
# - RK3588Hardware for ExoPilot 01M
```

### BSP Configuration

| Platform | WiFi | BT | Cameras | NPU |
|----------|------|-----|---------|-----|
| **RK3588** | RTL8822CE (PCIe) | RTL8822CE (USB) | 4 cameras | SoC only |

### HAL Usage in Daemons

```python
# Example: networkd using BSP config
from openpilot.system.hardware import HARDWARE

wifi_chip = HARDWARE.WIFI_CHIP      # "RTL8822CE" or "AP6275P"
wifi_interface = HARDWARE.WIFI_INTERFACE  # "wlan0"
```

---

## Consolidation Summary

### Removed Redundant Daemons

| Before | After | Reason |
|--------|-------|--------|
| `healthd` | Removed | Redundant with selfdrived events |
| `suspend_manager` | Removed | Merged into stated |
| `thermal_protection` | Merged | Now in thermald |
| `power_monitor` | Merged | Now in hardwared |

### Final Daemon Count

| Category | Count | Daemons |
|----------|-------|---------|
| Foundational | 5 | logmessaged, stated, thermald, hardwared, wdgd |
| I/O Device | 12 | v4l2d, imud, micd, spkd, rtcd, pigeond, rtkd, bluetoothd, networkd, socketd, obd2d, adaptd |
| Storage | 4 | loggerd, deleter, uploader, mcapd |
| **Total System** | **20** | All HAL daemons |

---

## Message Flow Example

```
┌─────────┐     ┌─────────┐     ┌─────────┐
│  imud   │────▶│ sensor  │────▶│controlsd│
│ (I2C)   │     │ Events  │     │ (fusion)│
└─────────┘     └─────────┘     └────┬────┘
                                     │
┌─────────┐     ┌─────────┐         │
│  v4l2d  │────▶│  frame  │─────────┤
│ (V4L2)  │     │(Vision) │         │
└─────────┘     └────┬────┘         │
                     │              │
                     ▼              │
               ┌─────────┐          │
               │ modeld  │──────────┘
               │  (NPU)  │
               └─────────┘
```

---

## See Also

- HAL.md - Hardware Abstraction Layer details
- [DAEMON_CONNECTIONS.md](DAEMON_CONNECTIONS.md) - Message flow diagrams
- RK3588 pinout — private ExoPilot HAL documentation (`exopilot/docs/02-HARDWARE/RK3588_PINS.md`)
