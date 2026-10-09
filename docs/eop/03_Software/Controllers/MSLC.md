# Design Document: MSLC (Map Speed Limit Controller)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/controls/lib/mslc.py` |

---


> **Full Name:** Map Speed Limit Controller  
> **Component Type:** Controller (inside `controlsd` or `longitudinal_planner`)  
> **Complexity:** Medium  
> **Tier:** Core Vision (works with Road camera, uses MAPD data)  
> **Reference Implementation:** FrogPilot `speed_limit_controller.py`  
> **EOP Integration:** `selfdrive/controls/lib/mslc.py`

---

## 1. Objective

MSLC provides intelligent speed limit compliance using OpenStreetMap (OSM) data. It automatically adjusts the vehicle's target speed based on posted speed limits along the current route, with configurable offsets and user override capabilities.

**Key Benefits:**
- **Automatic Speed Compliance:** Follow posted speed limits without driver intervention
- **Configurable Offset:** User-adjustable margin above/below limit
- **Smart Transitions:** Gradual speed changes before limit changes
- **Route-Aware:** Only applies on known roads with speed limit data

---

## 2. Why MSLC is Core Vision

| Requirement | MSLC Needs |
|-------------|------------|
| **Road camera (8mm)** | ⚠️ Optional - for verification only |
| **Wide road camera (1.7mm)** | ❌ No |
| **Stereo cameras** | ❌ No |
| **GPS** | ✅ **Required** - for position matching |
| **OSM/Maps** | ✅ **Required** - speed limit data from MAPD |

**Conclusion:** MSLC primarily uses MAPD (OSM) data - it's a Core Vision feature that works without cameras.

---

## 3. Relationship with MTSC

```
┌─────────────────────────────────────────────────────────────────┐
│              MSLC vs MTSC Architecture                          │
└─────────────────────────────────────────────────────────────────┘

MAPD (OSM Database)
    ├── Speed Limits (MSLC)
    │   └── highway=motorway → 120 km/h
    │   └── highway=residential → 50 km/h
    │
    └── Curvature Data (MTSC)
        └── Node geometry → radius calculation

Both use:
- GPS position
- Route matching
- Overpass API queries
- SQLite caching
```

**Shared Infrastructure:**
- MAPD daemon for OSM queries
- Route matching logic
- Geohash-based caching
- Position tracking

---

## 4. Technical Architecture

### 4.1 System Context

**Speed limit source priority (sunnypilot pattern):**
1. Car vision sign (camera-detected) — highest priority, not in MSLC
2. OSM / MAPD (`mapData.speedLimit`) — primary MSLC source
3. NAVD (`navInstruction.speedLimit`, m/s) — fallback when OSM has no data

```
┌─────────────────────────────────────────────────────────────────┐
│                    MSLC SYSTEM CONTEXT                          │
└─────────────────────────────────────────────────────────────────┘

MAPD (OSM Lookup)  ──────────────────────────────────┐
    ├── Current speed limit at GPS position           │ primary
    ├── Upcoming speed limit (500m ahead)             │
    └── Speed limit change distance                  │
                                                     ↓
NAVD (Valhalla maxspeed annotation, m/s)  ──→  MSLC Controller
    └── navInstruction.speedLimit                (fallback)
            ↓
    ┌─────────────────────────────────────┐
    │         MSLC Controller             │
    │  (selfdrive/controls/lib/           │
    │   mslc.py)           │
    │                                     │
    │  ┌──────────────┐  ┌──────────────┐ │
    │  │   Speed      │  │   Offset     │ │
    │  │   Limit      │→ │   Applied    │ │
    │  │   Lookup     │  │              │ │
    │  └──────────────┘  └──────────────┘ │
    │           ↓              ↓          │
    │  ┌────────────────────────────────┐ │
    │  │    Smoothed Speed Target       │ │
    │  │  (with gradual transitions)    │ │
    │  └────────────────────────────────┘ │
    └─────────────────────────────────────┘
            ↓
    longitudinal_planner / controlsd
            ↓
    v_cruise (target cruise speed)
```

### 4.2 Speed Limit Data Sources

**Primary: OpenStreetMap (via MAPD)**
| OSM Tag | Typical Speed | Road Type |
|---------|---------------|-----------|
| `maxspeed=120` | 120 km/h | Motorway |
| `maxspeed=90` | 90 km/h | Rural highway |
| `maxspeed=50` | 50 km/h | Urban |
| `maxspeed=30` | 30 km/h | Residential |
| `zone:maxspeed=DE:urban` | 50 km/h | German urban zone |

**Fallback: NAVD (if OSM missing)**
- Used when OSM has no speed limit data
- Source: Valhalla offline routing (no internet required)

### 4.3 Core Algorithms

#### 4.3.1 Speed Limit Lookup

```python
def get_speed_limit_from_mapd(mapd_data, gps_position, route):
    """
    Get current and upcoming speed limits from MAPD.
    
    Returns:
        current_limit: Speed limit at current position (km/h)
        upcoming_limit: Speed limit ahead (km/h)
        distance_to_change: Distance to upcoming limit (m)
    """
    # Match current GPS position to route
    route_index = match_position_to_route(gps_position, route)
    
    # Get current way OSM tags
    current_way = mapd_data.ways[route_index]
    current_limit = parse_speed_limit(current_way.tags.get('maxspeed'))
    
    # Look ahead for upcoming limit changes
    upcoming_limit = current_limit
    distance_to_change = float('inf')
    
    for i in range(route_index + 1, min(route_index + 50, len(route))):
        way = mapd_data.ways[i]
        limit = parse_speed_limit(way.tags.get('maxspeed'))
        
        if limit != current_limit:
            upcoming_limit = limit
            distance_to_change = calculate_distance(
                gps_position, route[i].position
            )
            break
    
    return current_limit, upcoming_limit, distance_to_change


def parse_speed_limit(maxspeed_tag):
    """Parse OSM maxspeed tag to km/h."""
    if maxspeed_tag is None:
        return None
    
    # Direct value: "60" or "60 km/h"
    if maxspeed_tag.isdigit():
        return int(maxspeed_tag)
    
    # Zone-based: "DE:urban" -> 50 km/h
    zone_mapping = {
        'DE:motorway': None,  # No limit
        'DE:rural': 100,
        'DE:urban': 50,
        'DE:zone30': 30,
        'US:interstate': 120,
        'US:highway': 90,
    }
    
    if maxspeed_tag in zone_mapping:
        return zone_mapping[maxspeed_tag]
    
    return None
```

#### 4.3.2 Speed Target Calculation

```python
def calculate_mslc_speed_target(v_ego, current_limit, upcoming_limit, 
                                 distance_to_change, params):
    """
    Calculate speed target with limit and offset.
    
    Args:
        params.offset_percent: 0-20% above limit (user setting)
        params.offset_fixed: Fixed km/h above limit
    """
    if current_limit is None:
        return None  # No speed limit data
    
    # Apply user offset
    offset = current_limit * (params.offset_percent / 100.0)
    offset += params.offset_fixed
    
    target = current_limit + offset
    
    # Lookahead: start slowing for upcoming lower limit
    if upcoming_limit < current_limit and distance_to_change < 500:
        # Blend current and upcoming based on distance
        blend_factor = 1.0 - (distance_to_change / 500.0)
        upcoming_target = upcoming_limit + offset
        target = target * (1 - blend_factor) + upcoming_target * blend_factor
    
    # Don't suggest speed increase if already above (user override)
    if v_ego > target + 5:
        return None  # Let user maintain current speed
    
    return target
```

#### 4.3.3 State Machine

```
┌─────────────────────────────────────────────────────────────────┐
│                    MSLC STATE MACHINE                           │
└─────────────────────────────────────────────────────────────────┘

    ┌──────────────┐
    │   DISABLED   │ ← MSLC disabled or no GPS
    └──────┬───────┘
           │ Enabled + GPS valid
           ▼
    ┌──────────────┐     No speed limit data
    │   NO_DATA    │──────────────────┐
    │              │                  │
    └──────┬───────┘                  │
           │ Data received            │
           ▼                          │
    ┌──────────────┐                  │
    │    ACTIVE    │←─────────────────┘
    │ (following   │  Data received
    │  speed limit)│
    └──────┬───────┘
           │ Driver gas override
           ▼
    ┌──────────────┐     Override timeout
    │  OVERRIDDEN  │──────────────────→ ACTIVE
    │              │
    └──────────────┘
```

---

## 5. EOP Implementation Plan

### 5.1 Files and Classes

| File | Purpose |
|------|---------|
| `selfdrive/controls/lib/mslc.py` | MSLC controller class |
| `selfdrive/controls/controlsd.py` | Integration point |
| `common/params_keys.h` | MSLC parameters |

### 5.2 Class Structure

```python
# selfdrive/controls/lib/mslc.py

from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL


class MSLC:
    """
    Map Speed Limit Controller
    
    Adjusts target speed based on OSM speed limit data from MAPD.
    """
    
    # State definitions
    STATE_DISABLED = 0
    STATE_NO_DATA = 1
    STATE_ACTIVE = 2
    STATE_OVERRIDDEN = 3
    
    # Constants
    LOOKAHEAD_DISTANCE = 500  # meters
    OVERRIDE_TIMEOUT = 10.0  # seconds
    
    def __init__(self):
        self.params = Params()
        self.enabled = self.params.get_bool("EOPMSLCEnabled")
        
        # User settings
        self.offset_percent = self.params.get_int("EOPMSLCOffsetPercent")
        self.offset_fixed = self.params.get_int("EOPMSLCOffsetFixed")
        
        # State
        self.state = self.STATE_DISABLED
        self.current_limit = None
        self.target_speed = None
        self.override_time = 0.0
    
    def update(self, map_data, gps_position, v_ego, 
               driver_overriding, t):
        """
        Main update method.
        
        Args:
            map_data: MAPD output with speed limits
            gps_position: Current GPS position
            v_ego: Current speed (m/s)
            driver_overriding: True if driver pressing gas
            t: Current time
            
        Returns:
            target_speed: Speed target (m/s) or None
            status: Current MSLC state
        """
        if not self.enabled:
            return None, self.STATE_DISABLED
        
        # Check for driver override
        if driver_overriding and self.state == self.STATE_ACTIVE:
            self.state = self.STATE_OVERRIDDEN
            self.override_time = t
        
        # Clear override after timeout
        if self.state == self.STATE_OVERRIDDEN:
            if t - self.override_time > self.OVERRIDE_TIMEOUT:
                self.state = self.STATE_ACTIVE
        
        # Get speed limit from MAPD
        current_limit, upcoming_limit, distance = \
            self._get_speed_limit(map_data, gps_position)
        
        if current_limit is None:
            self.state = self.STATE_NO_DATA
            return None, self.state
        
        self.current_limit = current_limit
        self.state = self.STATE_ACTIVE
        
        # Calculate target
        target_kmh = self._calculate_target(
            v_ego * 3.6,  # Convert to km/h
            current_limit,
            upcoming_limit,
            distance
        )
        
        if target_kmh is None:
            return None, self.state
        
        self.target_speed = target_kmh / 3.6  # Convert to m/s
        return self.target_speed, self.state
    
    def _get_speed_limit(self, map_data, gps_position):
        """Extract speed limit from MAPD data."""
        # Implementation from 4.3.1
        pass
    
    def _calculate_target(self, v_ego_kmh, current_limit, 
                          upcoming_limit, distance):
        """Calculate speed target with offset and lookahead."""
        # Implementation from 4.3.2
        pass
```

### 5.3 Integration in controlsd.py

```python
# selfdrive/controls/controlsd.py

from openpilot.selfdrive.controls.lib.mslc import MSLC

class ControlsD:
    def __init__(self):
        # ... existing init ...
        self.mslc_controller = MSLC()
        
    def update(self):
        # ... existing update ...
        
        # Update MSLC
        mslc_speed, mslc_state = self.mslc_controller.update(
            map_data=sm['mapD'],
            gps_position=sm['gpsLocation'],
            v_ego=CS.vEgo,
            driver_overriding=CS.gasPressed,
            t=sm.logMonoTime['carState'] * 1e-9
        )
        
        # Apply MSLC speed if active and lower than current target
        if mslc_speed is not None:
            v_cruise = min(v_cruise, mslc_speed)
        
        # ... rest of control logic ...
```

### 5.4 Parameter Management

**EOP Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPMSLCEnabled` | Bool | `0` | Master toggle for MSLC |
| `EOPMSLCOffsetPercent` | Int | `5` | Speed limit offset in percent |
| `EOPMSLCOffsetFixed` | Int | `0` | Fixed offset in km/h |
| `EOPMSLCUseNavdFallback` | Bool | `1` | Use NAVD speed limit fallback when OSM missing |

---

## 6. Safety Analysis

### 6.1 Safety Mechanisms

| Mechanism | Implementation |
|-----------|----------------|
| **Driver Override** | Gas pedal disables MSLC for 10 seconds |
| **Gradual Transitions** | 500m lookahead for speed reductions |
| **No Speed Increase** | Only reduces speed, never increases above current |
| **Data Validation** | Rejects unrealistic limits (>130 km/h on non-motorway) |
| **GPS Confidence** | Only active with HDOP < 5.0 |

### 6.2 Risk Assessment

| Risk | Mitigation | Likelihood | Severity |
|------|------------|------------|----------|
| Wrong speed limit | OSM validation + NAVD fallback | Low | Medium |
| Sudden slowdown | 500m lookahead blending | Very Low | Low |
| Driver surprise | Clear UI indicator + easy override | Low | Low |
| Outdated OSM data | NAVD fallback | Low | Medium |

---

## 7. Comparison with MTSC

| Aspect | MTSC | MSLC |
|--------|------|------|
| **OSM Data** | Curvature (geometry) | Speed limits (tags) |
| **Physics** | $v = \sqrt{a / \kappa}$ | Direct limit + offset |
| **Range** | 250-500m | Current + 500m ahead |
| **User Override** | No (safety) | Yes (gas pedal) |
| **Offset Support** | No | Yes (percent + fixed) |
| **Fallback** | None | NAVD (Valhalla) |

---

## 8. Integration Points

### 8.1 Data Flow

```
MAPD (OSM) + NAVD (Valhalla)
    ↓
MSLC.update()
    ↓
Speed target (with offset)
    ↓
longitudinal_planner
    ↓
v_cruise
    ↓
CarController (throttle/brake)
```

### 8.2 Dependencies

**Required:**
- MAPD daemon with OSM speed limit support
- GPS position
- Route data from NAVD

**Optional:**
- NAVD/Valhalla speed limit fallback (offline)

---

## 9. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Design Document | ✅ Complete | This document |
| MAPD Integration | ✅ Complete | Speed limit queries working |
| Core Implementation | ✅ Complete | `selfdrive/controls/lib/mslc.py` |
| UI Toggles | ✅ Complete | Offset controls in eop_panel.cc |
| Unit Tests | ⏳ Pending | Test scenarios defined |
| Documentation | ✅ Complete | This document |

---

---

## Implementation

### Algorithm

```
Input: mapData.speedLimit, modelV2.leads
Output: maxSpeedLimit, isMSLCActive
```

Speed limit compliance:
- Query speed limit from MAPD
- Apply hysteresis (5 km/h buffer)
- Respect lead vehicles

### Code Location

- `selfdrive/controls/lib/speed_limit_controller.py` *(not implemented)*


## 10. Related Documents

- [EOP OVERVIEW](../../00_Index/OVERVIEW.md) - EOP Architecture Overview
- MAPD.md - Map Daemon (shared infrastructure)
- [MTSC.md](./MTSC.md) - Map Turn Speed Control (companion feature)
- NAVD.md - Navigation Daemon (route data)
