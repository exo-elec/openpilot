# Design Document: UI (User Interface)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |

---


> **Full Name:** EnhancedOpenPilot User Interface
> **Platform:** Qt 5 / C++ with OpenGL acceleration
> **Target:** RK3588 Mali-G610 GPU @ 60 FPS
> **Last Updated:** 2026-04-08

---

## 1. Objective

The EOP UI provides a high-performance, interactive dashboard optimized for the RK3588's Mali-G610 GPU. It ensures smooth 60 FPS rendering of complex 3D vision overlays, navigation maps, and real-time ADAS status while maintaining isolation from safety-critical control loops.

---

## 2. Technical Architecture

### 2.1 Performance Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Framework** | Qt 5.15 / C++ | UI components and event handling |
| **Rendering** | OpenGL ES 3.2 | Hardware-accelerated graphics |
| **Video** | DMA-BUF + V4L2 | Zero-copy camera display |
| **Fonts** | Noto Sans CJK | Multi-language support |

### 2.2 Safety Isolation

```
UI Process (selfdrive/ui/)
    +-- Qt Event Loop (60 Hz)
    +-- OpenGL Renderer
    +-- Parameter Reads (non-blocking)
            | IPC (cereal/zmq)
Control Process (selfdrive/controls/)
    +-- controlsd (100 Hz)
    +-- Safety-critical logic
```

**Key Principle:** UI hangs cannot affect vehicle control.

---

## 3. EOP Panel Design

### 3.1 EOPPanel Structure

```cpp
// selfdrive/ui/qt/offroad/eop_panel.h

class EopPanel : public ListWidget {
  Q_OBJECT

public:
  explicit EopPanel(SettingsWindow *parent);

private:
  void add_lateral_toggles();             // ALCC, DLAT, LCA, SOC, RED
  void add_longitudinal_toggles();        // TJA, DLON, VTSC, MTSC, MSLC
  void add_enhanced_perception_toggles(); // Stereo, Cameras, Grid
  void add_enhanced_controllers_section(); // ELAT, ELON, AEB
  void add_ui_toggles();                  // Display preferences
  void add_device_toggles();              // Device settings
  void add_recording_controls();          // DVR recording

  void updateStates();

  // Dynamic controls
  ParamDoubleSpinBoxControl* tsc_lat_accel_toggle;
  ParamDoubleSpinBoxControl* lane_change_delay_slider;
  ParamDoubleSpinBoxControl* minimum_lane_width_slider;
};
```

### 3.2 Section: Lateral Control

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `LatALCC` | Toggle | Always-on Lane Centering Control (ALCC) |
| `ALCCAllowAlways` | Toggle | ALCC without cruise main |
| `ALCCHoldAtStandstill` | Toggle | Keep lateral torque at stop |
| `ALCCBrakeMode` | ButtonSelect | Maintain/Pause/Disengage |
| `EOPDLATMode` | ButtonSelect | Laneful/Auto/Laneless |
| `DLPCurvesEnabled` | Toggle | Pre-switch to laneless in curves |
| `LCAControllerEnabled` | Toggle | Lane Change Assistant |
| `LCAGapEvalEnabled` | Toggle | Gap evaluation for LCA |
| `LCABSMEnabled` | Toggle | Use vehicle BSM |
| `SOCControllerEnabled` | Toggle | Smart Offset Control |
| `RedControllerEnabled` | Toggle | Road Edge Detection |
| `AutoLaneChange` | Toggle | Auto lane change on signal |
| `OneLaneChange` | Toggle | Single lane change limit |
| `LatLCASpeed` | SpinBox | LCA minimum speed (km/h) |
| `LaneChangeDelay` | DoubleSpinBox | Delay before lane change (s) |
| `MinimumLaneWidth` | DoubleSpinBox | Min lane width (m) |

### 3.3 Section: Longitudinal Control

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `LonExtRadar` | Toggle | External radar addon |
| `EOPDLONEnabled` | Toggle | Dynamic Longitudinal Profile |
| `EOPDLONMode` | ButtonSelect | Chill/Auto/Experimental |
| `VTSCEnabled` | Toggle | Vision Turn Speed Control |
| `MTSCEnabled` | Toggle | Map Turn Speed Control |
| `TSCTargetLatAccel` | DoubleSpinBox | Target lateral accel (m/s2) |
| `MSLCEnabled` | Toggle | Map Speed Limit Control |
| `MSLCOffsetPercent` | SpinBox | Offset above limit (%) |
| `MSLCOffsetFixed` | SpinBox | Fixed offset (km/h) |
| `MapdEnabled` | Toggle | Map Daemon (OSM) |
| `NavEnabled` | Toggle | Navigation Daemon |
| `EOPNavSource` | ButtonSelect | *(Deprecated — NAVD uses Valhalla only, no source selector needed)* |
| `TLSCEnabled` | Toggle | Traffic Light Speed Control |
| `TJAEnabled` | Toggle | Traffic Jam Assist |
| `TJAMaxHoldMinutes` | SpinBox | TJA hold timer (min) |

### 3.4 Section: Enhanced Perception

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `StereoEnabled` | Toggle | Stereo depth perception |
| `LeftCameraEnabled` | Toggle | Forward left camera |
| `RightCameraEnabled` | Toggle | Forward right camera |
| `SideCamerasSwapped` | Toggle | Swap camera outputs |
| `GridEnabled` | Toggle | Occupancy grid mapping |

### 3.5 Section: Enhanced Controllers (Stereo Required)

| Parameter | UI Element | Description | Requires |
|-----------|------------|-------------|----------|
| `AEBEnabled` | Toggle | Experimental bounded collision mitigation (confirmed forward 77 GHz radar only) | Forward radar |

**Note:** These toggles are disabled when `StereoEnabled` is false.

### 3.6 Section: UI Preferences

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `UIRadarTracks` | Toggle | Display radar tracks |
| `UIRainbow` | Toggle | Rainbow driving path |
| `UIDisplayMode` | Toggle | Advanced display mode |
| `UIHideHudSpeedKph` | SpinBox | Hide HUD above speed |
| `UIBrightness` | SpinBox | Screen brightness |

### 3.7 Section: Device Settings

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `DeviceIsRhd` | Toggle | Right-hand drive mode |
| `DeviceBeep` | Toggle | Enable warning beep |
| `DeviceAudibleAlertMode` | ButtonSelect | Std/Warning/Off |
| `DeviceAutoShutdownIn` | SpinBox | Auto shutdown timer |

### 3.8 Section: Recording

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `RecordEnabled` | Toggle | On-road recording |

---

## 4. Visual States

### 4.1 Engagement Indicators

| State | Color | Description |
|-------|-------|-------------|
| **Disengaged** | Gray | System off |
| **ALCC Only** | Blue | Lateral active only |
| **Fully Engaged** | Green | Both lateral + longitudinal |
| **Warning** | Yellow | Attention required |
| **Critical** | Red | Immediate takeover |

### 4.2 Feature Status Icons

| Icon | Feature | Trigger |
|------|---------|---------|
| Curve arrow | VTSC | Approaching curve |
| Map curve | MTSC | Map curve ahead |
| Lane arrows | LCA | Lane change active |
| Speed sign | MSLC | Speed limit active |
| Shield | ELAT | Obstacle offset |
| Road waves | ELON | Surface detected |
| Brake amber | AEB | Standby |
| Brake red | AEB | Emergency braking |
| Edge line | RED | Road edge warning |

---

## 5. On-Road Overlay

### 5.1 Vision Overlays

| Element | Source | Description |
|---------|--------|-------------|
| **Lane Lines** | modelV2 | 4 lane lines (2 per side) |
| **Predicted Path** | modelV2 | E2E path (rainbow option) |
| **Enhanced Path** | pathd | ELAT/ELON adjusted path |
| **Lead Indicator** | modelV2/radar | Distance color indicator |
| **Occupancy Grid** | gridd | BEV heat map overlay |
| **Speed Limit** | MSLC | OSM speed limit icon |
| **Curve Warning** | VTSC/MTSC | Upcoming curve indicator |
| **Road Edge** | RED | Edge boundary warning |

### 5.2 Alert System

| Alert | Level | Message |
|-------|-------|---------|
| MSLC_SPEED_CHANGE | Info | "Speed limit 60 ahead" |
| VTSC_CURVE_AHEAD | Info | "Curve ahead" |
| LCA_BLINDSPOT | Warning | "Vehicle in blindspot" |
| SOC_OFFSET_ACTIVE | Warning | "Offsetting for truck" |
| RED_APPROACHING | Warning | "Approaching road edge" |
| AEB_BRAKING | Critical | "Emergency braking!" |
| RED_BOUNDARY | Critical | "Road edge detected" |

---

## 6. File Structure

```
selfdrive/ui/qt/offroad/
+-- eop_panel.cc          # Main panel implementation
+-- eop_panel.h           # Header with class definition

selfdrive/ui/qt/onroad/
+-- eop_overlay.cc        # On-road feature indicators
+-- eop_alerts.cc         # Alert display system
```

---

## 7. Complete Parameter Reference

### 7.1 All UI Parameters by Section

| Section | Parameter | Type | Default | UI Element |
|---------|-----------|------|---------|------------|
| **Lateral** | LatALCC | Bool | 0 | Toggle |
| | ALCCAllowAlways | Bool | 0 | Toggle |
| | ALCCHoldAtStandstill | Bool | 0 | Toggle |
| | ALCCBrakeMode | String | "Maintain" | ButtonSelect |
| | EOPDLATMode | String | "Auto" | ButtonSelect |
| | DLPCurvesEnabled | Bool | 0 | Toggle |
| | LCAControllerEnabled | Bool | 0 | Toggle |
| | LCAGapEvalEnabled | Bool | 0 | Toggle |
| | LCABSMEnabled | Bool | 0 | Toggle |
| | SOCControllerEnabled | Bool | 0 | Toggle |
| | RedControllerEnabled | Bool | 1 | Toggle |
| | AutoLaneChange | Bool | 0 | Toggle |
| | OneLaneChange | Bool | 0 | Toggle |
| | LatLCASpeed | Int | 0 | SpinBox |
| | LaneChangeDelay | Float | 1.0 | DoubleSpinBox |
| | MinimumLaneWidth | Float | 3.0 | DoubleSpinBox |
| **Longitudinal** | LonExtRadar | Bool | 0 | Toggle |
| | EOPDLONEnabled | Bool | 0 | Toggle |
| | EOPDLONMode | String | "Chill" | ButtonSelect |
| | VTSCEnabled | Bool | 0 | Toggle |
| | MTSCEnabled | Bool | 0 | Toggle |
| | TSCTargetLatAccel | Float | 1.8 | DoubleSpinBox |
| | MSLCEnabled | Bool | 0 | Toggle |
| | MSLCOffsetPercent | Int | 0 | SpinBox |
| | MSLCOffsetFixed | Int | 0 | SpinBox |
| | MapdEnabled | Bool | 0 | Toggle |
| | NavEnabled | Bool | 0 | Toggle |
| | EOPNavSource | String | "None" | *(Deprecated — Valhalla only)* |
| | TLSCEnabled | Bool | 0 | Toggle |
| | TJAEnabled | Bool | 0 | Toggle |
| | TJAMaxHoldMinutes | Int | 10 | SpinBox |
| **Enhanced Perception** | StereoEnabled | Bool | 0 | Toggle |
| | LeftCameraEnabled | Bool | 0 | Toggle |
| | RightCameraEnabled | Bool | 0 | Toggle |
| | SideCamerasSwapped | Bool | 0 | Toggle |
| | GridEnabled | Bool | 0 | Toggle |
| | AEBEnabled | Bool | 0 | Toggle |
| **UI** | UIRadarTracks | Bool | 0 | Toggle |
| | UIRainbow | Bool | 0 | Toggle |
| | UIDisplayMode | Bool | 0 | Toggle |
| | UIHideHudSpeedKph | Int | 0 | SpinBox |
| | UIBrightness | Int | 0 | SpinBox |
| **Device** | DeviceIsRhd | Bool | 0 | Toggle |
| | DeviceBeep | Bool | 0 | Toggle |
| | DeviceAudibleAlertMode | String | "Std." | ButtonSelect |
| | DeviceAutoShutdownIn | Int | -1 | SpinBox |
| **Recording** | RecordEnabled | Bool | 1 | Toggle |

---

## 8. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| Qt5 / GPU Accel | ✅ Done | Mali-G610 optimized |
| EOPPanel Framework | ✅ Done | Based on dp_panel |
| Lateral Controls | ✅ Done | All toggles + sliders |
| Longitudinal Controls | ✅ Done | All toggles + sliders |
| Enhanced Perception | ✅ Done | Camera/stereo toggles |
| Enhanced Controllers | ✅ Done | ELAT/ELON/AEB toggles added |
| UI Preferences | ✅ Done | Display + brightness |
| Device Settings | ✅ Done | RHD, alerts, shutdown |
| Recording Controls | ✅ Done | On-road toggle |
| RED Toggle | ✅ Done | Road Edge Detection |
| LCA Toggles | ✅ Done | Gap eval + BSM |
| MSLC Offsets | ✅ Done | Percent + fixed controls |
| Nav Source | ✅ Done | *(Deprecated — Valhalla only)* |
| Visual States | ⏳ Partial | Basic colors done |
| On-Road Overlays | ⏳ Pending | Enhanced path display |
| Alert System | ⏳ Pending | EOP-specific alerts |

---

## 9. Related Documents

- [EOP OVERVIEW](../00_Index/OVERVIEW.md) - System architecture
- [NAMING_CONVENTIONS](../01_Core/NAMING_CONVENTIONS.md) - Parameter naming
- RED.md - Road Edge Detection design
- [IMPLEMENTATION_STATUS](../00_Index/IMPLEMENTATION_STATUS.md) - Overall status
