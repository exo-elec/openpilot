# Foxglove Studio Integration

**MCAP Logging** - Parallel logging for Foxglove visualization.

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `system/mcapd/mcapd.py` |
| **MCAP Output** | ✅ `/data/media/0/mcap/` |
| **WebSocket** | ✅ Port 8765 |

---

## Architecture

```
cereal messages ──┬──▶ loggerd ──▶ rlog.zst (PlotJuggler)
                  └──▶ mcapd ────▶ data.mcap (Foxglove)
                         │
                         └──▶ WebSocket (live)
```

---

## Components

| Component | Purpose |
|-----------|---------|
| `mcapd` | MCAP file writer |
| `foxglove_bridge.py` | Standalone WebSocket |
| `rlog_to_mcap.py` | Offline conversion |

---

## Output

- MCAP files: `/data/media/0/mcap/<route>--<segment>/data.mcap`
- WebSocket: `ws://<ip>:8765`

---

## File Locations

- **Daemon**: `system/mcapd/mcapd.py`
- **Tools**: `tools/foxglove/`

---

## See Also

- MCAPD
- tools/foxglove/
