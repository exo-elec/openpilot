# Foxglove Studio Integration

Parallel visualization system supporting both **PlotJuggler** (rlog) and **Foxglove Studio** (MCAP).

## Architecture - Dual Visualization

```
                    REAL-TIME STREAMING
                    ┌─────────────────────────────────────┐
                    │                                     │
  ┌───────────┐     │    ┌──────────────┐                 │    ┌──────────────────┐
  │  cereal   │─────┼───▶│  juggle.py   │──UDP/WS──────▶  │───▶│  PlotJuggler     │
  │  messages │     │    │  (original)  │                 │    │  (real-time)     │
  └───────────┘     │    └──────────────┘                 │    └──────────────────┘
       │            │                                     │
       │            │    ┌──────────────┐                 │    ┌──────────────────┐
       └────────────┼───▶│  mcapd       │──WebSocket───▶  │───▶│  Foxglove Studio │
                    │    │  (WebSocket) │   port 8765     │    │  (real-time)     │
                    │    └──────────────┘                 │    └──────────────────┘
                    └─────────────────────────────────────┘

                    FILE STORAGE (Parallel)
                    ┌─────────────────────────────────────┐
                    │                                     │
  ┌───────────┐     │    ┌──────────────┐                 │    ┌──────────────────┐
  │  loggerd  │─────┼───▶│  rlog.zst    │──Open file───▶  │───▶│  PlotJuggler     │
  │           │     │    │  (original)  │                 │    │  (file mode)     │
  └───────────┘     │    └──────────────┘                 │    └──────────────────┘
       │            │                                     │
       │            │    ┌──────────────┐                 │    ┌──────────────────┐
       └────────────┼───▶│  data.mcap   │──Open file───▶  │───▶│  Foxglove Studio │
                    │    │  (mcapd)     │                 │    │  (file mode)     │
                    │    └──────────────┘                 │    └──────────────────┘
                    └─────────────────────────────────────┘
```

## Quick Comparison

| Feature | PlotJuggler | Foxglove Studio |
|---------|-------------|-----------------|
| **File format** | rlog.zst (cereal) | data.mcap (MCAP) |
| **Real-time** | `juggle.py --stream` | `mcapd` WebSocket |
| **File location** | `/data/media/0/realdata/` | `/data/media/0/mcap/` |
| **Plugin needed** | Yes (cereal plugin) | No (native) |
| **Web interface** | No | Yes |
| **Mobile app** | No | Yes |

## Usage

### Real-Time Streaming

**PlotJuggler:**
```bash
cd tools/plotjuggler
./juggle.py --stream
# Then open PlotJuggler → Connect
```

**Foxglove Studio:**
```bash
# mcapd runs automatically with loggerd
# Or manually for testing:
python system/mcapd/mcapd.py

# Foxglove Studio → Open Connection → Foxglove WebSocket
# Enter: ws://<device_ip>:8765
```

### File Analysis

**PlotJuggler:**
```bash
# Open rlog file directly
cd tools/plotjuggler
./juggle.py /data/media/0/realdata/2026-04-07--12-00-00--xxx--0/rlog.zst
```

**Foxglove Studio:**
```bash
# mcapd writes files automatically to:
# /data/media/0/mcap/2026-04-07--12-00-00--xxx--0/data.mcap

# In Foxglove: Open Local File → Select data.mcap
```

### Convert Old RLOGs to MCAP

```bash
python tools/foxglove/rlog_to_mcap.py \
    /data/media/0/realdata/2026-04-07--12-00-00--xxx--0/rlog.zst \
    -o old_drive.mcap
```

## File Locations

```
/data/media/0/realdata/              # Original rlog (PlotJuggler)
├── 2026-04-07--12-00-00--xxx--0/
│   ├── rlog.zst                     ← PlotJuggler file
│   └── qlog.zst

/data/media/0/mcap/                  # MCAP output (Foxglove)
├── 2026-04-07--12-00-00--xxx--0/
│   └── data.mcap                    ← Foxglove file
```

## Why Both?

| Use Case | Best Tool | Reason |
|----------|-----------|--------|
| Quick debug | PlotJuggler | Familiar, fast |
| Share with team | Foxglove | Web interface, no install |
| Mobile viewing | Foxglove | iOS/Android apps |
| Deep analysis | PlotJuggler | Custom layouts, CSV export |
| Remote viewing | Foxglove | Works over internet |
| Data pipeline | Both | rlog for training, MCAP for viz |

## Components

| Component | Purpose | Language | Type |
|-----------|---------|----------|------|
| `loggerd` | Original logging | C++ | Native daemon |
| `mcapd` | MCAP logging + WebSocket | Python | Python daemon |
| `juggle.py` | PlotJuggler interface | Python | Tool |
| `rlog_to_mcap.py` | Offline conversion | Python | Tool |

## Configuration

Environment variables for mcapd:
```bash
MCAP_ROOT=/data/media/0/mcap          # Output directory
MCAP_SEGMENT_LENGTH=60                # Segment duration (seconds)
MCAPD_WS_PORT=8765                    # WebSocket port
MCAPD_ENABLE_WS=1                     # Enable WebSocket (0=disable)
```

## Troubleshooting

**mcapd not starting:**
```bash
# Check if mcap module installed
pip install mcap

# Check logs
tail -f /tmp/mapd.log
```

**WebSocket connection failed:**
```bash
# Check firewall
sudo ufw allow 8765

# Test locally
python system/mcapd/mcapd.py
# Connect to ws://localhost:8765
```

**Missing MCAP files:**
```bash
# Check mcapd is running
ps aux | grep mcapd

# Check output directory
ls -la /data/media/0/mcap/
```
