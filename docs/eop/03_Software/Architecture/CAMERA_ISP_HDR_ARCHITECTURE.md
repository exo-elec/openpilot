# Camera ISP / HDR Architecture

**Date:** 2026-04-20  
**Scope:** v4l2d, monod, stereod — RKISP, V4L2, HDR/SDR pipeline design  
**Platform:** RK3588 (ExoPilot 01M)  
**Status:** Design complete — implementation gap identified

---

## 1. Executive Summary

This document defines the camera pipeline architecture for ExoPilot, covering:

- **How** mono cameras (road/wide_road/tele_road) use **RKISP + on-chip HDR @ 30Hz**
- **How** stereo cameras (stereo_left/stereo_right) use **V4L2 SDR @ 20Hz** for sync accuracy
- **Why** this hybrid approach is necessary for ADAS safety
- **Step-by-step** implementation plan to close the gap vs. VisionPilot

### Current State (OpenPilot)

| Aspect | Status | Detail |
|--------|--------|--------|
| v4l2d camera daemon | ✅ Running | Unified V4L2 capture for all cameras |
| RKISP integration | ❌ Missing | No HDR mode control, no RKIAQ 3A |
| OX03C10 HDR | ❌ Missing | Sensor defaults to SDR/linear |
| GC4653 stereo sync | ⚠️ Implicit | SDR enforced by lack of HDR config |
| ISP 3A (AE/AWB) | ❌ Stubbed | `ISP_AVAILABLE = False` in v4l2d |

### Target State (VisionPilot-Aligned)

| Aspect | Target | Detail |
|--------|--------|--------|
| Mono cameras | RKISP + HDR4 @ 30Hz | OX03C10 on-chip HDR4 (140dB) via V4L2 controls |
| Stereo cameras | V4L2 SDR @ 20Hz | GC4653 synchronized, no HDR |
| ISP 3A | RKIAQ ctypes | Hardware AE/AWB via `librkaiq.so` |
| HDR switching | Runtime configurable | SDR/HDR2/HDR3/HDR4 per camera |

---

## 2. Sensor Architecture

### 2.1 Camera Array by Platform

```
┌────────────────────────────────────────────────────────────────────────────┐
│                     ExoPilot 01M (RK3588) — 80 mm stereo baseline                  │
├────────────────────────────────────────────────────────────────────────────┤
│  Camera    │ Sensor  │ Resolution │ fps   │ HDR  │ ISP         │ Position  │
├────────────────────────────────────────────────────────────────────────────┤
│  Road      │ OX03C10 │ 1920×1280  │ 20fps │ HDR4 │ RKISP linear│ 0 mm      │
│  wide_road │ OX03C10 │ 1920×1280  │ 20fps │ HDR4 │ RKISP linear│ +80 mm    │
│  stereo_left│ GC4653  │ 2560×1440  │ 20fps │ SDR  │ V4L2 direct │ 0 mm      │
│  stereo_right│ GC4653  │ 2560×1440  │ 20fps │ SDR  │ V4L2 direct │ +80 mm    │
└────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────┐
│                     ExoPilot 02M (RK3576) — 160 mm stereo baseline                 │
├────────────────────────────────────────────────────────────────────────────┤
│  Camera    │ Sensor  │ Resolution │ fps   │ HDR  │ ISP         │ Position  │
├────────────────────────────────────────────────────────────────────────────┤
│  Road      │ OX03C10 │ 1920×1280  │ 20fps │ HDR4 │ RKISP linear│ 0 mm      │
│  wide_road │ OX03C10 │ 1920×1280  │ 20fps │ HDR4 │ RKISP linear│ +80 mm    │
│  tele_road │ OX03C10 │ 1920×1280  │ 20fps │ HDR4 │ RKISP linear│ 0 mm      │
│  stereo_left│ GC4653  │ 2560×1440  │ 20fps │ SDR  │ V4L2 direct │ −80 mm    │
│  stereo_right│ GC4653  │ 2560×1440  │ 20fps │ SDR  │ V4L2 direct │ +80 mm    │
└────────────────────────────────────────────────────────────────────────────┘
```

*(ExoPilot 02M array shown for reference only — VisionPilot's platform, not supported by openpilot.)*

### 2.2 Why Different HDR Strategies?

| Camera Type | Sensor | HDR Strategy | Reason |
|-------------|--------|--------------|--------|
| **Mono (road/wide_road/tele_road)** | OX03C10 | ✅ On-chip HDR4 @ 30fps | Night glare, tunnel exit, oncoming headlights — 140dB DR, PWL 20-bit output |
| **Stereo (stereo_left/stereo_right)** | GC4653 | ❌ SDR only (no HDR hardware) | GC4653 is SDR-only (81dB). Even if HDR were available, temporal misalignment would cause depth errors |

> **Critical Safety Finding:** Staggered HDR causes 10–15ms row-dependent timing variation. At 100 km/h relative speed, this creates **±0.8m depth error** (5× worse than SDR). See HDR Stereo Depth Analysis for full derivation.

---

## 3. Pipeline Architecture

### 3.1 Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MONO PIPELINE (HDR)                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Scene ──▶ OX03C10 ──▶ [On-chip HDR4] ──▶ PWL 16-bit ──▶ RKISP ──▶ NV12  │
│              │                    │                          │              │
│              │                    │ 120dB combined           │ 3A (AE/AWB)  │
│              │                    │ on-sensor                │ linear mode  │
│              ▼                    ▼                          ▼              │
│         V4L2 capture          No ISP HDR                /dev/videoN        │
│                                                                             │
│  Output: NV12 @ 30Hz ──▶ VisionIPC ──▶ monod (YOLO + segmentation)        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                        STEREO PIPELINE (SDR)                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Scene ──▶ GC4653 ──▶ [SDR linear] ──▶ 12-bit RAW ──▶ RKISP ──▶ NV12     │
│              │                 │                         │                  │
│              │                 │ 81dB DR                 │ 3A (AE/AWB)      │
│              │                 │ synchronized pair       │ exposure sync    │
│              ▼                 ▼                         ▼                  │
│         V4L2 capture      No HDR merging            /dev/videoN             │
│                                                                             │
│  Output: NV12 @ 20Hz ──▶ VisionIPC ──▶ stereod (SGM depth)                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 ISP Mode Configuration

| Sensor | ISP Mode | HDR Location | Output Format | Notes |
|--------|----------|--------------|---------------|-------|
| OX03C10 | `RK_AIQ_WORKING_MODE_NORMAL` | On-chip (sensor) | 16-bit PWL → NV12 | ISP does NOT do HDR — sensor handles it |
| GC4653 | `RK_AIQ_WORKING_MODE_NORMAL` | None (SDR) | 12-bit linear → NV12 | Exposure synchronized between L/R |

> **Common Mistake:** Using `RK_AIQ_WORKING_MODE_ISP_HDR2/3` for OX03C10. This is wrong — the sensor already outputs combined HDR. ISP HDR modes are for sensors that output separate exposures requiring ISP-level combination.

---

## 4. Component Deep Dive

### 4.1 v4l2d — Camera Daemon

**File:** `system/v4l2d/v4l2d.py`

**Current Behavior:**
- Discovers cameras via `/sys/class/video4linux` with `rkisp` name matching
- Opens all cameras through `HARDWARE.get_camera_hal().open_camera()`
- **No HDR mode configuration** — relies on driver defaults
- **ISP 3A stubbed** — `ISP_AVAILABLE = False`, `init_isp()` is no-op

**Required Changes:**

```python
# system/v4l2d/v4l2d.py — CameraConfig extension
CameraConfig = namedtuple("CameraConfig", [
    "msg_name",
    "stream_type",
    "device_path",
    "cam_id",
    "vipc_server",
    "sensor",
    "hdr_mode",        # NEW: HDRMode enum (SDR, HDR2, HDR3, HDR4)
    "fps",             # NEW: target framerate
    "isp_mode",        # NEW: RKIAQ working mode
])
```

**Per-Camera Defaults:**

| Camera | hdr_mode | fps | isp_mode | Reason |
|--------|----------|-----|----------|--------|
| road | `HDR4` | 20 | `NORMAL` | 140dB @ 20fps — 2-lane MIPI bandwidth limit |
| wide_road | `HDR4` | 20 | `NORMAL` | 140dB @ 20fps — 2-lane MIPI bandwidth limit |
| stereo_left | `SDR` | 20 | `NORMAL` | GC4653 has no HDR. Temporal sync required |
| stereo_right | `SDR` | 20 | `NORMAL` | GC4653 has no HDR. Temporal sync required |

### 4.2 OX03C10 Driver

**File:** `system/v4l2d/drivers/ox03c10.py` (to create)

**Architecture:**

```python
class OX03C10Driver:
    """OX03C10 with on-chip HDR control.
    
    HDR is configured at SENSOR level via V4L2 private controls.
    ISP runs in NORMAL (linear) mode — it receives already-combined HDR.
    """
    
    # V4L2 private control IDs (kernel driver specific)
    V4L2_CID_OX03C10_HDR_MODE = V4L2_CID_PRIVATE_BASE + 1
    V4L2_CID_OX03C10_LFM = V4L2_CID_PRIVATE_BASE + 2
    
    class HDRMode(IntEnum):
        LINEAR = 0   # SDR, ~60dB
        HDR2 = 1     # 2-exposure, ~80dB
        HDR3 = 2     # 3-exposure, ~120dB
        HDR4 = 3     # 4-exposure, ~140dB ← DEFAULT for ADAS
    
    def set_hdr_mode(self, mode: HDRMode) -> bool:
        """Set HDR mode via V4L2 ioctl. Requires stream restart."""
        # ioctl VIDIOC_S_CTRL with V4L2_CID_OX03C10_HDR_MODE
        # Fallback to v4l2-ctl subprocess if ioctl fails
```

**HDR Mode Selection Logic:**

```python
def select_hdr_mode(conditions: LightingConditions) -> OX03C10Driver.HDRMode:
    """Auto-select HDR mode based on scene lighting."""
    if conditions.max_lux > 50000 and conditions.min_lux < 10:
        return HDRMode.HDR4   # High contrast: tunnel exit, night+headlights
    elif conditions.max_lux > 10000:
        return HDRMode.HDR2   # Moderate contrast: daytime shadows
    else:
        return HDRMode.LINEAR  # Low contrast: overcast, uniform lighting
```

### 4.3 GC4653 Driver

**File:** `system/v4l2d/drivers/gc4653.py` (to create)

**Architecture:**

```python
class GC4653Driver:
    """GC4653 stereo camera — SDR only, synchronized exposure.
    
    Critical: Both stereo_left and stereo_right MUST have identical exposure timing
    for accurate stereo correspondence. HDR is EXPLICITLY DISABLED.
    """
    
    # OTP black level calibration
    REG_BLC_TARGET = 0x0315
    REG_EXPOSURE_H = 0x0202
    REG_EXPOSURE_L = 0x0203
    REG_GAIN_H = 0x02b3
    REG_GAIN_L = 0x02b4
    
    def __init__(self, v4l2_device: str, i2c_bus: int, i2c_addr: int,
                 is_master: bool = False):
        self.is_master = is_master
        # Master camera drives exposure; slave follows
    
    def sync_exposure(self, exposure_lines: int, gain: float):
        """Synchronize exposure with stereo partner."""
        # Atomically write exposure + gain registers
        # Both cameras must receive same values within <1ms
```

**Stereo Synchronization:**

| Aspect | Requirement | Implementation |
|--------|-------------|----------------|
| Exposure match | ±1 line | I2C burst write to both cameras |
| Frame sync | Hardware FSYNC | GPIO trigger or shared XCLK |
| Black level | Matched OTP | Read OTP, apply common BLC |
| Temperature | Compensated | 0.5 LSB/°C drift correction |

### 4.4 RKIAQ ISP Integration

**File:** `system/v4l2d/isp/rkiaq_wrapper.py` (to create)

**Architecture:**

```python
class RKIAQWrapper:
    """Rockchip ISP 3A via RKIAQ ctypes bindings.
    
    Library: librkaiq.so v2.0.8
    Modes: NORMAL (for OX03C10), HDR2/HDR3/HDR4 (for multi-exposure sensors)
    """
    
    def __init__(self, device_path: str, sensor_name: str, 
                 iq_file: Optional[str] = None):
        self._lib = ctypes.CDLL("librkaiq.so", mode=ctypes.RTLD_GLOBAL)
        self._ctx = None
        self._sensor = sensor_name  # "OX03C10" or "GC4653"
        
    def initialize(self) -> bool:
        """Init RKIAQ context with IQ tuning file."""
        # rk_aiq_uapi2_sysctl_init(iq_file_dir, sensor_name)
        # For OX03C10: always use NORMAL mode (sensor does HDR)
        
    def set_ae(self, target_brightness: int, 
               exposure_range_us: Tuple[int, int],
               gain_range_db: Tuple[float, float]) -> bool:
        """Configure auto-exposure."""
        
    def set_awb(self, mode: str, color_temp_range: Tuple[int, int]) -> bool:
        """Configure auto white balance."""
        
    def get_metadata(self) -> ISPMetadata:
        """Get current 3A state for publishing."""
```

**IQ Tuning File Structure:**

```json
{
  "version": "2.0.8",
  "sensor": "OX03C10",
  "resolution": "1920x1280",
  "ae": {
    "target_brightness": 128,
    "exposure_range_us": [100, 33000],
    "gain_range_db": [0, 24],
    "anti_flicker": "50hz"
  },
  "awb": {
    "mode": "auto",
    "color_temperature_range_k": [2700, 6500]
  },
  "hdr": {
    "mode": "linear",
    "note": "OX03C10 does HDR on-chip — ISP runs linear"
  },
  "nr": {
    "3dnr_enabled": true,
    "2dnr_enabled": true,
    "nr_strength": 30
  }
}
```

---

## 5. Message Flow

### 5.1 Frame Pipeline

```
┌─────────┐    VisionIPC     ┌─────────┐    msgq      ┌─────────┐
│  v4l2d  │─────────────────▶│  monod  │─────────────▶│ modeld  │
│ (capture)│   NV12 @ 30Hz   │ (detect)│ monoDetections│ (plan)  │
│ + HDR4  │                  │ + HDR   │              │         │
└─────────┘                  └─────────┘              └─────────┘
     │
     │ VisionIPC (stereo_left/right)
     ▼
┌─────────┐    msgq          ┌─────────┐
│ stereod  │─────────────────▶│  gridd  │
│ (SGM)   │ stereoDepth      │ (fusion)│
│ + SDR   │                  │         │
└─────────┘                  └─────────┘
```

### 5.2 Metadata Publishing

| Message | Publisher | Content | Rate |
|---------|-----------|---------|------|
| `roadCameraState` | v4l2d | frameId, timestamp, exposure, gain, sensor | 30Hz |
| `wideRoadCameraState` | v4l2d | frameId, timestamp, exposure, gain, sensor | 30Hz |
| `stereoCameraState` | v4l2d | frameId, timestamp, exposure, gain, sensor | 20Hz |
| `stereoCameraStateRight` | v4l2d | frameId, timestamp, exposure, gain, sensor | 20Hz |
| `ispMetadata` | v4l2d | ae_converged, awb_converged, color_temp, hdr_mode | 20Hz |

---

## 6. Implementation Roadmap

### Phase 1: V4L2 Driver Layer (Week 1)

| Step | Task | Files | Verification |
|------|------|-------|------------|
| 1.1 | Create `OX03C10Driver` | `system/v4l2d/drivers/ox03c10.py` | `v4l2-ctl -d /dev/video0 --set-ctrl hdr_mode=2` |
| 1.2 | Create `GC4653Driver` | `system/v4l2d/drivers/gc4653.py` | I2C register read/write verified |
| 1.3 | Create driver base class | `system/v4l2d/drivers/base.py` | Common V4L2 ioctl wrapper |
| 1.4 | Extend `CameraConfig` | `system/v4l2d/v4l2d.py` | hdr_mode + fps fields added |

### Phase 2: RKIAQ ISP Integration (Week 2)

| Step | Task | Files | Verification |
|------|------|-------|------------|
| 2.1 | Create RKIAQ ctypes bindings | `system/v4l2d/isp/rkaiq_ctypes.py` | `librkaiq.so` loads, version printed |
| 2.2 | Create `RKIAQWrapper` | `system/v4l2d/isp/rkiaq_wrapper.py` | Context init succeeds |
| 2.3 | Add IQ tuning files | `/etc/iqfiles/ox03c10.json` | AE/AWB converge within 5 frames |
| 2.4 | Integrate ISP into v4l2d | `system/v4l2d/v4l2d.py` | `ISP_AVAILABLE = True` |

### Phase 3: HDR Mode Control (Week 3)

| Step | Task | Files | Verification |
|------|------|-------|------------|
| 3.1 | Implement HDR switching | `system/v4l2d/drivers/ox03c10.py` | HDR4 → SDR → HDR4 stream restart works |
| 3.2 | Add runtime HDR config | Params: `EOPRoadHDR`, `EOPWideHDR` | Param change triggers mode switch |
| 3.3 | Stereo exposure sync | `system/v4l2d/drivers/gc4653.py` | stereo_left/stereo_right exposure match ±1 line |
| 3.4 | OTP black level read | `system/v4l2d/drivers/gc4653.py` | OTP values read, common BLC applied |

### Phase 4: Integration & Testing (Week 4)

| Step | Task | Files | Verification |
|------|------|-------|------------|
| 4.1 | End-to-end mono pipeline | v4l2d → monod | 20Hz sustained, HDR4 active |
| 4.2 | End-to-end stereo pipeline | v4l2d → stereod | 20Hz sustained, depth accuracy <2% |
| 4.3 | ISP metadata publishing | `cereal/log.capnp` | `ispMetadata` message at 20Hz |
| 4.4 | MIPI bandwidth test | Full system | All 5 cameras @ 20fps, no CRC errors |
| 4.5 | Thermal stress test | Full system | No frame drops at 75°C SoC |

---

## 7. Configuration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPRoadHDR` | string | `"hdr4"` | Road camera HDR mode: sdr, hdr2, hdr3, hdr4 |
| `EOPWideHDR` | string | `"hdr4"` | Wide road camera HDR mode |
| `EOPCameraFPS` | int | `20` | Target framerate (20=safe for 2-lane HDR4, 30=SDR only) |
| `EOPStereoHDR` | string | `"sdr"` | Stereo HDR mode — MUST stay SDR |
| `EOPStereoSync` | bool | `true` | Enable stereo exposure synchronization |
| `EOPIspEnabled` | bool | `true` | Enable RKIAQ ISP 3A |
| `EOPIQFilePath` | string | `"/etc/iqfiles"` | IQ tuning file directory |

---

## 8. Safety Considerations

### 8.1 Stereo Depth Accuracy

| Condition | SDR Error | HDR4 Error | Impact |
|-----------|-----------|------------|--------|
| Static scene | ±0.1m | ±0.3m | Minor |
| 100 km/h closing | ±0.2m | ±1.0m | **Critical** — false AEB trigger risk |
| Headlight glare | ±0.2m | ±0.8m | Lane keeping degradation |

**Enforcement:** `EOPStereoHDR` param is read-only (set at factory). UI shows warning if modified.

### 8.2 HDR Mode Switching

- HDR mode change requires **stream stop + reconfigure + restart**
- During switch: camera unavailable for ~500ms
- **Never switch HDR while engaged** — selfdrived blocks mode changes when `controlsState.enabled`

### 8.3 ISP Failure Fallback

| Failure | Detection | Fallback |
|---------|-----------|----------|
| RKIAQ init fail | `librkaiq.so` missing | Software AE via V4L2 controls |
| IQ file missing | File not found | Default linear mode, no tuning |
| HDR ioctl fail | `VIDIOC_S_CTRL` error | Continue in current mode, log warning |
| Stereo sync fail | Exposure mismatch >5 lines | Disable stereo, alert driver |

---

## 9. File Structure

```
system/v4l2d/
├── v4l2d.py                    # Main daemon (extend CameraConfig)
├── drivers/
│   ├── __init__.py
│   ├── base.py                 # BaseCameraDriver — V4L2 ioctl wrapper
│   ├── ox03c10.py              # OX03C10 — HDR4 on-chip, V4L2 controls
│   └── gc4653.py               # GC4653 — SDR only, OTP BLC, sync
├── isp/
│   ├── __init__.py
│   ├── rkaiq_ctypes.py         # librkaiq.so ctypes bindings
│   ├── rkiaq_wrapper.py        # High-level ISP control
│   └── iq_files/
│       ├── ox03c10.json        # OX03C10 IQ tuning (linear mode)
│       └── gc4653.json         # GC4653 IQ tuning (linear only)
└── tests/
    ├── test_ox03c10.py
    ├── test_gc4653.py
    └── test_stereo_sync.py
```

---

## 10. References

### Internal Documents

| Document | Purpose |
|----------|---------|
| HAL.md | Compute HAL architecture |
| VISIONPILOT_FEATURE_COMPARISON.md | Feature parity analysis |
| VISIONPILOT_SYSTEM_COMPARISON.md | System architecture comparison |

### External References

| Document | Source | Purpose |
|----------|--------|---------|
| `HDR_STEREO_DEPTH_ANALYSIS.md` | VisionPilot | Quantitative HDR vs stereo accuracy |
| `ISP_HDR_STEREO_IMPLEMENTATION_PLAN.md` | VisionPilot | Phase-by-phase implementation |
| `hdr_mode_switching.md` | VisionPilot | OX03C10 on-chip HDR architecture |
| `ox03c10_hdr_note.md` | VisionPilot | Why OX03C10 HDR is sensor-level |
| GC4653 Datasheet | GalaxyCore | OTP memory map, register definitions |
| OX03C10 Driver | NXP kernel | V4L2 control IDs |
| RKIAQ v2.0.8 | rockchip-linux | C API documentation |

---

## 11. Document History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-04-20 | Initial architecture — gap analysis, roadmap, safety findings |
