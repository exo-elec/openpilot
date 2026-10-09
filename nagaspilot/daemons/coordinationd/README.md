# Coordination Daemon (coordinationd)

**ECEF position fusion with road constraints.**

See full documentation:
- `docs/eop/06_Localization/LOCALIZATION_STACK.md` - Complete stack overview

## Quick Start

```bash
# Enable coordinationd
python3 -c "from openpilot.common.params import Params; Params().put_bool('EOPGlobaldEnabled', True)"

# Enable OSM road constraint
python3 -c "from openpilot.common.params import Params; Params().put_bool('EOPOsmLocalizerEnabled', True)"

# Enable SGM geometry constraint  
python3 -c "from openpilot.common.params import Params; Params().put_bool('EOPSGMLocalizerEnabled', True)"
```

## Architecture

```
locationd (20Hz) ──► livePose (velocity/orientation)
       ↓
coordinationd (5Hz) fuses:
  ├── GNSS (GPS/RTK)
  ├── OSM (road network constraint)
  └── SGM (stereo geometry confirmation)
       ↓
fusedPosition (ECEF with road constraints)
```

## Output Message

- `fusedPosition` - ECEF position, lat/lon, confidence, road info

## Why ECEF?

Even though the final output includes lat/lon for display, all **fusion math is done in ECEF** (Earth-Centered, Earth-Fixed coordinates) because:

1. **GNSS receivers output ECEF natively** - No conversion needed for GPS
2. **Simple math** - Distance = `√((x2-x1)² + (y2-y1)² + (z2-z1)²)` vs complex spherical formulas
3. **Linear averaging** - Can directly average positions: `(p1 + p2) / 2`
4. **Kalman filter friendly** - Matrix operations assume flat Cartesian space
5. **No pole/meridian issues** - No discontinuity at 180° longitude

**Pipeline:** Convert all inputs to ECEF → Fuse → Output both ECEF and lat/lon

## Dependencies

- `livePose` from locationd (velocity)
- `gnssMeasurements` from pigeond (GNSS)
- `osmCorrectedPose` from osm_localizer (optional)
- `sgmCorrectedPose` from sgm_localizer (optional)
