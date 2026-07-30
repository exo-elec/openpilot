# NavD - Navigation Daemon (Offline)

OpenPilot's **offline-only** navigation system using Valhalla routing engine.

## Overview

NavD provides turn-by-turn navigation without internet connectivity:
- **Local Valhalla** routing engine runs on-device (RK3588)
- **Offline OSM tiles** stored on SD card (`/data/media/0/valhalla/`)
- **No cloud dependency** - works in tunnels, remote areas

> **Note:** For online/cloud routing with real-time traffic, use **VisionPilot** (successor).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    OFFLINE NAVIGATION                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │   GPS/      │───→│    NavD     │───→│   Local     │     │
│  │   Fused     │    │   (navd.py) │    │  Valhalla   │     │
│  │  Position   │    │             │    │  (:8002)    │     │
│  └─────────────┘    └──────┬──────┘    └─────────────┘     │
│                            │                                 │
│                            ▼                                 │
│                     ┌─────────────┐                         │
│                     │  navInstruction                      │
│                     │  navRoute                            │
│                     └─────────────┘                         │
│                                                              │
│  Components:                                                 │
│  - tile_manager.py      Manual tile download/build          │
│  - tile_auto_manager.py Auto-download based on GPS          │
│  - set_destination.py   CLI tool for setting destination    │
│  - helpers.py           Valhalla response parsing           │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Build Valhalla (One-time)

```bash
cd /home/vcar/pilot/openpilot
scons -j4 --with-valhalla
```

### 2. Install Tiles

**Manual:**
```bash
python selfdrive/navd/tile_manager.py ensure thailand
```

**Auto (Recommended):**
```bash
# Enable auto-download (default: enabled)
python -c "from openpilot.common.params import Params; Params().put_bool('EOPAutoTileEnabled', True)"

# Auto-detects region from GPS and downloads on WiFi
python selfdrive/navd/tile_auto_manager.py --daemon
```

### 3. Set Destination

```bash
# Via CLI
python selfdrive/navd/set_destination.py 13.7563 100.5018 "Bangkok"

# Via NavPilot app (Bluetooth SPP)
# App sends destination via BLE to bluetoothd → NavDestination param
```

## Tile Management

### Available Regions

| Region | Size | Countries |
|--------|------|-----------|
| `thailand` | ~300 MB | Thailand |
| `malaysia-singapore` | ~150 MB | Malaysia, Singapore, Brunei |
| `japan` | ~2.5 GB | Japan |
| `california` | ~1.2 GB | California, USA |
| `germany` | ~8 GB | Germany |

### Commands

```bash
# List available regions
python selfdrive/navd/tile_manager.py list

# Download region
python selfdrive/navd/tile_manager.py download thailand

# Build tiles from PBF
python selfdrive/navd/tile_manager.py build thailand

# Download + build in one step
python selfdrive/navd/tile_manager.py ensure thailand

# Check status
python selfdrive/navd/tile_manager.py status

# Clean up PBF files (keep tiles)
python selfdrive/navd/tile_manager.py cleanup
```

## Configuration

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPNavEnabled` | bool | `0` | Enable navigation daemon |
| `EOPAutoTileEnabled` | bool | `1` | Auto-download tiles based on GPS |
| `EOPAutoTileWifiOnly` | bool | `1` | Only auto-download on WiFi |
| `NavDestination` | string | `""` | Current destination (JSON) |

### Valhalla Config

Template: `third_party/valhalla/valhalla.json.template`

Installed to: `/data/media/0/valhalla/valhalla.json`

## File Structure

```
selfdrive/navd/
├── navd.py                 # Main navigation daemon
├── helpers.py              # Valhalla response parsing
├── tile_manager.py         # Manual tile management
├── tile_auto_manager.py    # Auto tile download daemon
├── set_destination.py      # CLI destination tool
└── README.md               # This file

third_party/valhalla/
├── src/                    # Valhalla submodule (source)
├── bin/                    # Built binaries
│   ├── valhalla_service    # HTTP routing service
│   ├── valhalla_build_tiles
│   └── valhalla_build_config
└── valhalla.json.template  # Config template

/data/media/0/valhalla/     # SD card storage
├── valhalla_tiles.tar      # Routing tiles
├── valhalla.json           # Runtime config
└── thailand.osm.pbf        # Source OSM (optional, can delete)
```

## Troubleshooting

### "No offline tiles found"

```bash
# Download tiles for your region
python selfdrive/navd/tile_manager.py ensure <region>
```

### "Valhalla service not running"

```bash
# Check if tiles exist
ls -lh /data/media/0/valhalla/valhalla_tiles.tar

# Check if process is running
ps aux | grep valhalla_service

# Test manually
third_party/valhalla/bin/valhalla_service /data/media/0/valhalla/valhalla.json 1
```

### "Out of storage"

```bash
# Delete PBF source files (keep tiles)
python selfdrive/navd/tile_manager.py cleanup

# Check space
df -h /data/media/0/
```

## Comparison with VisionPilot

| Feature | OpenPilot (NavD) | VisionPilot |
|---------|------------------|-------------|
| **Routing** | Local/offline | Cloud/online |
| **Traffic** | Historical only | Real-time |
| **Tile Management** | Manual/Auto GPS | Automatic cloud |
| **Internet** | Not required | Required |
| **Best For** | Remote areas, privacy | City driving, traffic |

## References

- [Valhalla Quick Start](../../docs/eop/VALHALLA_QUICK_START.md)
- [Valhalla Offline Routing](../../docs/eop/VALHALLA_OFFLINE_ROUTING.md)
- [VisionPilot Navigation](../../../visionpilot/docs/navigation/)
