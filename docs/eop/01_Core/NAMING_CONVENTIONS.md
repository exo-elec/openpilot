# EOP Naming Conventions

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |

---


## Overview

This document defines the authoritative naming standards for EnhancedOpenPilot (EOP) files, classes, and processes. These conventions ensure consistency with OpenPilot upstream while accommodating EOP's RK3588-specific architecture.

---

## 1. Daemon Naming (`*d` Suffix)

### Rule: The `d` Suffix Indicates a Daemon

A **daemon** is a long-running background service with these characteristics:
- Continuous event loop (typically with `Ratekeeper`)
- Publishes/subscribes cereal messages
- Managed by `system.manager`
- Runs for the lifetime of the system or driving session

### ✅ Correct Daemon Naming

| File | Class | Process Name | Registration |
|------|-------|--------------|--------------|
| `pathd.py` | `PathD` | `pathd` | `PythonProcess("pathd", "selfdrive.pathd.pathd", ...)` |
| `gridd.py` | `GridD` | `gridd` | `PythonProcess("gridd", "selfdrive.gridd.gridd", ...)` |
| `socketd.py` | `SocketD` | `socketd` | `PythonProcess("socketd", "system.socketd.socketd", ...)` |
| `v4l2d.py` | `V4L2D` | `v4l2d` | `PythonProcess("v4l2d", "system.v4l2d.v4l2d", ...)` |
| `recordd.py` | `RecordD` | `recordd` | `PythonProcess("recordd", "selfdrive.recordd.recordd", ...)` |
| `bluetoothd.py` | `BluetoothD` | `bluetoothd` | `PythonProcess("bluetoothd", "system.bluetoothd.bluetoothd", ...)` |

`v4l2d` provides the VisionIPC server for front-facing camera frames consumed by `modeld`, `monod`, and `ui`.

### ❌ Incorrect Daemon Naming

```python
# Wrong: Missing 'D' suffix in class name
class Path:  # Should be PathD
    pass

# Wrong: File doesn't end with 'd'
# path.py  # Should be pathd.py for a daemon

# Wrong: Process name doesn't match file
PythonProcess("vision", "selfdrive.gridd.gridd", ...)  # Should be "gridd"
```

---

## 2. Non-Daemon Exceptions (NO `d` Suffix)

These process types do NOT use the `d` suffix:

| Type | Examples | Rationale |
|------|----------|-----------|
| **UI Applications** | `ui` | User-facing Qt application, not a background service |
| **Batch Utilities** | `deleter`, `uploader` | Run periodically, exit when done |
| **External Control** | `steamd` | Single source of external vehicle control (VR teleop + joystick + keyboard) |
| **One-shot Scripts** | `calibrate_stereo.py` | Single execution, no event loop |

² `steamd` subsumes the old `joystickd` debug tool. All external control flows through SteamD.

---

## 3. The Class-D Pattern (Mandatory)

All Python daemons MUST implement the Class-D pattern:

```python
#!/usr/bin/env python3
"""Module docstring explaining daemon purpose."""

from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog

class MyDaemonD:
    """
    Main daemon class ending with capital D.
    
    Responsibilities:
    - Initialize hardware/resources in __init__
    - Run main loop in run()
    - Cleanup in stop() or __del__
    """
    
    def __init__(self):
        """Initialize hardware, allocate resources."""
        self.rk = Ratekeeper(20, print_delay_threshold=None)
        self.running = False
        
    def run(self):
        """Main daemon loop. Blocks until stop()."""
        self.running = True
        while self.running:
            # Process messages, update state
            self.rk.keep_time()
            
    def stop(self):
        """Graceful shutdown. Called on SIGTERM."""
        self.running = False
        # Release hardware, close file descriptors

def main():
    """Entry point - instantiate and run daemon."""
    daemon = MyDaemonD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.stop()

if __name__ == "__main__":
    main()
```

---

## 4. Process Registration Compatibility

### Standard Registration (Name Matches File)

```python
# File: selfdrive/pathd/pathd.py
# Class: PathD
# Process name matches implementation:
PythonProcess("pathd", "selfdrive.pathd.pathd", only_onroad)
```

### Compatibility Registration (Name ≠ File)

Use when the daemon replaces an upstream component:

```python
# v4l2d provides VisionIPC server "v4l2d" for front camera frames
PythonProcess("v4l2d", "system.v4l2d.v4l2d", camera_on)

# socketd replaces pandad for SocketCAN
# (Note: pandad completely removed in EOP, socketd is new)
```

**Rules for compatibility naming:**
1. **VisionIPC server names** should match the publishing daemon name (e.g., `v4l2d` for all MIPI CSI cameras, `uvcd` for USB cameras)
2. **Log headers** should use the canonical name for downstream tools
3. **Document the mapping** in daemon's design doc

---

## 5. File Naming Reference

### Python Source Files

| Pattern | Example | Usage |
|---------|---------|-------|
| `*_d.py` | `pathd.py`, `gridd.py` | Python daemons (Class-D pattern) |
| `*.py` | `ui.py`, `deleter.py` | Utilities, UI apps, libraries |
| `*_test.py` | `test_pathd.py` | Unit tests |

### Directory Naming

| Pattern | Example | Usage |
|---------|---------|-------|
| `*_d/` | `pathd/`, `gridd/`, `v4l2d/` | Daemon package directories |
| No suffix | `ui/`, `debug/`, `assets/` | Non-daemon modules |

### Documentation Files

| Pattern | Example | Usage |
|---------|---------|-------|
| `ALL_CAPS_D.md` | `PATHD.md`, `V4L2D.md` | Daemon design documents |
| `ALL_CAPS.md` | `HAL.md`, `OVERVIEW.md` | System architecture docs |

---

## 6. Controllers vs Daemons

A common source of confusion: **DLAT, DLON, ALCC, SOC, RED** are NOT daemons. They are **controllers** that run *inside* `controlsd`.

### Key Differences

| Aspect | Daemon (`pathd`) | Controller (`VTSC`) |
|--------|------------------|---------------------|
| **Process** | Separate process | Runs inside `controlsd` or `plannerd` |
| **Event Loop** | Own `Ratekeeper` loop | Called by parent process loop |
| **Messaging** | Publishes/subscribes directly | Uses parent messaging |
| **File** | `*_d.py` | `*.py` (no suffix) |
| **Class** | `PathD` | `VTSCController` or `TJAController` |
| **Parameters** | N/A | `EOP*` prefix (see Section 11) |
| **Docs** | `PATHD.md` | `VTSC.md`, `TJA.md`, etc. |

### Controller Naming Standards

Based on analysis of reference forks:
- **FrogPilot**: Mixed naming, inconsistent (`CurveSpeedController`, `FrogPilotAcceleration`)
- **dragonpilot**: Consistent `dp_` prefix for params, clean class names
- **sunnypilot**: Plain English (hard to distinguish from upstream)

**EOP Decision:**

| Element | Pattern | Example | Rationale |
|---------|---------|---------|-----------|
| **Parameters** | `EOP<Feature><Param>` | `EOPTJAEnabled`, `EOPVTSCEnabled` | Clear identification in database |
| **File Name** | `<feature>.py` | `tja.py`, `vtsc.py` | Clean, no redundant prefix |
| **Class Name** | `<Feature>Controller` or `<Feature>` | `TJA`, `VTSC` | Module location indicates EOP origin |
| **Functions** | `snake_case` | `apply_ramp()`, `calculate_speed()` | Python convention |

### Controller File Organization

```
selfdrive/controls/lib/
├── longcontrol.py          # Stock + TJA modification
├── longitudinal_planner.py # Stock + VTSC integration
├── desire_helper.py        # Stock + LCA enhancement
├── tja.py                  # NEW: TJA class
├── vtsc.py                 # NEW: VTSC class
├── dlat.py                 # NEW: DLAT class
├── dlon.py                 # NEW: DLON class
└── ...
```

**Note:** Controllers are imported by `controlsd` or `plannerd`, not run as separate processes.

### Implementation Status

| Controller | Module | Status |
|------------|--------|--------|
| DLAT | `selfdrive/controls/lib/dlat.py` | ✅ Implemented |
| DLON | `selfdrive/controls/lib/dlon.py` | ✅ Implemented |

---

## 7. Complete Feature Inventory

### Core Vision Features (Controllers - inside `controlsd` or planners)

| Feature | Doc File | Code Location | Description |
|---------|----------|---------------|-------------|
| **ALCC** | `ALCC.md` | `controlsd` | Always-On Lateral Centering Control |
| **DLAT** | `DLAT.md` | `controls/lib` | Dynamic Lateral Profile switching |
| **DLON** | `DLON.md` | `longitudinal_planner` | Dynamic Longitudinal Profile switching |
| **SOC** | `SOC.md` | `pathd/soc.py` | Smart Offset Control (truck nudge) |
| **RED** | `RED.md` | `controls/lib` | Road Edge Detection guardrail |
| **VTSC** | `VTSC.md` | `longitudinal_planner` | Vision Turn Speed Control (0-250m) |
| **MTSC** | `MTSC.md` | `longitudinal_planner` | Map Turn Speed Control (250-500m) |
| **TJA** | `TJA.md` | `longcontrol` | Traffic Jam Assist (smooth start) |

### Enhanced Vision Features (Daemons - standalone processes)

| Feature | Doc File | Process | Description |
|---------|----------|---------|-------------|
| **STEREO** | `STEREOD.md` | Pipeline | Overall stereo vision system |
| **GridD** | `GRIDD.md` | `gridd` | Perception & occupancy grid |
| **PathD** | `PATHD.md` | `pathd` | Policy & trajectory fusion |
| **RecordD** | `RECORDD.md` | `recordd` | DVR ring-buffer recording |

---

## 8. Complete Daemon Inventory

### Current EOP Daemons (Ending with `d`)

| Daemon | File | Class | Purpose | Rate |
|--------|------|-------|---------|------|
| `controlsd` | `selfdrive/controls/controlsd.py` | `ControlsD` | Lateral/longitudinal control | 100 Hz |
| `modeld` | `selfdrive/modeld/modeld.py` | `ModelD` | Driving neural network | 20 Hz |
| `gridd` | `selfdrive/gridd/gridd.py` | `GridD` | Stereo vision + occupancy grid | 20 Hz |
| `pathd` | `selfdrive/pathd/pathd.py` | `PathD` | Path planning + collision avoidance | 20 Hz |
| `recordd` | `selfdrive/recordd/recordd.py` | `RecordD` | DVR ring-buffer recording | Always |
| `radar3d` | `selfdrive/controls/radar3d.py` | `RadarD` | Car OEM CAN radar (ACC) | 20 Hz |
| `radar4d` | `selfdrive/controls/radar4d.py` | `Radar4DD` | BGT60TR13C 4D short-range → gridd | 20 Hz |
| `plannerd` | `selfdrive/controls/plannerd.py` | `PlannerD` | Longitudinal planning | 20 Hz |
| `imud` | `system/imud/imud.py` | `ImuD` | IMU sensor polling | 100 Hz |
| `socketd` | `system/socketd/socketd.py` | `SocketD` | SocketCAN bridge | Always |
| `v4l2d` | `system/v4l2d/v4l2d.py` | `V4L2D` | Camera capture (VisionIPC server `v4l2d`) | 20 Hz |
| `pigeond` | `system/ubloxd/pigeond.py` | `PigeonD` | GPS driver | Always |
| `bluetoothd` | `system/bluetoothd/bluetoothd.py` | `BluetoothD` | BLE SPP server, NavPilot NCP v4.1 | Always |
| `hardwared` | `system/hardware/hardwared.py` | `HardwareD` | Thermal/power management | 2 Hz |
| `calibrationd` | `selfdrive/locationd/calibrationd.py` | `CalibrationD` | Camera calibration | On-road |
| `locationd` | `selfdrive/locationd/locationd.py` | `LocationD` | Localization | 20 Hz |
| `paramsd` | `selfdrive/locationd/paramsd.py` | `ParamsD` | Parameter learning | 20 Hz |
| `torqued` | `selfdrive/locationd/torqued.py` | `TorqueD` | Torque estimation | 20 Hz |
| `selfdrived` | `selfdrive/selfdrived/selfdrived.py` | `SelfdriveD` | Self-drive state machine | 20 Hz |
| `card` | `selfdrive/vehicled/car/card.py` | `CarD` | Car interface (Tesla-only) | 100 Hz |
| `soundd` | `selfdrive/ui/soundd.py` *(not implemented)* | `SoundD` | Audio feedback | On-road |

### Non-Daemon Processes (NO `d` Suffix)

| Process | File | Type | Purpose |
|---------|------|------|---------|
| `ui` | `selfdrive/ui/ui.py` | UI App | Qt5 user interface |
| `deleter` | `system/loggerd/deleter.py` | Batch Utility | Log file cleanup |
| `uploader` | `system/loggerd/uploader.py` | Batch Utility | Log upload to cloud |

---

## 8. Migration Guide

### Adding a New Daemon

1. **Create directory**: `mkdir system/mydaemon_d` or `selfdrive/mydaemon_d` *(not implemented)*
2. **Create file**: `touch system/mydaemon_d/mydaemond.py`
3. **Implement Class-D**: `class MyDaemonD:` with `run()` and `stop()`
4. **Add to process_config.py**:
   ```python
   PythonProcess("mydaemond", "system.mydaemon_d.mydaemond", condition_fn)
   ```
5. **Create documentation**: `docs/eop/system/MYDAEMOND.md`

### Renaming an Existing Daemon

1. Update file name: `oldname.py` → `newnamed.py`
2. Update class name: `OldName` → `NewNameD`
3. Update `process_config.py` registration
4. Update documentation filename
5. Update any hardcoded references in tests

---

## 9. Common Pitfalls

| Pitfall | Example | Fix |
|---------|---------|-----|
| Missing `D` in class | `class Path:` | `class PathD:` |
| Missing `d` in file | `path.py` | `pathd.py` |
| Inconsistent registration | `PythonProcess("path", "selfdrive.pathd.pathd", ...)` | Use `"pathd"` as name |
| UI app with `d` suffix | `uide.py` | Should be `ui.py` |
| Doc without `D` | `PATH.md` | Should be `PATHD.md` for daemons |

---

## 10. Quick Reference Card

```
DECISION TREE: What naming to use?

Is it a standalone process?
├── YES → Is it a continuous loop service?
│   ├── YES → DAEMON
│   │   ├── File:     pathd.py
│   │   ├── Class:    PathD
│   │   ├── Process:  pathd
│   │   └── Docs:     PATHD.md
│   │
│   └── NO → UTILITY
│       ├── File:     deleter.py
│       ├── Class:    Deleter (no D suffix)
│       ├── Process:  deleter
│       └── Docs:     DELETER.md (or no doc)
│
└── NO → Runs inside another process?
    ├── Controller (inside controlsd)
    │   ├── File:     dlat.py
    │   ├── Class:    DLAT
    │   └── Docs:     DLAT.md
    │
    └── Library/Helper
        ├── File:     latcontrol_torque.py
        ├── Class:    LatControlTorque
        └── Docs:     (inline or LATCONTROL.md)
```

---

## References

- CONVENTIONS.md — Project-wide conventions
## 11. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Class-D Pattern | ✅ Done | Unified daemon class structure. |
| File Naming (*d.py) | ✅ Done | Standardized background services. |
| Doc Naming (CAPS_D) | ✅ Done | Verified for all EOP daemons. |
| Controller Distinction | ✅ Done | DLAT/DLON/SOC clearly decoupled from daemons. |
| Audit (2026-03-15) | ✅ Done | All current EOP files comply with standards. |
