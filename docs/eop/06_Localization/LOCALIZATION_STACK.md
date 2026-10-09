# Localization Stack

**Unified Localization** - OSM + SGM + GPS fusion.

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **coordinationd** | ✅ Merged daemon |
| **OSM Module** | ✅ In coordinationd |
| **SGM Module** | ✅ In coordinationd |

---

## Architecture

```
┌─────────────┐
│   GPS       │────┐
│  (pigeond)  │    │
└─────────────┘    │    ┌─────────────┐     ┌─────────────┐
                   ├───▶│   coordinationd   │────▶│ fusedPosition│
┌─────────────┐    │    │   (fusion)  │     │   (ECEF)    │
│   OSM       │────┘    └─────────────┘     └─────────────┘
│  (mapd)     │         │                    │
└─────────────┘         │                    ▼
                        │            ┌─────────────┐
┌─────────────┐         │            │   pathd     │
│   SGM       │─────────┘            │   navd      │
│ (pointcloud)│                      └─────────────┘
└─────────────┘
```

---

## Components

| Component | Function | Status |
|-----------|----------|--------|
| pigeond | GPS input | ✅ |
| mapd | OSM data | ✅ |
| pointcloudd | 3D reconstruction | ✅ |
| coordinationd | Fusion | ✅ |

---

## See Also

- GLOBALD
- MAP_MATCHING_LOCALIZATION.md
