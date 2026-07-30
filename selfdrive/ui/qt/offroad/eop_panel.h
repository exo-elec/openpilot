#pragma once

#include "selfdrive/ui/qt/offroad/settings.h"

class LabelControl;

class EopPanel : public ListWidget {
  Q_OBJECT
public:
  explicit EopPanel(SettingsWindow *parent);

public slots:
  void expandToggleDescription(const QString &param);

private:
  Params params;
  ParamWatcher *fs_watch;
  std::map<std::string, ParamControl*> toggles;
  bool is_metric;
  bool is_onroad = false;
  bool vehicle_has_long_ctrl;
  bool vehicle_has_radar_unavailable;

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
  void add_radar4d_toggles();       // BGT60TR13C 4D short-range radar
  void add_monod_toggles();         // Hailo-8 long-range detection
  void add_pointcloud_toggles();    // 3D point cloud recording
  void add_globald_toggles();       // Global localization (OSM + SGM)
  void add_surface_toggles();       // Surface/Gridd configuration
  void updateStates();
  void showEvent(QShowEvent *event) override;

  ParamDoubleSpinBoxControl* tsc_lat_accel_toggle;
  ParamDoubleSpinBoxControl* lane_change_delay_slider;
  ParamDoubleSpinBoxControl* minimum_lane_width_slider;
  ParamControl *recorders_toggle = nullptr;
};