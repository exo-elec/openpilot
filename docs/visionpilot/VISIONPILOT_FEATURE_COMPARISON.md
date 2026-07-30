# VisionPilot vs OpenPilot - Detailed Feature Comparison

**Date:** 2026-04-08  
**Purpose:** Compare specific ADAS/Control features between VisionPilot and OpenPilot

---

## Summary: Feature Parity Status

| Category | OpenPilot | VisionPilot | Parity |
|----------|-----------|-------------|--------|
| **Core ADAS** | 10 features | 10 features | ✅ 90% |
| **Enhanced ADAS** | 4 features | 3 features | ⚠️ 75% |
| **Safety** | 3 features | 5 features | ⚠️ 75% |
| **Voice/AI** | 6 features | 6 features | ✅ 100% |
| **Navigation** | 5 features | 7 features | ⚠️ 71% |
| **Perception** | 6 features | 9 features | ⚠️ 67% |

**Overall Feature Parity: ~70%** - Significant overlap with some gaps in each direction.

---

## 1. Core ADAS/Control Features

### 1.1 ALCC (Always Lane Centering Control)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/controls/controlsd.py` | `src/control/vehicle_controller/lateral_assist_controller.py` |
| **Description** | Continuous lateral assistance | Industry-standard LKA/LCA |
| **Override** | Torque-based (1.0 Nm) | Torque-based |
| **Visual** | Blue wheel indicator | Status publishing |

**Parity: ✅ FULL**

---

### 1.2 TJA (Traffic Jam Assist)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/controls/lib/longcontrol.py` | `src/control/vehicle_controller/advanced_features_controller.py` |
| **Features** | Standstill hold, gentle resume | Standstill hold, gentle resume |
| **Hold Time** | 120s | 120s |
| **Ramp** | 0.25 → 1.2 m/s² | 0.25 → 1.2 m/s² |

**Parity: ✅ FULL**

---

### 1.3 VTSC (Vision Turn Speed Control)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/controls/lib/vtsc.py` | Integrated in velocity planner |
| **Formula** | v = √(a_comfort / κ) | Same formula |
| **States** | 5-state machine | Curve detection |
| **Range** | 0-150m (vision) | 0-150m (vision) |

**Parity: ✅ FULL**

---

### 1.4 MTSC (Map Turn Speed Control)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ❌ NOT IMPLEMENTED |
| **File** | `selfdrive/controls/lib/mtsc.py` | N/A |
| **Data Source** | OSM via MAPD | N/A |
| **Range** | 250-500m | N/A |
| **Integration** | NAVD → MAPD → MTSC | N/A |

**Parity: ❌ GAP** - VisionPilot lacks MTSC

---

### 1.5 MSLC (Map Speed Limit Control)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/controls/lib/mslc.py` | Integrated in velocity planner |
| **Data Source** | OSM via MAPD | OSM |
| **Offset** | Percent or fixed | Configurable |

**Parity: ✅ FULL**

---

### 1.6 NSLC (Navigation Speed Limit Control)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **Source** | Navigation route data | Navigation route data |

**Parity: ✅ FULL**

---

### 1.7 TLSC (Traffic Light Speed Control)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/controls/lib/tlsc.py` | `src/perception/traffic_light_detector/` |
| **Detection** | Traffic light classification | Traffic light detection + classification |
| **Response** | Stop/yield at red/yellow | Stop/yield at red/yellow |

**Parity: ✅ FULL**

---

### 1.8 DLAT (Dynamic Lateral Profile)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/controls/lib/dlat.py` | `src/control/vehicle_controller/vehicle_controller_node.py` |
| **Modes** | Laneful/Auto/Laneless | Laneful/Auto/Laneless |
| **Switching** | Hysteresis-based | ModelV2 confidence-based |
| **Confidence** | 3-state hysteresis | Confidence thresholding |

**Parity: ✅ FULL**

---

### 1.9 DLON (Dynamic Longitudinal Profile)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/controls/lib/dlon.py` | `src/control/vehicle_controller/dynamic_longitudinal_controller.py` |
| **Modes** | Chill/Auto/Experimental | ACC/Blended/Experimental |
| **Algorithm** | Kalman-filtered triggers | Kalman-filtered triggers |
| **Triggers** | 5+ conditions | 4+ conditions |

**Parity: ✅ FULL** - Both implement similar mode switching

---

### 1.10 LCA (Lane Change Assist)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ❌ NOT IMPLEMENTED |
| **File** | `selfdrive/controls/lib/desire_helper.py` | N/A |
| **Features** | Nudgeless, gap eval, BSM | N/A |
| **States** | OFF→PRE_CHANGE→STARTING→FINISHING | N/A |

**Parity: ❌ GAP** - VisionPilot lacks LCA

---

### 1.11 SQSC (Surface Quality Speed Control)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/controls/lib/sqsc.py` | Integrated in surface analysis |
| **Source** | Surface quality database | Surface roughness analysis |
| **Adaptation** | Speed reduction for rough roads | Speed reduction for rough roads |

**Parity: ✅ FULL**

---

## 2. Enhanced ADAS Features (Stereo-Based)

### 2.1 ELAT (Enhanced Lateral Controller)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ❌ NOT IMPLEMENTED |
| **File** | `selfdrive/pathd/lat_nudge.py` | N/A |
| **Features** | Lateral obstacle avoidance, road boundary | N/A |
| **Range** | 0-160m (stereo) | N/A |
| **Input** | GRIDD stereoGround, gridObjects | N/A |

**Parity: ❌ GAP** - VisionPilot lacks ELAT

---

### 2.2 ELON (Enhanced Longitudinal Controller)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ❌ NOT IMPLEMENTED |
| **File** | `selfdrive/pathd/lon_nudge.py` | N/A |
| **Features** | Surface-aware speed, 3D lead tracking | N/A |
| **Range** | 0-160m (stereo) | N/A |
| **Input** | GRIDD stereoGround, elevation | N/A |

**Parity: ❌ GAP** - VisionPilot lacks ELON

---

### 2.3 GRIDD (Grid Daemon)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/gridd/gridd.py` | `src/perception/occupancy_grid/` |
| **Features** | 3D objects, stereo ground, depth map | Costmap, BEV, object detection |
| **Outputs** | gridObjects, stereoGround | Occupancy grid, 3D objects |

**Parity: ✅ FULL** - Both have occupancy grid generation

---

### 2.4 RECORDD (Recording Daemon)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/recordd/recordd.py` | `src/logger/loop/` |
| **Source** | stereo_left camera | Camera loop recording |
| **Resolution** | 1920x1080 @ 30fps | 1920x1080 @ 30fps |
| **Codec** | H.264 hardware | H.264 hardware |

**Parity: ✅ FULL**

---

### 2.5 AEB (Automatic Emergency Braking)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ❌ NOT SUPPORTED | ✅ Complete |
| **File** | N/A - Design rejected | `src/safety/aeb/` |
| **Range** | N/A | Full implementation |
| **Trigger** | N/A | TTC-based |
| **Reason** | OpenPilot safety policy | N/A |

**Parity: ❌ INTENTIONAL** - OpenPilot explicitly does not support AEB by design decision

---

## 3. Safety Features

### 3.1 FCW (Forward Collision Warning)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/controls/lib/red.py` | Integrated in controller |
| **Trigger** | Lead vehicle closing speed | Closing speed detection |

**Parity: ✅ FULL**

---

### 3.2 LDW (Lane Departure Warning)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ❌ NOT IMPLEMENTED |
| **File** | `selfdrive/controls/lib/ldw.py` | N/A |
| **Trigger** | Unintended lane departure | N/A |

**Parity: ❌ GAP** - VisionPilot lacks standalone LDW

---

### 3.3 BSD (Blind Spot Detection)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete (vehicle CAN) | ✅ Complete |
| **File** | `selfdrive/pathd/blindspot.py` | `src/safety/bsd/` |
| **Source** | Vehicle CAN signals | Dedicated BSD module |

**Parity: ✅ FULL**

---

### 3.4 RED (Road Edge Detection)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/controls/lib/red.py` | `src/control/vehicle_controller/road_edge_controller.py` |
| **Fusion** | Vision (0.5) + YOLO (0.3) + Stereo (0.2) | Vision-based |
| **States** | 4-state machine | Active in laneless mode |

**Parity: ✅ FULL**

---

### 3.5 SOC (Smart Offset Controller)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ❌ NOT IMPLEMENTED |
| **File** | `selfdrive/pathd/soc.py` | N/A |
| **Function** | Lateral nudge for fast-closing objects | N/A |

**Parity: ❌ GAP** - VisionPilot lacks SOC

---

## 4. Voice/AI Features

### 4.1 Wake Word Detection

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/waked/waked.py` | `src/voice/wake_word/` |
| **Trigger** | "Hey ExoPilot", "OK ExoPilot" | "Hi EXO" |
| **Hardware** | Hailo-8 | Hailo-8 |

**Parity: ✅ FULL** - Both have wake word detection

---

### 4.2 Speech-to-Text (STT)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/voiced/hailo_whisper.py` | `src/voice/whisper_stt/` |
| **Model** | Whisper (Hailo) | Whisper (Hailo) |
| **Hardware** | Hailo-8 | Hailo-8 |

**Parity: ✅ FULL**

---

### 4.3 Natural Language Understanding

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete (3-tier) | ✅ Complete (3-tier) |
| **File** | `selfdrive/voiced/intent_pipeline.py` | `src/voice/nlu/` |
| **Tier 1** | Dictionary/Regex (CPU) | Keyword matching |
| **Tier 2** | Qwen2.5-0.5B (Hailo-8) | Pattern matching |
| **Tier 3** | N/A (removed) | LLM inference |

**Parity: ✅ FULL** - Both have 3-tier NLU pipelines

---

### 4.4 Text-to-Speech (TTS)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/soundd/piper_tts.py` | `src/audio/piper_tts/` |
| **Engine** | Piper | Piper |
| **Output** | I2S speaker | I2S speaker |

**Parity: ✅ FULL**

---

### 4.5 Voice Navigation

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **Recognition** | ✅ "Navigate to X" recognized | ✅ "Navigate to X" |
| **Execution** | ✅ Connected via intentd | ✅ Connected to nav system |
| **File** | `selfdrive/intentd/intentd.py` | `src/voice/command_routers/` |
| **Shortcuts** | "Take me home", "Take me to work" | Similar |

**Parity: ✅ FULL** - intentd handles navigation voice commands and sets NavDestination

---

### 4.6 AI Assistant/LLM (Tier 3)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | N/A (removed) | `src/voice/nlu/` |
| **Function** | Not available | Cloud NLU (Gemini) |

**Parity: ✅ FULL**

---

## 5. Navigation Features

### 5.1 Local Routing (Valhalla)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **Engine** | Valhalla | Valhalla |
| **Data** | OSM | OSM |

**Parity: ✅ FULL**

---

### 5.2 Offline Maps

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **Source** | OpenStreetMap | OpenStreetMap |

**Parity: ✅ FULL**

---

### 5.3 Turn-by-Turn

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **Message** | NavManeuver | NavManeuver |

**Parity: ✅ FULL**

---

### 5.4 POI Cache

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ❌ NOT IMPLEMENTED | ✅ Complete |
| **Size** | N/A | 5000-entry cache |

**Parity: ❌ GAP** - OpenPilot lacks POI cache

---

### 5.5 Search (Google Places/Nominatim)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ❌ NOT IMPLEMENTED | ✅ Complete |
| **Local Search** | ❌ None | ✅ Google Places + Nominatim |
| **NCP Protocol** | ✅ Fallback response (0x50/0x51) | ✅ Full implementation |
| **Behavior** | Tells NavPilot to use local search | Handles search locally |

**Parity: ❌ GAP** - OpenPilot has no local search; relies on NavPilot fallback

---

### 5.6 Electronic Horizon

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ❌ NOT IMPLEMENTED | ✅ Complete |
| **Service** | N/A | 0x0A01 NCP service |

**Parity: ❌ GAP** - OpenPilot lacks electronic horizon

---

### 5.7 HD Maps (Mapbox)

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ❌ REMOVED | ❌ NOT IMPLEMENTED |
| **Source** | N/A | N/A |

**Parity: ✅ REMOVED** - Mapbox dependency removed from both projects

---

### 5.8 Traffic Integration

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ❌ NOT IMPLEMENTED | ✅ Complete |
| **Source** | N/A (offline) | Gemini traffic API |

**Parity: ❌ GAP** - OpenPilot lacks traffic integration

---

## 6. Perception Features

### 6.1 YOLO Object Detection

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/gridd/yolo_objdet.py` | `src/perception/yolo_detector/` |
| **Classes** | Multi-class | Multi-class |

**Parity: ✅ FULL**

---

### 6.2 Lane Detection

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | Model-based | `src/perception/ego_lanes/` |

**Parity: ✅ FULL**

---

### 6.3 Traffic Light Detection

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/gridd/traffic_light_classifier.py` | `src/perception/traffic_light_detector/` |

**Parity: ✅ FULL**

---

### 6.4 Stereo Depth

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/stereod/` | `src/perception/stereo_depth/` |
| **Method** | SGM | SGM |
| **Baseline** | 80mm | 160mm (RK3576) |
| **HDR** | ❌ Not configured | ❌ SDR enforced (correct) |
| **ISP 3A** | ❌ Stubbed | ✅ RKIAQ hardware AE/AWB |

**Parity: ⚠️ PARTIAL** - Same SGM algorithm, but OpenPilot lacks RKISP integration and stereo exposure sync.

---

### 6.5 BEV Drivable Area

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ❌ NOT IMPLEMENTED | ✅ Complete |
| **File** | N/A | `src/perception/bev_segmentation/` |

**Parity: ❌ GAP** - OpenPilot lacks BEV

---

### 6.6 Surface Analysis

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ✅ Complete | ✅ Complete |
| **File** | `selfdrive/surfaced/` | `src/perception/surface_analyzer/` |

**Parity: ✅ FULL**

---

### 6.7 LiDAR Support

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ❌ NOT IMPLEMENTED | ✅ (RK3688 only) |
| **Hardware** | N/A | Hesai Pandar QT64 |

**Parity: ❌ GAP** - OpenPilot lacks LiDAR

---

### 6.8 Stop Line Detection

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ❌ NOT IMPLEMENTED | ✅ Complete |
| **File** | N/A | `src/perception/stop_line_detector/` |

**Parity: ❌ GAP** - OpenPilot lacks stop line detection

---

### 6.9 Pedestrian/Cyclist Detection

| Aspect | OpenPilot | VisionPilot |
|--------|-----------|-------------|
| **Status** | ⚠️ Via YOLO | ✅ Dedicated |
| **File** | YOLO general | `src/perception/safety_detector/` |

**Parity: ⚠️ PARTIAL** - VisionPilot has dedicated safety detection

---

## 7. Summary of Feature Gaps

### Features OpenPilot Has (VisionPilot Missing)

| Feature | Priority | Impact |
|---------|----------|--------|
| MTSC (Map Turn Speed) | Medium | Better curve anticipation |
| LCA (Lane Change Assist) | High | Auto lane change |
| ELAT (Enhanced Lateral) | Medium | Obstacle avoidance |
| ELON (Enhanced Longitudinal) | Medium | Surface-aware speed |
| LDW (Lane Departure Warning) | Low | Safety feature |
| SOC (Smart Offset) | Low | Comfort feature |
| ~~HD Maps (Mapbox)~~ | ~~Low~~ | ~~Premium navigation~~ |

### Features VisionPilot Has (OpenPilot Missing)

| Feature | Priority | Impact |
|---------|----------|--------|
| AEB (Auto Emergency Braking) | ~~CRITICAL~~ **N/A** | Intentionally not supported in OpenPilot |
| ~~Wake Word~~ | ~~Medium~~ | ~~Both have it~~ |
| ~~Voice Navigation~~ | ~~Medium~~ | ~~Now implemented in intentd~~ |
| **Camera HDR (OX03C10)** | **High** | Night/tunnel perception — 120dB vs 60dB |
| **RKISP / RKIAQ 3A** | **High** | Hardware AE/AWB, noise reduction, edge enhancement |
| **Stereo Exposure Sync** | **Medium** | Accurate depth via synchronized SDR |
| POI Cache | Low | Faster search |
| Electronic Horizon | Medium | Predictive features |
| Traffic Integration | Low | Real-time routing |
| BEV Drivable Area | Low | Better perception |
| LiDAR Support | Low | RK3688 only |
| Stop Line Detection | Medium | Safety feature |
| Dedicated Pedestrian/Cyclist | Medium | Safety feature |

---

## 8. Critical Differences

### 8.1 Safety: AEB

**VisionPilot has AEB, OpenPilot explicitly does not support it.**

This is an intentional design decision for OpenPilot. AEB is not planned or supported due to safety policy and liability considerations.

### 8.2 Convenience: LCA

**OpenPilot has LCA, VisionPilot does not.**

Lane Change Assist provides nudgeless automatic lane changing, a high-value convenience feature.

### 8.3 Navigation: Electronic Horizon

**VisionPilot has electronic horizon, OpenPilot does not.**

Electronic horizon provides predictive road data (curvature, slope, etc.) ahead of the vehicle position.

### 8.4 Voice: Wake Word

**Both have wake word detection.**

- OpenPilot: "Hey ExoPilot", "OK ExoPilot"
- VisionPilot: "Hi EXO"

---

## 9. Recommendations

### For OpenPilot

1. **~~Priority 1: Implement AEB~~** - Not supported by design
2. **~~Priority 2: Add wake word~~** - Already implemented
3. **Priority 3: Camera HDR + RKISP integration** - Critical for night ADAS
   - Implement OX03C10 on-chip HDR3 via V4L2 controls
   - Add RKIAQ ctypes bindings for hardware 3A
   - Maintain SDR for stereo (sync accuracy)
   - See [CAMERA_ISP_HDR_ARCHITECTURE.md](../eop/03_Software/Architecture/CAMERA_ISP_HDR_ARCHITECTURE.md)
4. **Priority 4: Electronic horizon** - Predictive features
5. **Priority 5: Stop line detection** - Safety feature

### For VisionPilot

1. **Priority 1: Implement LCA** - High-value convenience
2. **Priority 2: Add MTSC** - Better curve handling
3. **Priority 3: ELAT/ELON** - Enhanced stereo features
4. **Priority 4: LDW** - Safety feature

### For NavPilot Integration

NavPilot should detect device capabilities and adjust UI accordingly:

```dart
// Feature detection for NavPilot
if (device.hasAEB) showAEBStatus();
if (device.hasLCA) showLaneChangeButton();
if (device.hasWakeWord) showVoiceIndicator();
if (device.hasElectronicHorizon) showPredictiveSpeed();
```

---

## 10. Final Assessment

| Metric | Score |
|--------|-------|
| **Core ADAS Parity** | 90% ✅ |
| **Safety Parity** | 75% ⚠️ (AEB intentionally not in OpenPilot) |
| **Convenience Parity** | 75% ⚠️ |
| **Voice/AI Parity** | 75% ⚠️ |
| **Navigation Parity** | 71% ⚠️ |
| **Overall Parity** | **70%** |

**Conclusion:** Both projects have strong core ADAS features with ~70% parity. AEB is intentionally not supported in OpenPilot (design decision). Other gaps are primarily convenience features that don't affect core functionality.

For NavPilot integration, the key is **feature detection and graceful degradation** - the app should work well with either platform by detecting available capabilities.
