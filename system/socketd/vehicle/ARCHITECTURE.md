# socketd Vehicle Adapter Architecture

## Overview

`system/socketd/vehicle/` is the unified SocketCAN vehicle adapter using
Tesla-format CAN protocol. There is no standalone `vehicled` process — the
adapter (`Car`, from `car/card.py`) runs as a thread inside the single
`socketd` daemon (`system/socketd/socketd.py`), alongside the SocketCAN
bridge. It replaces the Panda-dependent generic `card.py` path while using
the pinned OpenDBC submodule as the shared protocol/model source. The
BrownPanda gateway remains the hardware safety gateway (v1 = TC275, v2 = TC375).

## Safety Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     SAFETY LAYERS                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Layer 1 (Software): socketd vehicle adapter safety              │
│  - Location: system/socketd/vehicle/safety/ (shim) →            │
│    system/socketd/safety/tesla_safety.py (canonical)            │
│  - Limits: TIGHTER (80% of Panda limits)                        │
│  - Purpose: Catch bugs, enforce smooth control                  │
│  - Runs on: RK3588/RK3576 A76 cores                             │
│                                                                  │
│  Layer 2 (Hardware): BrownPanda Gateway                          │
│  - Location: External microcontroller                           │
│  - Limits: LOOSER (100% of Panda limits)                        │
│  - Purpose: Safety net, hardware enforcement                    │
│  - Runs on: BrownPanda v1 (TC275) or v2 (TC375)                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Component Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                system.socketd.socketd (single process)          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌───────────────────┐              ┌────────────────────────┐ │
│  │  candevice /       │              │  vehicle.Car (thread)  │ │
│  │  canbridge         │              │                        │ │
│  │  (system/socketd/  │   'can'      │  ┌────────┐ ┌────────┐ │ │
│  │  socketd.py)       │─────────────▶│  │CarState│ │CarCtrl │ │ │
│  │                    │              │  └───┬────┘ └───┬────┘ │ │
│  │  - SocketCAN RX/TX │◀─────────────│      │          │      │ │
│  │  - 'can'/'sendcan' │  'sendcan'   │      ▼          ▼      │ │
│  │    bridge, no      │              │  ┌────────────────┐   │ │
│  │    safety checks   │              │  │ SafetyManager  │   │ │
│  │                    │              │  │ (TeslaSafety)  │   │ │
│  └────────────────────┘              │  └────────────────┘   │ │
│                                       └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  BrownPanda Gateway (Hardware)                  │
│                     Layer 2 Safety                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  - Receives CAN from socketd                                    │
│  - Applies LOOSER safety limits                                 │
│  - Forwards to vehicle EPS/ACC                                  │
│  - Hardware-enforced safety net                                 │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Tesla Vehicle (EPS/ACC)                     │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### RX Path (Vehicle → openpilot)
```
Tesla CAN → socketd bridge → 'can' topic → vehicle.Car (safety RX processing) → CarState → 'carState' topic
```

### TX Path (openpilot → Vehicle)
```
'carControl' topic → vehicle.Car (CarController) → SafetyManager (Safety Check) → 'sendcan' topic → socketd bridge → BrownPanda → Tesla
```

## Key Files

| File | Purpose |
|------|---------|
| `car/card.py` | `Car` — main daemon class, started as a thread by `system/socketd/socketd.py` |
| `car/carstate.py` | `CarState` — parses CAN, tracks vehicle state |
| `car/carcontroller.py` | `CarController` — generates CAN commands |
| `car/cruise.py` | `VCruiseHelper` — cruise-set-speed state machine |
| `car/events.py` | `VehicleEvents` — Tesla-specific event handling |
| `car/vehicle_model.py` | Vehicle dynamics model |
| `tesla/tesla_parser.py` | Tesla CAN message parser |
| `tesla/continental_interface.py` | Continental radar (party-bus) CAN interface |
| `tesla/values.py` | `VEHICLE`/`PlatformConfig` (EOP chassis presets) + `CarControllerParams` (steering fields EOP-owned; `CANBUS` and `ACCEL_MIN/MAX`/`JERK_LIMIT_MIN/MAX` re-exported from `opendbc.car.tesla.values`, see `MIGRATION_SUMMARY.md`) |
| `safety/safety.py` | Compatibility shim re-exporting `system/socketd/safety/tesla_safety.py` |
| `safety/safety_manager.py` | `SafetyManager` — wraps `TeslaSafety` for the CAN bridge |

## Safety Limits (Layer 1 - TIGHTER)

| Parameter | Value | Panda Original | Ratio |
|-----------|-------|----------------|-------|
| MAX_STEERING_ANGLE | 270° | 360° | 75% |
| MAX_STEERING_RATE | 20°/s | 25°/s | 80% |
| MAX_ANGLE_ERROR | 24° | 30° | 80% |
| MAX_ACCEL | ~1.6 m/s² | 2.0 m/s² | 80% |
| MIN_ACCEL | ~-2.8 m/s² | -3.48 m/s² | 80% |
| HEARTBEAT_TIMEOUT | 200ms | 250ms | 80% |
| DRIVER_TORQUE | 2.0 Nm | 2.5 Nm | 80% |

## Removed Dependencies

- `opendbc_repo`'s generic multi-brand dispatch (`car_helpers.get_car()`, Panda
  fingerprinting/handshake) — not used; this fork is Tesla-only and has no
  Panda to fingerprint against. What EOP *does* reuse directly from the
  pinned OpenDBC submodule (shared commit with `dev/NGP10`): `CarState`/
  `CarController`/`RadarInterface` (via thin wrappers in `car/carstate.py`,
  `car/carcontroller.py`, `car/card.py`), and `CANBUS`/accel-jerk limits (via
  `tesla/values.py`, see `MIGRATION_SUMMARY.md`).
- `panda` — Safety moved to this software layer + BrownPanda gateway (hardware).

## Process Configuration

```python
# system/manager/process_config.py
PythonProcess("socketd", "system.socketd.socketd", always_run),
```

There is no separate `vehicled` process entry. `system.socketd.socketd.SocketD.start()`
imports `system.socketd.vehicle.Car` and runs it on its own thread
(`Car.card_thread()`) inside the same process as the CAN bridge.

## Migration Notes (historical)

### From opendbc/car
- Car interfaces moved to `system/socketd/vehicle/car/`.
- Tesla-specific only (no multi-brand support).
- `VehicleModel` ported to `system/socketd/vehicle/car/vehicle_model.py`.

### From panda/board/safety
- Safety logic moved to `system/socketd/safety/tesla_safety.py` (canonical;
  `system/socketd/vehicle/safety/safety.py` re-exports it).
- Runs as software on RK3588 instead of STM32.
- Tighter limits than original Panda.

### From selfdrive/vehicled (removed)
- `selfdrive/vehicled/` was renamed in-place to `system/socketd/vehicle/` and
  its standalone `vehicled.py` process wrapper was deleted — the daemon now
  runs only as `system.socketd.socketd`.
