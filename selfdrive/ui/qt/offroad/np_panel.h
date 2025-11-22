#pragma once

#include "selfdrive/ui/qt/offroad/settings.h"

class NPPanel : public ListWidget {
  Q_OBJECT
public:
  explicit NPPanel(SettingsWindow *parent);

public slots:
  void expandToggleDescription(const QString &param);

private:
  Params params;
  ParamWatcher *fs_watch;
  std::map<std::string, ParamControl*> toggles;
  QString brand;
  double base_sr = 0.0;
  bool is_metric;
  bool is_onroad = false;
  bool vehicle_has_long_ctrl;
  bool vehicle_has_radar_unavailable;

  void add_lateral_toggles();
  void add_longitudinal_toggles();
  void add_ui_toggles();
  void add_device_toggles();
  void updateStates();
  void showEvent(QShowEvent *event) override;

  ParamDoubleSpinBoxControl* lca_sec_toggle;
  LabelControl* cat_status_label = nullptr;
  QString cat_status_text;
  ParamDoubleSpinBoxControl* cat_manual_sr_spin = nullptr;
};
