#pragma once

#include "selfdrive/ui/qt/offroad/ngp_controls.h"
#include "selfdrive/ui/qt/offroad/settings.h"

class LabelControl;

// One section header style for every settings page: a centred caps title
// between two hairlines.
QWidget *settingsSection(const QString &title);

// The ExoPilot settings, split by what the driver is doing (step 4.4b).
// Each page is its own instance; DRIVING and DEVICE_* are embedded in the
// Driving / Device pages, the others are pages of their own.
class EopPanel : public ListWidget {
  Q_OBJECT
public:
  enum Page { DRIVING, SAFETY, DISPLAY, DEVICE_VEHICLE, DEVICE_SYSTEM, ADVANCED };
  EopPanel(SettingsWindow *parent, Page page);

public slots:
  void expandToggleDescription(const QString &param);

private:
  Params params;
  ParamWatcher *fs_watch;
  std::map<std::string, ParamControl*> toggles;
  bool is_metric;
  bool is_onroad = false;
  bool vehicle_has_long_ctrl = false;
  bool vehicle_has_radar_unavailable = false;

  void add_lateral_toggles();
  void add_longitudinal_toggles();
  void add_safety_toggles();
  void add_enhanced_perception_toggles();
  void add_enhanced_controllers_section();
  void add_calibration_section();
  void add_car_adaptive_toggles();
  void add_gps_rtk_toggles();
  void add_recording_controls();
  void add_ui_toggles();
  void add_vehicle_platform_section();  // Vehicle size category selector
  void add_device_toggles();
  void add_voice_ai_toggles();      // Voice AI (waked, voiced, intentd)
  void add_driver_toggles();        // Driver pose detection toggles
  void add_monod_toggles();         // Hailo-8 long-range detection
  void add_pointcloud_toggles();    // 3D point cloud recording
  void add_globald_toggles();       // Global localization (OSM + SGM)
  void add_surface_toggles();       // Surface/Gridd configuration
  void add_display_page();
  void add_param_toggle(const char *param, const QString &title, const QString &desc);
  void updateStates();
  void showEvent(QShowEvent *event) override;

  ParamDoubleSpinBoxControl* tsc_lat_accel_toggle = nullptr;
  ParamDoubleSpinBoxControl* lane_change_delay_slider = nullptr;
  ParamDoubleSpinBoxControl* minimum_lane_width_slider = nullptr;
  ParamControl *recorders_toggle = nullptr;
};
