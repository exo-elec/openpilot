# ExoPilot Database Schema v1.0

Shared database format for ExoPilot hardware platform.

## Location

```
/data/shared/exopilot/
├── surface.db    # Surface quality data
└── curve.db      # Curve speed data
```

## Surface Quality Table

### Schema

```sql
CREATE TABLE surface_quality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    quality_score REAL NOT NULL,
    timestamp REAL NOT NULL,
    source TEXT DEFAULT 'exopilot01m',  -- ExoPilot platform: exopilot01m, exopilot02m, exopilot03m
    heading REAL DEFAULT 0.0,
    texture TEXT DEFAULT 'unknown',
    confidence REAL DEFAULT 0.0,
    pass_count INTEGER DEFAULT 1,
    roughness_rms REAL,
    recommended_speed_mps REAL,
    geohash TEXT,
    _schema_version TEXT DEFAULT '1.0'
);
```

### Columns

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| id | INTEGER | Yes | Auto-increment primary key |
| lat | REAL | Yes | GPS latitude |
| lon | REAL | Yes | GPS longitude |
| quality_score | REAL | Yes | 0.0 (smooth) to 1.0 (very rough) |
| timestamp | REAL | Yes | Unix timestamp |
| source | TEXT | No | Hardware platform identifier (rk3588, rk3576, etc.) |
| heading | REAL | No | Vehicle heading in degrees |
| texture | TEXT | No | 'smooth', 'normal', 'rough', 'very_rough' |
| confidence | REAL | No | 0.0 to 1.0 |
| pass_count | INTEGER | No | Number of passes over this location |
| roughness_rms | REAL | No | IMU roughness measurement |
| recommended_speed_mps | REAL | No | Learned safe speed |
| geohash | TEXT | No | For spatial indexing |
| _schema_version | TEXT | No | Schema version |

### Indexes

- `idx_surface_geohash` - Fast spatial queries
- `idx_surface_timestamp` - Time-based queries
- `idx_surface_source` - Filter by hardware platform

## Curve Speeds Table

### Schema

```sql
CREATE TABLE curve_speeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    heading REAL NOT NULL,
    curvature REAL NOT NULL,
    speed_mps REAL NOT NULL,
    timestamp REAL NOT NULL,
    source TEXT DEFAULT 'exopilot01m',  -- Auto-detected platform identifier
    speed_confidence REAL DEFAULT 0.0,
    sample_count INTEGER DEFAULT 1,
    road_hash TEXT,
    _schema_version TEXT DEFAULT '1.0'
);
```

### Columns

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| id | INTEGER | Yes | Auto-increment primary key |
| lat | REAL | Yes | GPS latitude |
| lon | REAL | Yes | GPS longitude |
| heading | REAL | Yes | Vehicle heading in degrees |
| curvature | REAL | Yes | 1/m (1/radius) |
| speed_mps | REAL | Yes | Learned speed in m/s |
| timestamp | REAL | Yes | Unix timestamp |
| source | TEXT | No | Hardware platform identifier |
| speed_confidence | REAL | No | 0.0 to 1.0 |
| sample_count | INTEGER | No | Number of samples |
| road_hash | TEXT | No | Road identifier |
| _schema_version | TEXT | No | Schema version |

### Indexes

- `idx_curve_latlon` - Fast spatial queries
- `idx_curve_source` - Filter by hardware platform

## ExoPilot Hardware Platforms

| Platform | SoC | Source Value | NPU TOPS | Description |
|----------|-----|--------------|----------|-------------|
| ExoPilot 01M | RK3588 | `exopilot01m` | 6 | Entry ADAS (openpilot) |
| ExoPilot 02M | RK3576 | `exopilot02m` | 6 | Standard ADAS (openpilot/VisionPilot) |
| ExoPilot 03M | RK3688 | `exopilot03m` | 12 | DoraPilot platform — different software |

## Schema Evolution

### Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-04 | Initial schema |

### Adding Columns

New columns must:
1. Have a DEFAULT value
2. Be optional (nullable)
3. Not change existing columns

Example:
```sql
ALTER TABLE surface_quality ADD COLUMN new_field REAL DEFAULT 0.0;
```

### Breaking Changes

Major version bumps (e.g., 1.0 -> 2.0) require coordination between hardware platforms.

## Example Usage

### Python

```python
import sqlite3

# Write with hardware platform attribution
conn = sqlite3.connect("/data/shared/exopilot/surface.db")
conn.execute("""
    INSERT INTO surface_quality 
    (lat, lon, quality_score, timestamp, source)
    VALUES (?, ?, ?, ?, 'rk3588')
""", (35.0, 139.0, 0.5, time.time()))
conn.commit()

# Read all data
conn = sqlite3.connect("/data/shared/exopilot/surface.db")
cursor = conn.execute("""
    SELECT * FROM surface_quality 
    WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
""", (lat_min, lat_max, lon_min, lon_max))
```

## Notes

- Database location is hardware-platform neutral (`/data/shared/exopilot/`)
- Source field identifies hardware platform, not software project
- Compatible with multiple software implementations on same hardware
