# EOP Localization & Perception Design Index

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |

---


**Version**: 1.0  
**Last Updated**: 2026-04-04

---

## Quick Navigation

### 🎯 Start Here
| Document | Purpose | Read Time |
|----------|---------|-----------|
| **LOCALIZATION_PERCEPTION_PIPELINE.md** | Complete system architecture | 20 min |

### 📚 Component Deep Dives
| Document | Component | Focus Area |
|----------|-----------|------------|
| MAP_MATCHING_LOCALIZATION.md | osm_localizer | HMM map matching to OSM roads |
| LANE_LEVEL_LOCALIZATION.md | pointcloudd | VIO tracking in road frame |
| FREE_SPACE_LOCALIZATION.md | surfaced | Boundary-based free space detection |
| ROAD_SIDE_LOCALIZATION.md | surfaced/gridd | Heading-based road side detection |

### 🔧 Implementation References
| Document | Purpose |
|----------|---------|
| REFACTOR_SURFACE.md | Surface daemon refactoring plan |
| SURFACE_ARCHITECTURE.md | Legacy surface architecture |
| SURFACED.md | Surface daemon documentation |

### 🚀 Hardware Acceleration
| Document | Purpose |
|----------|---------|
| DESIGN_ACCELERATION.md | Hardware acceleration architecture |
| GPU_ACCELERATION.md | GPU acceleration guide |
| SYSTEM_LIBRARIES.md | System library strategy |
| ARM_COMPUTE_LIBRARY.md | ARM Compute Library integration |
| OPENCL_VS_VULKAN.md | OpenCL vs Vulkan comparison |
| MPP_INTEGRATION.md | Rockchip MPP video codec |

---

## System Overview

### The Problem

Non-RTK GPS has **±3-5 meter drift**. Road width is ~3.5m, lane width ~3m.
**GPS alone cannot determine lane position!**

### The Solution

```
┌─────────────────────────────────────────────────────────────────┐
│                    LAYERED LOCALIZATION                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  LAYER 1: Map Matching                                          │
│  GPS (±5m) ──► HMM Map Match ──► Road ID + Rough Position (±1m) │
│                                                                 │
│  LAYER 2: Road-Relative VIO                                     │
│  Road Frame + VIO ──► Precise Tracking (±0.1m)                  │
│                                                                 │
│  LAYER 3: Visual Refinement                                     │
│  Point Cloud + Boundaries ──► Lane-Level Position (±0.05m)      │
│                                                                 │
│  LAYER 4: Temporal Fusion                                       │
│  Current + Historical ──► Enhanced Perception                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Key Innovation

**Don't fight GPS drift with visual features!**

Instead:
1. **Snap GPS to road geometry** (map matching)
2. **Track within road frame** (VIO)
3. **Refine with visual features** (free space detection)

Result: **Lane-level accuracy without RTK GPS!**

---

## Pipeline Flow

```
┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────┐
│          │    │              │    │              │    │              │    │          │
│ pigeond  │───►│osm_localizer │───►│ pointcloudd  │───►│   surfaced   │───►│  gridd   │
│          │    │              │    │              │    │              │    │          │
│  GPS     │    │  Map Match   │    │  VIO + Store │    │ Free Space + │    │ Cost Map │
│  @10Hz   │    │   @10Hz      │    │   @20Hz      │    │  Temporal    │    │  @20Hz   │
│          │    │              │    │              │    │   @20Hz      │    │          │
└──────────┘    └──────────────┘    └──────────────┘    └──────────────┘    └──────────┘
     │                 │                  │                  │                 │
     │                 │                  │                  │                 │
     ▼                 ▼                  ▼                  ▼                 ▼
┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────┐
│ lat/lon  │    │  road_id     │    │  s, y, θ     │    │  drivable    │    │  grid    │
│ accuracy │    │  progress    │    │  road frame  │    │  area +      │    │  objects │
│  ±3-5m   │    │  lateral     │    │  pose        │    │  clearances  │    │          │
│          │    │  confidence  │    │              │    │              │    │          │
└──────────┘    └──────────────┘    └──────────────┘    └──────────────┘    └──────────┘
```

### Message Flow

| Step | From | To | Message | Key Data |
|------|------|-----|---------|----------|
| 1 | pigeond | osm_localizer | gpsLocation | lat, lon, heading |
| 2 | osm_localizer | pointcloudd | mapMatch | road_id, progress, lateral_offset |
| 3 | stereod | pointcloudd | stereoDepth | point cloud |
| 4 | pointcloudd | surfaced | pointcloudProcessed | filtered points |
| 4 | pointcloudd | surfaced | roadFramePose | s, y, θ, roadSide |
| 5 | surfaced | gridd | drivableArea | BEV grid + pose |
| 5 | surfaced | controls | surfaceStatus | obstacles, quality |
| 6 | gridd | pathd | gridObjects | cost map |

---

## Design Principles

### 1. Layer Separation

| Layer | Responsibility | Input | Output |
|-------|---------------|-------|--------|
| Map Matching | Road identification | GPS trajectory | road_id, rough position |
| VIO Tracking | Precise local tracking | IMU + points | road frame pose |
| Visual Refinement | Boundary detection | Point cloud | Clearances |
| Temporal Fusion | Historical context | Current + history | Enhanced perception |

### 2. Coordinate Frame Hierarchy

```
GPS (global, ±5m)
    │
    ▼ (map matching)
Road Frame (local persistent, ±1m)
    │
    ▼ (VIO tracking)
Road Frame + VIO (±0.1m)
    │
    ▼ (visual refinement)
Precise Road Pose (±0.05m)
    │
    ▼ (grid generation)
BEV Grid (planning frame)
```

### 3. Failure Modes

| Scenario | Detection | Response |
|----------|-----------|----------|
| GPS outage | No GPS for >2s | Continue VIO, mark reduced confidence |
| Map match fail | No candidate roads | Use vehicle frame, mark unknown |
| Wrong way | Heading opposite road | Alert driver, reduce speed |
| VIO drift | s mismatch with GPS | Soft correction from map match |

---

## Implementation Roadmap

### Phase 1: Foundation (Weeks 1-2)
- [ ] osm_localizer: HMM map matching
- [ ] pointcloudd: Object filter + VIO skeleton
- [ ] Cereal messages: mapMatch, roadFramePose

### Phase 2: Core Tracking (Weeks 3-4)
- [ ] pointcloudd: Road-relative VIO
- [ ] pointcloudd: Road-segmented storage
- [ ] surfaced: Free space detector

### Phase 3: Refinement (Weeks 5-6)
- [ ] surfaced: Visual refinement
- [ ] surfaced: Road-side detection
- [ ] gridd: Road-aware cost maps

### Phase 4: Enhancement (Weeks 7-8)
- [ ] surfaced: Temporal matcher
- [ ] Loop closure detection
- [ ] Multi-session mapping

### Phase 5: Validation (Weeks 9-10)
- [ ] Unit tests for all components
- [ ] Integration tests
- [ ] Field testing
- [ ] Performance optimization

---

## File Structure

```
selfdrive/
├── locationd/
│   └── osm_localizer/
│       ├── osm_localizer.py      # Main daemon
│       ├── hmm_matcher.py        # HMM algorithm
│       └── osm_cache.py          # Local OSM storage
│
├── pointcloudd/
│   ├── pointcloudd.py            # Main daemon
│   ├── filters/
│   │   └── object_filter.py      # Dynamic object removal
│   ├── vio/
│   │   ├── road_vio.py           # Road-relative VIO
│   │   └── pose_tracker.py       # Pose tracking
│   └── storage/
│       └── road_storage.py       # Road-segmented PCD storage
│
├── surfaced/
│   ├── surfaced.py               # Main daemon
│   ├── freespace/
│   │   ├── detector.py           # Free space detection
│   │   └── boundary_detector.py  # Boundary extraction
│   ├── matcher/
│   │   └── temporal_matcher.py   # Historical data matching
│   └── bev/
│       └── bev_extractor.py      # BEV grid extraction
│
└── gridd/
    ├── gridd.py                  # Main daemon (modified)
    └── costmap/
        └── road_aware_costmap.py # Road-side context

cereal/
├── log.capnp                     # Existing messages
└── custom.capnp                  # EOP-specific messages
    ├── MapMatch                  # osm_localizer output
    ├── RoadFramePose            # pointcloudd output
    ├── DrivableArea             # surfaced output
    └── SurfaceStatus            # surfaced output
```

---

## Dependencies

### External
- OSM data (offline cache)
- PCL or Open3D (point cloud processing)
- Eigen (linear algebra)

### Internal
- stereod (point cloud input)
- pigeond (GPS input)
- imud (IMU input)
- mapd (OSM data provider)

---

## Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| End-to-end latency | <100ms | From GPS to gridObjects |
| Map matching | <50ms | HMM with 50m radius |
| VIO tracking | <30ms | IMU + point cloud |
| Free space detection | <40ms | BEV grid generation |
| Position accuracy | <0.1m | Within road frame |
| Lateral accuracy | <0.05m | Lane-level precision |

---

## Success Criteria

1. **Lane-level localization**: ±0.1m lateral accuracy on known roads
2. **Robust to GPS drift**: Works with ±5m GPS error
3. **Real-time performance**: 20Hz output on RK3588
4. **Wrong-way detection**: >95% accuracy
5. **Free space detection**: >90% boundary detection rate
6. **Temporal fusion**: Improved perception on repeated routes

---

## Related Documents

- LOCALIZATION_IMPROVEMENT.md - High-level architecture
- MAP_MATCHING_LOCALIZATION.md - HMM map matching details
- LANE_LEVEL_LOCALIZATION.md - VIO and road frame
- FREE_SPACE_LOCALIZATION.md - Boundary detection
- ROAD_SIDE_LOCALIZATION.md - Heading-based side detection
- REFACTOR_SURFACE.md - Surface daemon refactoring
- SURFACED.md - Surface daemon docs

---

## Glossary

| Term | Definition |
|------|------------|
| VIO | Visual-Inertial Odometry |
| HMM | Hidden Markov Model |
| OSM | OpenStreetMap |
| BEV | Bird's Eye View |
| PCD | Point Cloud Data (file format) |
| RTK | Real-Time Kinematic (GPS correction) |
| s | Distance along road (longitudinal) |
| y | Lateral offset from road center |
| θ | Heading relative to road direction |
| RHT | Right-Hand Traffic |
| LHT | Left-Hand Traffic |

---

## Document History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-04-04 | Initial comprehensive design |
