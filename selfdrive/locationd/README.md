# Location Stack

**High-precision localization for OpenPilot.**

## Overview

This directory contains the localization stack, which provides accurate position, velocity, and orientation estimates.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        LOCALIZATION STACK                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  VELOCITY & ORIENTATION (20Hz)                                              │
│  └── locationd/ ──► High-rate EKF for control                               │
│      ├── locationd.py ──► Main daemon                                       │
│      └── models/ ──► Kalman filter implementations                          │
│                                                                             │
│  LOCALIZERS (5Hz)                                                           │
│  ├── osm_localizer/ ──► Road network matching                               │
│  │   └── osm_localizer.py                                                   │
│  │                                                                          │
│  └── sgm_localizer/ ──► Stereo geometry matching                            │
│      └── sgm_localizer.py                                                   │
│                                                                             │
│  GLOBAL POSITION (5Hz)                                                      │
│  └── coordinationd/ ──► Position fusion with road constraints                     │
│      └── coordinationd.py                                                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Descriptions

| Component | Rate | Output | Purpose |
|-----------|------|--------|---------|
| **locationd** | 20Hz | Velocity, orientation | Control loop |
| **osm_localizer** | 5Hz | osmCorrectedPose | Road network constraint |
| **sgm_localizer** | 5Hz | sgmCorrectedPose | Road geometry constraint |
| **coordinationd** | 5Hz | fusedPosition | Fused absolute position |

## Key Design

- **locationd** = High-rate velocity/orientation (for control)
- **coordinationd** = Low-rate absolute position (for navigation)
- **osm_localizer + sgm_localizer** = Position corrections

See `docs/eop/03_Software/LOCALIZATION_STACK.md` for complete documentation.
