# MAPD Implementation Tracking Document

## Project Overview
Implementing MAPD (Map Data) integration for NagasPilot's M-TSC (Map Turn Speed Control) system to replace simulated curve detection with real OSM-based curve detection using proven code from sunnypilot and FrogPilot.

## Implementation Phases

### Phase 1: Binary Management (1-2 days) ✅ READY TO START
**Component**: `NPMapdBinaryManager`
**Files to Create/Modify**:
- `nagaspilot/selfdrive/mapd/np_mapd_binary_manager.py` (new)
- `nagaspilot/selfdrive/mapd/__init__.py` (new)

**Requirements**:
- Download pfeiferj/mapd binary (7.8MB Go executable)
- Auto-update functionality
- Binary validation and permissions
- Use FrogPilot's proven download URLs

**Success Criteria**:
- Binary downloads and validates correctly
- Auto-update mechanism works
- Proper error handling and fallback

### Phase 2: Auto Map Download System (3-4 days)
**Component**: `NPMapAutoUpdater`
**Files to Create/Modify**:
- `nagaspilot/selfdrive/mapd/np_map_auto_updater.py` (new)

**Requirements**:
- GPS-based region detection
- Smart bandwidth management (WiFi preferred)
- Automatic map freshness checking
- Background scheduling system

**Success Criteria**:
- Maps download automatically based on location
- Bandwidth usage optimized
- Map updates scheduled appropriately

### Phase 3: Home Screen Alert System (1-2 days)
**Component**: `NPMapAlertManager`  
**Files to Create/Modify**:
- `nagaspilot/selfdrive/mapd/np_map_alert_manager.py` (new)
- UI integration files (TBD based on existing alert system)

**Requirements**:
- Contextual alerts for map status
- Action buttons ("Download Now", "Schedule for WiFi", "Cancel")
- Real-time progress display

**Success Criteria**:
- Users see relevant map status alerts
- Download controls work correctly
- Progress indication is accurate

### Phase 4: M-TSC Data Interface (2-3 days)
**Component**: `NPMTSCDataExtractor`
**Files to Modify**:
- `nagaspilot/selfdrive/mapd/np_mtsc_data_extractor.py` (new)
- `nagaspilot/selfdrive/controls/lib/np_mtsc_controller.py` (existing - update to use real data)
- `nagaspilot/selfdrive/controls/lib/np_vtsc_controller.py` (existing - vision-based control)
- `nagaspilot/selfdrive/controls/lib/np_mtsc_controller.py` (existing - map-based control)

**Requirements**:
- Parse real OSM curve data from binary
- Extract curve radius from OSM way geometry
- Calculate curvature (1/radius) with physics accuracy
- Replace hash-based simulation with geometry-based detection

**Success Criteria**:
- 95% curve detection accuracy (vs current ~60%)
- <5% false positives (vs current ~15%)
- ±5m radius accuracy (vs current ±50m)
- <50MB memory usage, <10% CPU overhead

## Technical Architecture

### Data Flow
1. **GPS Location** → NPMapAutoUpdater → Download OSM data for region
2. **OSM Data** → pfeiferj/mapd binary → Processed curve data
3. **Curve Data** → NPMTSCDataExtractor → Structured data for M-TSC
4. **M-TSC Data** → np_mtsc_controller.py → Speed control decisions

### Communication Protocol
- Use params for binary communication (MTSCCurveRequest/MTSCCurveData)
- JSON format for structured data exchange
- Fallback to simulation when MAPD unavailable

### Performance Targets
- **Accuracy**: 95% curve detection (vs current 60%)
- **Memory**: <50MB usage
- **CPU**: <10% overhead
- **Precision**: ±5m radius accuracy (vs current ±50m)

## Implementation Status

### ✅ Completed
- [x] Phase 1: NPMapdManager (renamed from NPMapdBinaryManager)
- [x] Phase 2: NPMapdUpdater (renamed from NPMapAutoUpdater)  
- [x] Phase 3: NPMapdAlerts (renamed from NPMapAlertManager)
- [x] Phase 4: NPMapdExtractor (renamed from NPMTSCDataExtractor)
- [x] M-TSC Integration with real OSM data
- [x] FrogPilot/SunnyPilot naming convention alignment
- [x] ~~Ethernet connection alert for M-TSC~~ (removed - not required for functionality)
- [x] Syntax validation and basic testing

### 🔄 Current Status
**IMPLEMENTATION COMPLETE** - All phases finished with proper naming conventions

### ✅ Key Features Implemented
1. **Real OSM Data Integration**: Replaced hash-based simulation with geometry-based curve detection
2. **Auto-Download System**: GPS-based region detection with bandwidth management
3. **Smart Alerts**: Contextual alerts for map downloads and updates
4. **Binary Management**: Auto-update pfeiferj/mapd binary with validation
5. **Advanced Offline Operation**: M-TSC works with cached maps without requiring constant connectivity

### 🎯 500m M-TSC Focused Optimizations  
6. **Focused 500m Lookahead**: Optimized for M-TSC braking calculations (not long-range)
7. **High-Density Waypoints**: 50 waypoints per 500m (10m spacing) for precise curve geometry
8. **Efficient Caching**: 1km cache radius optimized for 500m focus with 200 segment limit
9. **Real-Time Updates**: 0.5s query intervals for responsive 500m ahead predictions
10. **M-TSC Curve Filtering**: Only tracks curves within 500m range for computational efficiency

## Key Dependencies
- **pfeiferj/mapd binary**: 7.8MB Go executable for OSM processing
- **FrogPilot patterns**: Binary management and download logic
- **SunnyPilot patterns**: Data processing and integration methods
- **Current M-TSC system**: Integration points in np_mtsc_controller.py

## Risk Mitigation
- **Fallback Strategy**: Keep existing simulation as backup
- **Incremental Testing**: Validate each phase before proceeding
- **Performance Monitoring**: Track memory/CPU usage throughout
- **User Experience**: Ensure seamless operation with proper alerts

## Timeline Estimate
**Total**: 7-11 days
- Phase 1: 1-2 days (Binary Management)
- Phase 2: 3-4 days (Auto Download System) 
- Phase 3: 1-2 days (Alert System)
- Phase 4: 2-3 days (M-TSC Integration)

**Target Completion**: End of current development cycle