# ExoPilot (EOP)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |

---


**EnhancedOpenPilot** - Advanced ADAS for Rockchip RK3588.

---

## Platforms

| Platform | SoC | NPU | Status |
|----------|-----|-----|--------|
| **ExoPilot 01M** | RK3588 | 6 TOPS (3×2) | 🧪 Design target, not yet hardware-validated — see [RK3588_HARDWARE_VALIDATION_CHECKLIST.md](RK3588_HARDWARE_VALIDATION_CHECKLIST.md) |
| ExoPilot 02M | RK3576 | 6 TOPS (2×3) | ✗ Not supported by openpilot — VisionPilot only |

---

## Key Features

| Feature | Status |
|---------|--------|
| Stereo Vision | ✅ 80 mm baseline |
| Multi-Camera | ✅ 4 MIPI + 3 USB side/rear |
| Foxglove Logging | ✅ Parallel MCAP |
| Offline Nav | ✅ Valhalla + on-device routing |
| Driver Monitoring | ⚠️ VisionPilot only — not openpilot |
| System Switching | ✅ Switch to VisionPilot via Settings → Device |
| Side Camera Video | ✅ USB UVC streams |
| Side Camera AI BSD | ✅ Hailo-8 (no fallback policy without it) |
| Voice Pipeline | ⚠️ Azure voice server — local alert tones only; no on-board mic/STT/TTS |
| Two-Layer Safety | ✅ SocketD (software) + TC275 (hardware) |
| MCAP Recording | ✅ Parallel logging for Foxglove Studio |

---

## 🔥 Current Priority: InferenceD HAL (Phase 3 & 4 Complete)

See **INFERENCED_INDEX.md** for:
- Production-ready compute acceleration framework
- Performance profiling results
- All 5 daemon integrations verified (modeld, stereod, gridd, recordd + IPC)
- Edge hardware deployment plan (Phase 5 next)

---

## Documentation Index

| Category | Location | Purpose |
|----------|----------|---------|
| **System Switching** | SYSTEM_SWITCHING.md | openpilot ↔ VisionPilot upgrade chain |
| **InferenceD** | INFERENCED_INDEX.md | Compute HAL (RKNN, ACL, RGA, MPP) |
| **Performance** | PHASE4_PERFORMANCE_REPORT.md | Benchmark results & metrics |
| **Business** | SUBSCRIPTION_BUSINESS_MODEL.md | NavPilot monetization |
| **Code Quality** | CODE_QUALITY_LINT_CLEANUP.md | Lint cleanup status & mypy debt (2026-08-12) |

### Per-Daemon READMEs

| Daemon | Path | Description |
|--------|------|-------------|
| `v4l2d` | [`system/v4l2d/`](../../system/v4l2d/) | MIPI CSI camera capture (road, wide_road, stereo) |
| `uvcd` | [`system/uvcd/`](../../system/uvcd/) | USB/UVC camera capture (driver, side_left, side_right) |
| `modeld` | [`selfdrive/modeld/`](../../selfdrive/modeld/) | Driving model inference |
| `stereod` | [`selfdrive/stereod/`](../../selfdrive/stereod/) | Stereo depth (SGM) |
| `gridd` | [`selfdrive/gridd/`](../../selfdrive/gridd/) | BEV occupancy grid fusion |
| `monod` | [`selfdrive/monod/`](../../selfdrive/monod/) | Mono detection (RKNN NPU) |
| `sided` | [`selfdrive/sided/`](../../selfdrive/sided/) | Side camera BSD/RCTA (Hailo-8) |
| `driverd` | `selfdrive/driverd/` *(not implemented)**) | Driver monitoring / DMS |
| `soundd` | [`selfdrive/soundd/`](../../selfdrive/soundd/) | TTS + alert tone generation |
| `spkd` | [`system/spkd/`](../../system/spkd/) | I2S speaker output |
| `navd` | [`selfdrive/navd/`](../../selfdrive/navd/) | Offline Valhalla navigation |
| `pathd` | [`selfdrive/pathd/`](../../selfdrive/pathd/) | Trajectory planning + stereo nudge |
| `coordinationd` | [`selfdrive/coordinationd/`](../../selfdrive/coordinationd/) | OSM + SGM localization fusion |

---

**Branch:** EOP10  
**Last Updated:** 2026-08-12 (Lint cleanup + CODE_QUALITY_LINT_CLEANUP.md added)
