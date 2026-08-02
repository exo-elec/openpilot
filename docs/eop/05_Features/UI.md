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
| `EOPLatALCC` | Toggle | Always-on Lane Centering Control (ALCC) |
| `EOPALCCAllowAlways` | Toggle | ALCC without cruise main |
| `EOPALCCHoldAtStandstill` | Toggle | Keep lateral torque at stop |
| `EOPALCCBrakeMode` | ButtonSelect | Maintain/Pause/Disengage |
| `EOPDLATMode` | ButtonSelect | Laneful/Auto/Laneless |
| `EOPDLPCurvesEnabled` | Toggle | Pre-switch to laneless in curves |
| `EOPLCAControllerEnabled` | Toggle | Lane Change Assistant |
| `EOPLCAGapEvalEnabled` | Toggle | Gap evaluation for LCA |
| `EOPLCABSMEnabled` | Toggle | Use vehicle BSM |
| `EOPSOCControllerEnabled` | Toggle | Smart Offset Control |
| `EOPRedControllerEnabled` | Toggle | Road Edge Detection |
| `EOPAutoLaneChange` | Toggle | Auto lane change on signal |
| `EOPOneLaneChange` | Toggle | Single lane change limit |
| `EOPLatLCASpeed` | SpinBox | LCA minimum speed (km/h) |
| `EOPLaneChangeDelay` | DoubleSpinBox | Delay before lane change (s) |
| `EOPMinimumLaneWidth` | DoubleSpinBox | Min lane width (m) |

### 3.3 Section: Longitudinal Control

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `EOPLonExtRadar` | Toggle | External radar addon |
| `EOPDLONEnabled` | Toggle | Dynamic Longitudinal Profile |
| `EOPDLONMode` | ButtonSelect | Chill/Auto/Experimental |
| `EOPVTSCEnabled` | Toggle | Vision Turn Speed Control |
| `EOPMTSCEnabled` | Toggle | Map Turn Speed Control |
| `EOPTSCTargetLatAccel` | DoubleSpinBox | Target lateral accel (m/s2) |
| `EOPMSLCEnabled` | Toggle | Map Speed Limit Control |
| `EOPMSLCOffsetPercent` | SpinBox | Offset above limit (%) |
| `EOPMSLCOffsetFixed` | SpinBox | Fixed offset (km/h) |
| `EOPMapdEnabled` | Toggle | Map Daemon (OSM) |
| `EOPNavEnabled` | Toggle | Navigation Daemon |
| `EOPNavSource` | ButtonSelect | *(Deprecated — NAVD uses Valhalla only, no source selector needed)* |
| `EOPTLSCEnabled` | Toggle | Traffic Light Speed Control |
| `EOPTJAEnabled` | Toggle | Traffic Jam Assist |
| `EOPTJAMaxHoldMinutes` | SpinBox | TJA hold timer (min) |

### 3.4 Section: Enhanced Perception

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `EOPStereoEnabled` | Toggle | Stereo depth perception |
| `EOPLeftCameraEnabled` | Toggle | Forward left camera |
| `EOPRightCameraEnabled` | Toggle | Forward right camera |
| `EOPSideCamerasSwapped` | Toggle | Swap camera outputs |
| `EOPGridEnabled` | Toggle | Occupancy grid mapping |

### 3.5 Section: Enhanced Controllers (Stereo Required)

| Parameter | UI Element | Description | Requires |
|-----------|------------|-------------|----------|
| `EOPAEBEnabled` | Toggle | Emergency Braking | Stereo |

**Note:** These toggles are disabled when `EOPStereoEnabled` is false.

### 3.6 Section: UI Preferences

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `EOPUIRadarTracks` | Toggle | Display radar tracks |
| `EOPUIRainbow` | Toggle | Rainbow driving path |
| `EOPUIDisplayMode` | Toggle | Advanced display mode |
| `EOPUIHideHudSpeedKph` | SpinBox | Hide HUD above speed |
| `EOPUIBrightness` | SpinBox | Screen brightness |

### 3.7 Section: Device Settings

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `EOPDeviceIsRhd` | Toggle | Right-hand drive mode |
| `EOPDeviceBeep` | Toggle | Enable warning beep |
| `EOPDeviceAudibleAlertMode` | ButtonSelect | Std/Warning/Off |
| `EOPDeviceAutoShutdownIn` | SpinBox | Auto shutdown timer |

### 3.8 Section: Recording

| Parameter | UI Element | Description |
|-----------|------------|-------------|
| `EOPRecordEnabled` | Toggle | On-road recording |

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
| **Lateral** | EOPLatALCC | Bool | 0 | Toggle |
| | EOPALCCAllowAlways | Bool | 0 | Toggle |
| | EOPALCCHoldAtStandstill | Bool | 0 | Toggle |
| | EOPALCCBrakeMode | String | "Maintain" | ButtonSelect |
| | EOPDLATMode | String | "Auto" | ButtonSelect |
| | EOPDLPCurvesEnabled | Bool | 0 | Toggle |
| | EOPLCAControllerEnabled | Bool | 0 | Toggle |
| | EOPLCAGapEvalEnabled | Bool | 0 | Toggle |
| | EOPLCABSMEnabled | Bool | 0 | Toggle |
| | EOPSOCControllerEnabled | Bool | 0 | Toggle |
| | EOPRedControllerEnabled | Bool | 1 | Toggle |
| | EOPAutoLaneChange | Bool | 0 | Toggle |
| | EOPOneLaneChange | Bool | 0 | Toggle |
| | EOPLatLCASpeed | Int | 0 | SpinBox |
| | EOPLaneChangeDelay | Float | 1.0 | DoubleSpinBox |
| | EOPMinimumLaneWidth | Float | 3.0 | DoubleSpinBox |
| **Longitudinal** | EOPLonExtRadar | Bool | 0 | Toggle |
| | EOPDLONEnabled | Bool | 0 | Toggle |
| | EOPDLONMode | String | "Chill" | ButtonSelect |
| | EOPVTSCEnabled | Bool | 0 | Toggle |
| | EOPMTSCEnabled | Bool | 0 | Toggle |
| | EOPTSCTargetLatAccel | Float | 1.8 | DoubleSpinBox |
| | EOPMSLCEnabled | Bool | 0 | Toggle |
| | EOPMSLCOffsetPercent | Int | 0 | SpinBox |
| | EOPMSLCOffsetFixed | Int | 0 | SpinBox |
| | EOPMapdEnabled | Bool | 0 | Toggle |
| | EOPNavEnabled | Bool | 0 | Toggle |
| | EOPNavSource | String | "None" | *(Deprecated — Valhalla only)* |
| | EOPTLSCEnabled | Bool | 0 | Toggle |
| | EOPTJAEnabled | Bool | 0 | Toggle |
| | EOPTJAMaxHoldMinutes | Int | 10 | SpinBox |
| **Enhanced Perception** | EOPStereoEnabled | Bool | 0 | Toggle |
| | EOPLeftCameraEnabled | Bool | 0 | Toggle |
| | EOPRightCameraEnabled | Bool | 0 | Toggle |
| | EOPSideCamerasSwapped | Bool | 0 | Toggle |
| | EOPGridEnabled | Bool | 0 | Toggle |
| | EOPAEBEnabled | Bool | 0 | Toggle |
| **UI** | EOPUIRadarTracks | Bool | 0 | Toggle |
| | EOPUIRainbow | Bool | 0 | Toggle |
| | EOPUIDisplayMode | Bool | 0 | Toggle |
| | EOPUIHideHudSpeedKph | Int | 0 | SpinBox |
| | EOPUIBrightness | Int | 0 | SpinBox |
| **Device** | EOPDeviceIsRhd | Bool | 0 | Toggle |
| | EOPDeviceBeep | Bool | 0 | Toggle |
| | EOPDeviceAudibleAlertMode | String | "Std." | ButtonSelect |
| | EOPDeviceAutoShutdownIn | Int | -1 | SpinBox |
| **Recording** | EOPRecordEnabled | Bool | 1 | Toggle |

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
