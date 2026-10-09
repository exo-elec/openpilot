# CARLA Simulation Testing Guide

This document describes how to test openpilot/EOP features in CARLA simulation.

## Quick Start (Notebook / CI)

Run the standalone test suite (no CARLA server, no GPU required):

```bash
cd ~/openpilot
python tools/sim/tests/run_tests.py
```

Expected output: **65 passed, 0 failed**

This validates:
- BSD radar+camera fusion logic
- Side-camera BEV geometry
- Scenario spawner configuration
- Weather preset definitions
- Mock CARLA ground-truth queries (SimulatedSideD, TrafficLightPublisher)

## Workstation Setup (Full Simulation)

### Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| GPU | NVIDIA GTX 1080 (8 GB) | NVIDIA RTX 3090 (24 GB) |
| CPU | 6 cores | 8+ cores (e.g. i5-13600K) |
| RAM | 16 GB | 32+ GB |
| Storage | 20 GB SSD | 50 GB NVMe |

### 1. Install CARLA 0.9.15

```bash
# Download from https://github.com/carla-simulator/carla/releases
cd /opt
tar -xvzf CARLA_0.9.15.tar.gz
cd CARLA_0.9.15

# Install Python API
pip install PythonAPI/carla/dist/carla-0.9.15-cp310-cp310-linux_x86_64.whl
```

### 2. Install openpilot Dependencies

```bash
cd ~/openpilot
./tools/op.sh setup
# Or manually:
pip install -r requirements.txt
```

### 3. Launch CARLA Server

```bash
cd /opt/CARLA_0.9.15
# Low quality (headless testing)
./CarlaUE4.sh -quality-level=Low -RenderOffScreen -nosound

# Or medium quality with display
./CarlaUE4.sh -quality-level=Medium -windowed -ResX=1280 -ResY=720
```

Wait for " Carla server ready! " in the logs before starting the bridge.

### 4. Configure Simulation Parameters

```bash
# Platform selection (affects camera geometry)
params put EOPSimPlatform rk3576    # or rk3588

# Side-camera BSD testing
params put_bool EOPSideCamerasEnabled 1

# Traffic light speed controller testing
params put_bool EOPTLSCEnabled 1

# Scenario selection
params put EOPSimScenario pedestrian_crossing
# Options: pedestrian_crossing, cut_in, emergency_brake, cyclist_overtake

# Weather selection
params put EOPSimWeather rain
# Options: clear_day, clear_night, rain, heavy_rain, fog, overcast

# Vehicle type (affects physics tuning)
params put EOPVehicleType SUV_C
```

### 5. Run openpilot Sim Bridge

```bash
cd ~/openpilot
python tools/sim/run_bridge.py --simulator carla
```

The bridge will:
1. Connect to CARLA server
2. Spawn ego vehicle with platform-specific cameras
3. Start `SimulatedSideD` (publishes `sideDetections`)
4. Start `TrafficLightPublisher` (publishes `stereoObjects`)
5. Spawn scenario actors if `EOPSimScenario` is set
6. Apply weather preset if `EOPSimWeather` is set
7. Run the openpilot stack (controlsd, plannerd, etc.)

### 6. Enable Features Under Test

```bash
# In a separate terminal while bridge is running:
params put_bool EOPBSDChimeEnabled 1
params put_bool EOPTTSAlertsEnabled 1
params put_bool EOPTLSCEnabled 1
params put_bool EOPSideCamerasEnabled 1
```

## Platform-Specific Camera Geometry

### RK3588 (ExoPilot 01M)
- Stereo baseline: 80 mm
- Wide road FOV: 120°
- Road cam Y: -40 mm, Wide road cam Y: +40 mm
- Side cameras: **disabled**

### RK3576 (ExoPilot 02M)
- Stereo baseline: 160 mm
- Wide road FOV: 180°
- Road cam Y: 0 mm, Wide road cam Y: +80 mm, Tele road cam Y: -80 mm
- Side cameras: **enabled** (left yaw=150°, right yaw=210°, pitch=0°)

## Scenario Descriptions

| Scenario | Trigger | Expected Behavior |
|----------|---------|-------------------|
| `pedestrian_crossing` | Pedestrian 30m ahead crosses path | AEB / FCW alert |
| `cut_in` | Vehicle in adjacent lane merges into ego lane | BSD warning + LCA block |
| `emergency_brake` | Lead vehicle brakes hard after 5s | AEB / FCW alert, longitudinal slowdown |
| `cyclist_overtake` | Cyclist 40m ahead in bike lane | BSD caution, safe pass clearance |

## Weather Effects

| Preset | Use Case |
|--------|----------|
| `clear_day` | Baseline perception testing |
| `clear_night` | Low-light camera performance |
| `rain` | Wet road traction, reduced visibility |
| `heavy_rain` | Extreme visibility reduction |
| `fog` | Sensor degradation, reduced range |
| `overcast` | Diffuse lighting, no shadows |

## Troubleshooting

### CARLA won't start
- Check Vulkan support: `vulkaninfo | head -20`
- Try software rendering: `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json ./CarlaUE4.sh`
- Ensure port 2000 is free: `lsof -i :2000`

### Bridge can't connect
- Verify CARLA server is ready (look for "Carla server ready!")
- Check firewall: `ufw allow 2000/tcp && ufw allow 2001/tcp`
- Try explicit host: `python tools/sim/run_bridge.py --simulator carla --host localhost`

### No sideDetections published
- Check `EOPSimPlatform` is `rk3576` (RK3588 has no side cameras)
- Verify `EOPSideCamerasEnabled` is `1`
- Check that NPC vehicles are near ego (within 1.5–5.0m lateral, -20 to +5m longitudinal)

### No traffic light detections
- Verify `EOPTLSCEnabled` is `1`
- Ensure town has traffic lights (Town10HD has many)
- Check ego is within 3–100m of a traffic light and roughly aligned

### pytest fails with params_pyx warning
- Set env var: `OPENPILOT_STUB_PARAMS_PYX=1 pytest ...`
- Or use standalone runner: `python tools/sim/tests/run_tests.py`

## File Reference

| File | Purpose |
|------|---------|
| `tools/sim/lib/simulated_sided.py` | Ground-truth side-camera publisher |
| `tools/sim/lib/traffic_light_publisher.py` | Ground-truth traffic-light publisher |
| `tools/sim/lib/scenario_spawner.py` | Dynamic scenario injection |
| `tools/sim/lib/weather_controller.py` | Param-driven weather presets |
| `tools/sim/bridge/carla/carla_world.py` | CARLA world integration |
| `selfdrive/controls/lib/bsd.py` | Blind Spot Detection controller |
| `selfdrive/sided/bev_reprojector.py` | Side-camera BEV geometry |
| `tools/sim/tests/run_tests.py` | Standalone test runner (no CARLA) |
| `tools/sim/tests/test_simulated_components.py` | pytest test suite |
