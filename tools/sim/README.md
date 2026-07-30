openpilot in simulator
=====================

openpilot implements a [bridge](run_bridge.py) that allows it to run in simulators.

Supported simulators:
- **[CARLA](https://carla.org/)** — high-fidelity, realistic physics and sensors (primary)
- **MetaDrive** — lightweight procedural-track simulator (`--simulator metadrive`, no GPU required)

## Launching openpilot

First, start openpilot:
```bash
# Run locally
./tools/sim/launch_openpilot.sh
```

## CARLA Bridge

### 1. Start CARLA Server

Using Docker (recommended):
```bash
./tools/sim/start_carla.sh [VERSION]
# Examples:
./tools/sim/start_carla.sh              # defaults to 0.9.16
./tools/sim/start_carla.sh 0.9.15       # specific version
QUALITY=Low ./tools/sim/start_carla.sh  # low quality for limited hardware
```

Or using local CARLA installation:
```bash
cd /path/to/carla
./CarlaUE4.sh -quality-level=Low -RenderOffScreen -fps=20
```

### 2. Run openpilot CARLA bridge

```bash
python tools/sim/run_bridge.py --dual_camera
```

Options:
```
--host HOST                  CARLA server host (default: 127.0.0.1, env: CARLA_HOST)
--port PORT                  CARLA server port (default: 2000, env: CARLA_PORT)
--town TOWN                  CARLA map to load (default: Town04_Opt)
--spawn_point N              Spawn point index (default: 16)
--tele_camera                Enable tele road camera (EOP ExoPilot 02M)
--stereo_camera              Enable stereo cameras (stereod VisionIPC)
--side_camera                Enable side cameras (blind spot, ExoPilot 01M & 02)
--high_quality               Enable high-quality rendering
```

### Example: full command for ExoPilot testing

```bash
# Terminal 1: openpilot
./tools/sim/launch_openpilot.sh

# Terminal 2: CARLA server
./tools/sim/start_carla.sh

# Terminal 3: bridge
python tools/sim/run_bridge.py --simulator carla --dual_camera --tele_camera --town Town04_Opt
```

Or use the one-command launcher (sets params and starts everything):
```bash
./tools/sim/launch_carla_sim.sh [scenario] [weather] [platform]
# Examples:
./tools/sim/launch_carla_sim.sh                    # free drive, clear_day, rk3576
./tools/sim/launch_carla_sim.sh emergency_brake rain rk3576
./tools/sim/launch_carla_sim.sh pedestrian_crossing clear_day rk3588
```

### Testing on limited hardware

If your PC doesn't have a strong GPU, CARLA can still run for integration testing:

```bash
# Lowest possible settings
QUALITY=Low RENDER_MODE=-RenderOffScreen ./tools/sim/start_carla.sh 0.9.15

# Or with local install
./CarlaUE4.sh -quality-level=Low -RenderOffScreen -nosound -fps=10
```

The bridge will still connect and publish messages; frame rates will be low but sufficient for testing the selfdrive stack integration.

### Unit Testing (No CARLA Required)

Run the standalone test suite to validate all Python-side logic without a CARLA server:

```bash
# 65 tests — BSD fusion, geometry, scenarios, weather, mock CARLA queries
python tools/sim/tests/run_tests.py
```

For full workstation setup instructions, see [TESTING.md](TESTING.md).

## Bridge Controls

- To engage openpilot press `2`, then press `1` to increase the speed and `2` to decrease.
- To disengage, press `S` (simulates a user brake)

#### All inputs:

```
| key  |   functionality       |
|------|-----------------------|
|  1   | Cruise Resume / Accel |
|  2   | Cruise Set    / Decel |
|  3   | Cruise Cancel         |
|  r   | Reset Simulation      |
|  i   | Toggle Ignition       |
|  q   | Exit all              |
| wasd | Control manually      |
```

## EOP Camera Support in Simulation

| Camera | Simulator Support | Notes |
|--------|------------------|-------|
| road | ✅ Both | Main forward camera (40° FOV) |
| wide_road | ✅ Both | Wide-angle camera (120° RK3588 / 180° RK3576) |
| tele_road | ✅ CARLA only | Long-range zoom (40° FOV), RK3576 only |
| stereo_left/right | ✅ CARLA only | 80 mm baseline (RK3588) or 160 mm (RK3576), use `--stereo_camera` |
| side_left/right | ✅ CARLA only | Blind-spot UVC cameras, ExoPilot 01M & 02, use `--side_camera` |

### Platform-Specific Camera Geometry

The simulation respects `EOPSimPlatform` (set via UI or param):

**RK3588 (ExoPilot 01M)** — selectable in EOP Settings panel:
- road @ −40 mm, wide_road @ +40 mm
- stereo_left @ −40 mm, stereo_right @ +40 mm (80 mm baseline)
- No tele_road or side cameras

**RK3576 (ExoPilot 02M)** — selectable in EOP Settings panel:
- road @ 0 mm, wide_road @ +80 mm, tele_road @ −80 mm
- stereo_left @ −80 mm, stereo_right @ +80 mm (160 mm baseline)
- side_left @ +850 mm, side_right @ −850 mm (hood fender, rear-pointing 120°)

For stereo depth testing in sim, enable `--stereo_camera`. For blind-spot / adjacent-lane testing, enable `--side_camera` (requires RK3576 platform).

---

## Autoware-Style Feature Testing

The CARLA bridge supports targeted scenario spawning for ADAS validation
(similar to Autoware's scenario_test_runner).

### Traffic Light Testing (TLSC)

When `EOPTLSCEnabled` is true, the bridge queries CARLA traffic lights near
the ego vehicle and publishes their states as `stereoObjects`. This lets the
Traffic Light Speed Controller react to red/yellow lights without a real
traffic-light classifier.

```bash
# Enable TLSC in openpilot, then run bridge
params put EOPTLSCEnabled 1
python tools/sim/run_bridge.py --simulator carla --dual_camera
```

### Scenario Spawner

Set `EOPSimScenario` to spawn specific test scenarios:

| Scenario | Description | Validates |
|----------|-------------|-----------|
| `pedestrian_crossing` | Pedestrian walks across ego path | AEB, object detection |
| `cut_in` | Vehicle merges into ego lane | BSD, LCA, side cameras |
| `emergency_brake` | Lead vehicle hard-brakes after 5s | Longitudinal control, AEB |
| `cyclist_overtake` | Cyclist in bike lane ahead | Safe passing, lateral control |

```bash
# Example: test emergency braking
params put EOPSimScenario emergency_brake
python tools/sim/run_bridge.py --simulator carla --dual_camera
```

### Camera-Based Blind Spot Monitoring

When side cameras are enabled, the bridge publishes simulated `sideDetections`
from CARLA ground truth. `bsd.py` fuses these with radar data for robust
blind-spot monitoring **without LiDAR** — an Autoware-style surround-monitoring
feature using cameras only.

```bash
# RK3576 platform with side cameras
params put EOPSimPlatform rk3576
params put EOPSideCamerasEnabled 1
python tools/sim/run_bridge.py --simulator carla --dual_camera --side_camera
```

### Weather Testing

Dynamic weather presets let you test perception and control under varying conditions:

```bash
params put EOPSimWeather rain
# Options: clear_day, clear_night, rain, heavy_rain, fog, overcast
```

Weather is applied automatically by `WeatherController` each time the bridge ticks.
