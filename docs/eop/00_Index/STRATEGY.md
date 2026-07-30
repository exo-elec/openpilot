# EOP Strategic Roadmap & Audit

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |

---


> **Research Date:** 2026-03-19
> **Reference Forks Analyzed:** FrogPilot, sunnypilot, dragonpilot, carrotpilot
> **Focus:** Core Vision features + NAVD (on-device Valhalla routing + NavPilot BLE)

---

## 1. Competitive Analysis

### 1.1 VisionPilot Gap Analysis (2026-04-20)

After comprehensive cross-analysis of VisionPilot v2.0 documentation (~2,400 lines across 3 comparison documents), the following strategic gaps have been identified:

| Priority | Gap | EOP Status | VisionPilot | Effort |
|----------|-----|------------|-------------|--------|
| 🔴 Critical | AEB Control Loop | ✅ Complete | ✅ Full | High |
| 🔴 Critical | SceneSeg Integration | ✅ Complete | ✅ | High |
| 🟡 Medium | BSD Standalone | ✅ Complete | ✅ | Low |
| 🟡 Medium | Whisper STT / Voice Pipeline | ✅ Complete | ✅ Full | Medium |

| 🟢 Lower | BEV Widget | ✅ Complete | ✅ | Low |
| 🟢 Lower | Theme System | ✅ Complete (dark only) | ✅ | Low |

**EOP Advantages to Maintain:**
- Lazy BEV Reprojection (10× perf gain — VisionPilot wants to adopt)
- DLAT State Machine with Hysteresis (VisionPilot item #3)
- CSLB Curve Speed Learning (VisionPilot item #5)
- CAT Adaptive Tuning (VisionPilot item #6)
- RK3588 Support (VisionPilot dropped it)
- Simpler architecture (~55K vs ~101K LOC)

See [VISIONPILOT_GAP_ANALYSIS.md](./VISIONPILOT_GAP_ANALYSIS.md) for full analysis.

---

### 1.2 Feature Implementation Comparison: Reference Forks

| Feature | FrogPilot | Sunnypilot | Dragonpilot | Carrotpilot | EOP Strategy |
|---------|-----------|------------|-------------|-------------|--------------|
| **TJA** | ✅ `human_acceleration` | ✅ `startAccel` | ✅ `CP.startAccel` | ✅ Basic | ✅ **Complete** |
| **VTSC** | ✅ CurveSpeedController | ✅ State machine | ❌ | ✅ Curve detection | ✅ **Complete** |
| **DLAT** | ✅ Conditional | ✅ `nnlc/` Neural LC | ❌ | ✅ Conditional | ✅ **Complete** |
| **DLON** | ✅ Conditional E2E | ✅ `dec.py` (388 lines) | ❌ Basic | ✅ Custom | ✅ **Complete** |
| **MTSC** | ✅ Speed limit filler | ✅ `map_controller.py` | ❌ | ✅ OSM curvature | ✅ **Complete** |
| **MSLC** | ✅ Speed limit control | ✅ `speedLimitControl` | ❌ | ✅ Speed camera DB | ✅ **Complete** |
| **ALCC** | ✅ `AlwaysOnLateral` | ✅ MADS | ✅ Basic | ✅ Always-on | ✅ **Complete** |
| **NAVD** | ✅ Valhalla offline | ✅ On-device routing | ❌ | ✅ Custom backend | ✅ **Complete** |
| **MAP UI** | ✅ Always-visible side | ✅ MapWindow side panel | ❌ | ✅ Side panel | ✅ **Complete** |

### 1.2 Implementation Complexity Analysis

| Feature | Lines of Code* | Dependencies | Risk Level |
|---------|---------------|--------------|------------|
| TJA | ~15 | None | Low |
| VTSC | ~100 | `modelV2` only | Low |
| ALCC | ~50 | `controlsd` | Medium |
| DLAT | ~200 | `modelV2`, lateral planner | Medium |
| DLON | ~400 | `modelV2`, longitudinal MPC | Medium |
| MTSC | ~250 | MAPD, GPS | Low |
| SOC | ~150 | YOLO, stereo | High |
| RED | ~200 | `modelV2`, `gridd` | High |

*Approximate based on reference fork implementations

### 1.3 Key Insights from Research

#### FrogPilot Strengths
- Most comprehensive feature set (500+ parameters)
- `CurveSpeedController` - excellent VTSC reference with calibration
- `FrogPilotAcceleration` - good longitudinal tuning patterns
- Well-structured `frogpilot/` namespace

**Weaknesses:**
- High complexity, heavy interdependencies
- Difficult to port individual features

#### Sunnypilot Strengths
- Cleanest architecture
- `dec.py` - best-in-class DLON implementation with:
  - Smooth Kalman filters for decision stability
  - Mode transition manager with hysteresis
  - Emergency override for critical situations
- `smart_cruise_control/` - well-modularized VTSC/MTSC
- `nnlc/` - neural network lateral control reference

**Weaknesses:**
- Some features tied to SP-specific cereal messages

#### Dragonpilot Strengths
- Minimal complexity
- Clean, simple implementations

**Weaknesses:**
- Fewer advanced features
- Less documentation

#### Carrotpilot Strengths (ajouatom — Korean fork)
- **BLE destination input**: NavPilot companion app sends destination via BLE SPP (NCP v4.1 `CMD_NAVIGATE`); `bluetoothd/spp.py` writes to `NavDestination` param; navd polls and routes — offline Valhalla, no API key needed
- `carrotd.py` master daemon cleanly separated in `selfdrive/carrot/` *(not implemented)* namespace
- Speed camera database integration (Korea + global) via T-map API
- No comma account required

**Weaknesses:**
- Korea-centric speed camera data
- Less modular than sunnypilot

---

## 2. EOP Recommended Implementation Roadmap

### Phase 1: Quick Wins (Weeks 1-2)

**Goal:** Deliver high user value with minimal risk

#### P1: TJA (Traffic Jam Assist)
- **Why:** Simplest implementation (~15 lines), proven in all forks
- **Reference:** FrogPilot `longcontrol.py`, sunnypilot `longcontrol.py`
- **Approach:** Fixed 2.0s acceleration ramp (0.25 → 1.2 m/s²)
- **File:** `selfdrive/controls/lib/longcontrol.py`
- **Doc:** TJA.md

#### P2: VTSC (Vision Turn Speed Control)
- **Why:** Well-documented, established math, high safety value
- **Reference:** FrogPilot `curve_speed_controller.py`, sunnypilot `vision_controller.py`
- **Approach:** Hybrid: FrogPilot curvature math + Sunnypilot state machine
- **File:** `selfdrive/controls/lib/vtsc.py`
- **Doc:** VTSC.md

### Phase 2: Core Intelligence (Weeks 3-6)

**Goal:** Enable E2E capabilities with robust switching logic

#### P3: DLAT (Dynamic Lateral Profile)
- **Why:** Enables E2E lateral when beneficial
- **Reference:** Sunnypilot `nnlc/` directory
- **Approach:** Heuristic probabilistic switcher (Laneful ↔ Laneless)
- **File:** `selfdrive/controls/lib/dlat.py`
- **Doc:** DLAT.md

#### P4: DLON (Dynamic Longitudinal Profile)
- **Why:** Contextual E2E longitudinal control
- **Reference:** Sunnypilot `dec.py` (primary), FrogPilot CEM (secondary)
- **Approach:** State machine with Kalman-filtered triggers
- **File:** `selfdrive/controls/lib/dlon.py`
- **Doc:** DLON.md

### Phase 3: Navigation Integration (Weeks 7-10) — ✅ COMPLETE

**Goal:** Proactive planning with map data

#### P5: MAPD + MTSC ✅
- **Why:** Long-range curve awareness
- **Reference:** Sunnypilot `map_controller.py`
- **Dependency:** MAPD OSM database
- **File:** `selfdrive/mapd/`, `selfdrive/controls/lib/mtsc.py`
- **Doc:** MTSC.md

#### P6: NAVD ✅
- **Why:** Navigation bridge from mobile app
- **File:** `selfdrive/navd/navd.py`, `selfdrive/navd/helpers.py`, `selfdrive/navd/tile_manager.py`, `selfdrive/navd/tile_auto_manager.py`
- **State:** On-device Valhalla offline routing + destination input via bluetoothd/NavDestination param + speed limit feed to MSLC

### Phase 5: NAVD

**Goal:** On-device offline navigation (no internet required).

#### P-NAV: NAVD — On-Device Valhalla Routing
- **Routing engine:** Valhalla (local C++ service on port 8002)
- **Tile source:** OpenStreetMap PBF → Valhalla graph tiles (offline)
- **Tile management:** `tile_manager.py` (manual) + `tile_auto_manager.py` (auto-download by GPS region)
- **Destination input:** `NavDestination` param (written by bluetoothd SPP, CLI, or companion app)
- **Architecture divergence:** Upstream/FrogPilot use online cloud routing APIs. EOP uses Valhalla (offline) + NavPilot BLE SPP for destination input.

**Architecture:**
```
Phone app / CLI / BLE SPP → NavDestination param
    ↓  navd polls params each loop
selfdrive/navd/navd.py  →  LOCAL Valhalla service (:8002)
    ↓
Parse maneuvers + speed limits from Valhalla response
    ↓
navInstruction / navRoute cereal (5 Hz)
    ↓                ↓
  MTSC           MSLC speed limit (SpeedLimitController)
```

- **New params:** `EOPNavEnabled`, `EOPAutoTileEnabled`, `EOPAutoTileWifiOnly`, `NavDestination`
- **Files:** `selfdrive/navd/navd.py`, `selfdrive/navd/helpers.py`, `selfdrive/navd/tile_manager.py`, `selfdrive/navd/tile_auto_manager.py`
- **Storage:** `/data/media/0/valhalla/` (tiles tar + config)

### Phase 4: Advanced Safety (Weeks 11-14)

**Goal:** Advanced safety and comfort features

#### P7: SOC (Smart Offset Controller)
- **Why:** Subjective safety improvement
- **Risk:** Higher - offset could push car into adjacent lane
- **File:** `selfdrive/pathd/soc.py`
- **Doc:** SOC.md

#### P8: RED (Road Edge Detection)
- **Why:** Safety guardrail for Laneless mode
- **Risk:** High - false positive = unnecessary intervention
- **File:** `selfdrive/controls/lib/red.py`
- **Doc:** RED.md

### Phase 6: VisionPilot Parity (Post-EOP10)

**Goal:** Close critical gaps identified in VisionPilot cross-analysis.

#### P-VP1: SceneSeg + AEB/RCD Unblocking
- **Why:** Single biggest safety gap vs VisionPilot
- **Dependency:** PP-LiteSeg scene segmentation model on NPU
- **Files:** `selfdrive/controls/lib/aeb.py`, new `selfdrive/safety/` *(not implemented)*
- **Blocked by:** Safety validation protocol

#### P-VP2: Voice Pipeline Completion
- **Why:** VisionPilot has full STT→NLU→LLM→TTS; EOP has wake word only
- **Components:**
  - Whisper STT integration in `voiced`
  - NLU intent classifier in `intentd`
  - Piper TTS in `soundd`
- **Effort:** Medium (4-6 weeks total)

#### P-VP3: BSD Standalone
- **Why:** Low effort, improves safety parity
- **File:** `selfdrive/controls/lib/bsd.py` or new `bsdd.py`
- **Effort:** Low (2-3 days)

---

## 3. Architectural Principles

### 3.1 Keep It Simple (KIS)
- Prefer FrogPilot's complexity level for user features
- Prefer Sunnypilot's architecture for core logic
- Avoid dragonpilot's limitations

### 3.2 Reference Fork Integration Pattern
```
Reference Fork Analysis
        ↓
   Identify Core Pattern
        ↓
   Simplify for EOP
        ↓
   Integrate with EOP Architecture
        ↓
   Document Differences
```

### 3.3 Safety-First Approach
- All features require explicit user toggle (`EOP<Feature>Enabled`)
- Default-off for experimental features
- Conservative tuning initially

---

## 4. Current Status Summary

### Core Controllers - Completed (✅)
- **TJA** - Traffic Jam Assist (`longcontrol.py`)
- **VTSC** - Vision Turn Speed Control (`vtsc.py`)
- **DLAT** - Dynamic Lateral Profile (`dlat.py`)
- **DLON** - Dynamic Longitudinal Profile (`dlon.py`)
- **MTSC** - Map Turn Speed Control (`mtsc.py`)
- **MSLC** - Map Speed Limit Control (`mslc.py`)
- **SOC** - Smart Offset Control (`pathd/soc.py`)
- **RED** - Road Edge Detection (`red.py`)
- **LCA** - Lane Change Assist (`desire_helper.py`)
- **ALCC** - Always Lane Centering (`controlsd.py`)

### Core Daemons - Completed (✅)
- Pipeline architecture (v4l2d, gridd, pathd, modeld)
- MAPD - Map Daemon (`selfdrive/mapd/`)
- NAVD - Navigation Daemon (`selfdrive/navd/`) — on-device Valhalla offline routing
- TRIPD - Trip Statistics (`selfdrive/tripd/`)
- BLE services (ELM327, Telemetry)
- Parameter infrastructure

### Enhanced Controllers - Completed (✅)
- **LatNudge** - Stereo Lateral Nudge (`selfdrive/pathd/lat_nudge.py`) — obstacle avoidance
- **LonNudge** - Stereo Longitudinal Nudge (`selfdrive/pathd/lon_nudge.py`) — drivable distance speed trim

### Enhanced Controllers - Blocked (⏸️)
- **AEB** - Requires safety validation
- **RCD** - Requires SceneSeg integration

### Next: VisionPilot Parity & AEB/RCD (⏸️ Blocked / Post-EOP10)
- **AEB** — safety validation and SceneSeg integration required
- **RCD** — SceneSeg integration required
- **Voice Pipeline** — Whisper STT + NLU + Piper TTS (4-6 weeks)
- **BSD** — standalone blind spot detection (2-3 days)

---

## 5. Next Actions

### All Implementable Features Complete (✅)
NAVD, MAP-PANEL, LatNudge, LonNudge, and all core features are implemented.
41 of 43 tracked features are done.

### Blocked (⏸️)
- **AEB** — safety validation and test protocol required
- **RCD** — SceneSeg pipeline integration required

### Platform Strategy
- EOP v1 targets RK3588 (LubanCat-5) exclusively
- ExoPilot 02M (RK3576, RPDZKJ RongPin) is VisionPilot's platform (branch EVP09); it reuses proven EOP components (MAPD, NAVD, LatNudge, LonNudge, RTK stack)
- MapLibre GL Native (QMapLibre) selected as map renderer — already in codebase

### Remaining Blocked
- **AEB** — Autonomous Emergency Braking (safety validation required)
- **RCD** — Road Condition Detection (SceneSeg integration required)

See IMPLEMENTATION_STATUS.md for detailed tracking.

---

## 6. Historical Code Audit Log

### 2026-03-19 (continued) — Integration Fixes

**Fixed:**
- `cereal/log.capnp`: Restored `struct NavInstruction` (upstream had removed it 2025-11-09 as Void)
- `selfdrive/navd/navd.py`: Removed broken BLE asyncio thread (was using non-existent `BleakServer`); BLE SPP moved to `system/bluetoothd/spp.py`; `_publish_route()` corrected to use `navRoute.coordinates` only
- `selfdrive/controls/plannerd.py`: Added `mapData` + `navInstruction` to SubMaster (both were missing — MTSC/MSLC updates were silently skipped)
- `selfdrive/controls/lib/mslc.py`: Added `nav_speed_limit_ms` fallback — uses Valhalla navInstruction speed limit when OSM has no data (OSM first, navd fallback)
- `selfdrive/navd/navd.py`: Fixed `speedLimit` published in km/h instead of m/s (schema is m/s)
- `selfdrive/pathd/pathd.py`: `nav_data` was hardcoded `None`; added `_NavData`/`_NavInstr` adapters + `navInstruction` SubMaster subscription — nav-aware collision braking now active

### 2026-03-19 — NAVD + RK3576 Map Panel Planning

**Analysis Conducted:**
- Added carrotpilot to fork reference list (cloud-free routing + BLE destination)
- Determined current `navd.py` needed rewrite for Valhalla offline routing
- Confirmed QMapLibre (MapLibre GL Native) already present in `selfdrive/ui/qt/maps/`
- Confirmed `OnroadWindow::split` QHBoxLayout is the correct injection point for map panel
- Designed NAVD architecture: on-device Valhalla routing, NavPilot BLE SPP destination input

**Key Decisions:**
1. **On-device routing** — phone sends destination only; routing runs on device (not phone)
2. **QMapLibre already present** — no new dependency needed for map rendering
3. **RK3576 map panel** — left side of 1600×600 display (576 px fixed width), toggleable
4. **Speed limit from Valhalla** — `maxspeed` annotations feed `MSLC` (sunnypilot pattern)
5. **EOP → EVP transition** — EOP serves RK3588 + RK3576; EVP is the next-gen platform

### 2026-03-18 — ELAT, ELON, RTK Stack Complete

**Completed:**
- ELAT (`selfdrive/pathd/lat_nudge.py`) — stereo boundary + obstacle lateral avoidance
- ELON (`selfdrive/pathd/lon_nudge.py`) — drivable distance speed trim + TTC lead tracking
- PathD integration — 7→33pt interpolation, speed delta pipeline
- ZED-F9P baud negotiation + NTRIP daemon + RTK params/UI
- Code quality fixes: unused imports, inline import in hot loop

### 2026-03-15 — Reference Fork Research

**Research Conducted:**
- Analyzed FrogPilot, sunnypilot, dragonpilot implementations
- Documented feature patterns and complexity
- Created implementation priority matrix
- Designed TJA feature document

**Key Decisions:**
1. **TJA as P1** - Simplest implementation, highest user value
2. **Hybrid approach** - Combine best patterns from multiple forks
3. **Sunnypilot DEC** - Adopt as primary DLON reference
4. **FrogPilot CSC** - Adopt curvature math for VTSC

### Critical Fixes Applied (Previous)
1. **CRITICAL A - pathd.py:** Fixed dead code in collision avoidance alert pipeline.
2. **CRITICAL B - controlsd.py:** Fixed velocity/acceleration unit mismatch in speedAdjustment.
3. **WARNING C - v4l2d.py:** Fixed hardcoded camera paths with dynamic sensor-aware discovery.
4. **STYLE - Daemon Naming:** Unified all daemons to the `CamelCaseD` pattern.
5. **PREFIX - Parameter Strategy:** Standardized on `EOP` prefix for all project extensions.

---

## 7. Related Documents

- CORE_FEATURES_INDEX.md - Complete feature index
- [OVERVIEW.md](./OVERVIEW.md) - EOP architecture overview
- [NAMING_CONVENTIONS.md](../01_Core/NAMING_CONVENTIONS.md) - Coding standards
- IMPLEMENTATION_STATUS.md - Implementation status
- [VISIONPILOT_GAP_ANALYSIS.md](./VISIONPILOT_GAP_ANALYSIS.md) - VisionPilot cross-analysis

**Reference Forks:**
- FrogPilot - Comprehensive features
- sunnypilot - Clean architecture
- dragonpilot - Minimal complexity
