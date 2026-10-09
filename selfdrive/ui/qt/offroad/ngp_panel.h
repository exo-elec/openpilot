#pragma once

#include "selfdrive/ui/qt/offroad/settings.h"
#include "selfdrive/ui/qt/offroad/ngp_controls.h"

class NGPPanel : public ListWidget {
  Q_OBJECT
public:
  explicit NGPPanel(SettingsWindow *parent);

public slots:
  void expandToggleDescription(const QString &param);

private:
  Params params;
  ParamWatcher *fs_watch;
  std::map<std::string, ParamControl*> toggles;

  void add_lateral_toggles();
  void add_longitudinal_toggles();
  void add_extended_controls();
  void updateStates();
  void showEvent(QShowEvent *event) override;

  ParamDoubleSpinBoxControl* lca_sec_toggle;
};
