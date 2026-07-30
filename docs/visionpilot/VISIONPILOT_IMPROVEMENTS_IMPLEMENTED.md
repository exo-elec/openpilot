# VisionPilot Improvements - Implementation Summary

## Overview
Successfully implemented VisionPilot-inspired improvements to OpenPilot's system/ directory while preserving the existing structure. Includes Hailo-8 NPU thermal monitoring for ExoPilot 02M.

---

## Changes Made

### 1. Thermal Management Enhancements (`system/thermald/`)

#### New Files:
- **`thermal_zones.py`** - Auto-discovery of thermal zones
  - Discovers zones by type name (cpu_thermal, npu_thermal, etc.)
  - Falls back to defaults if discovery fails
  - Clean abstraction for temperature reading
  - **Hailo-8 discovery support**

- **`fan_control.py`** - Hysteresis-based fan control
  - Overlapping temperature bands prevent oscillation
  - Temperature smoothing with low-pass filter
  - Proportional fan speed within bands

- **`hailo_thermal.py`** - **NEW: Hailo-8 NPU thermal monitoring**
  - Monitors Hailo-8 temperature via sysfs or HailoRT API
  - Thresholds: Normal<75°C, Elevated 75-85°C, High 85-95°C, Critical>95°C
  - Throttling detection for NPU
  - Auto-discovery of Hailo devices

#### Modified:
- **`thermald.py`** - Updated to use new modules
  - Auto-discovers thermal zones at startup
  - **Monitors Hailo-8 NPU temperature**
  - Uses hysteresis for stable fan control
  - Reports all thermal zones in thermalStatus

### 2. Logger Storage Management (`system/loggerd/`)

#### New Files:
- **`storage_policy.py`** - Comprehensive storage management
  - Size-based rotation
  - Age-based cleanup
  - Free space monitoring
  - Preserved segment handling

#### Modified:
- **`deleter.py`** - Uses StoragePolicy
  - Cleaner deletion logic
  - Better stats logging
  - Maintains backward compatibility

### 3. Health Monitoring (`system/health/` - New Module)

#### New Files:
- **`__init__.py`** - Module init
- **`health_monitor.py`** - Fault tree-based health monitoring
  - Hierarchical health nodes
  - **Hailo NPU health tracking** (thermal, inference)
  - Root cause analysis
  - Monitors thermal, panda, processes
  - Publishes `healthStatus` message

### 4. Power Monitoring (`system/power_monitor/` - New Module)

#### New Files:
- **`__init__.py`** - Module init  
- **`power_monitor.py`** - PMIC monitoring
  - Monitors 8 voltage rails (RK806S)
  - Under-voltage detection
  - Car power/ignition detection
  - Publishes `powerState` message

### 5. Process Configuration (`system/manager/`)

#### Modified:
- **`process_config.py`** - Added new processes
  - `healthd` - Health monitoring daemon
  - `powerd` - Power monitoring daemon

### 6. Cereal Message Definitions (`cereal/`)

#### Modified:
- **`log.capnp`** - Added new message types
  - `HealthStatus` struct with fault tree nodes
  - `PowerState` struct with rail monitoring
  - Event union entries @244, @245

---

## File Summary

### New Files (9)
```
system/thermald/thermal_zones.py
system/thermald/fan_control.py
system/thermald/hailo_thermal.py          # NEW
system/loggerd/storage_policy.py
system/health/__init__.py
system/health/health_monitor.py
system/power_monitor/__init__.py
system/power_monitor/power_monitor.py
```

### Modified Files (4)
```
system/thermald/thermald.py
system/loggerd/deleter.py
system/manager/process_config.py
cereal/log.capnp
```

---

## Key Features Implemented

### Thermal Management (Including Hailo-8)
| Feature | Before | After |
|---------|--------|-------|
| Zone Discovery | Hardcoded paths | Auto-discovery by type |
| SoC Thermal | CPU/GPU/NPU | CPU/GPU/NPU + **Hailo-8** |
| Fan Control | Simple threshold | Hysteresis bands |
| Temperature | Raw | Smoothed (5s tau) |
| Status | On/Off | GREEN/YELLOW/RED/DANGER |
| Hailo Thresholds | None | 75°C/85°C/95°C |

### Hailo-8 Thermal Monitoring
| Feature | Implementation |
|---------|---------------|
| Interface | Sysfs or HailoRT API |
| Auto-discovery | PCIe vendor ID (0x1e7c) + sysfs |
| Thresholds | Warning 75°C, High 85°C, Critical 95°C |
| Throttling | Automatic detection |
| Health Integration | Fault tree node |

### Storage Management
| Feature | Before | After |
|---------|--------|-------|
| Cleanup | Age only | Size + age + free space |
| Rotation | None | Size/duration based |
| Stats | Basic | Comprehensive |

### Health Monitoring
| Feature | Before | After |
|---------|--------|-------|
| Monitoring | None | Fault tree analysis |
| Root Cause | None | Automatic detection |
| Components | None | Thermal, **Hailo**, panda, processes |

### Power Monitoring
| Feature | Before | After |
|---------|--------|-------|
| PMIC | None | RK806S monitoring |
| Rails | None | 8 voltage rails |
| Under-voltage | None | Detection + logging |
| Car Power | None | Ignition detection |

---

## Testing Recommendations

1. **Thermal Zones (including Hailo)**
   ```bash
   python -c "from system.thermald.thermal_zones import discover_zones, discover_hailo_zones; print('SoC:', discover_zones()); print('Hailo:', discover_hailo_zones())"
   ```

2. **Hailo Thermal**
   ```bash
   python -c "from system.thermald.hailo_thermal import get_hailo_thermal; print(get_hailo_thermal())"
   ```

3. **Fan Control**
   ```bash
   python -c "from system.thermald.fan_control import FanController; fc = FanController(); print(fc.update(78, 1.0))"
   ```

4. **Health Monitor**
   ```bash
   python system/health/health_monitor.py
   ```

5. **Power Monitor**
   ```bash
   python system/power_monitor/power_monitor.py
   ```

---

## Backward Compatibility

All changes maintain backward compatibility:
- Existing message fields unchanged
- New messages use unused IDs (@244, @245)
- Process config adds new entries without modifying existing
- Fallback behavior for all new features
- Hailo monitoring gracefully degrades if not present

---

## ExoPilot 02M Specific Features

1. **Hailo-8 Thermal Monitoring**
   - Automatic detection of Hailo PCIe device
   - Temperature thresholds optimized for Hailo-8
   - Integration with system fan control
   - Health monitoring with fault tree

2. **NPU Thermal Zones**
   - SoC NPU (RK3588) + Hailo-8 NPU
   - Separate thresholds for each NPU type
   - Combined thermal management

---

## Next Steps

1. Build and test on ExoPilot 02M hardware
2. Verify Hailo thermal sysfs paths on actual hardware
3. Tune thermal bands for specific hardware
4. Test Hailo throttling behavior under load
