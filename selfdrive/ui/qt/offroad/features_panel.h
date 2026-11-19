#pragma once

#include "selfdrive/ui/qt/offroad/settings.h"
#include "selfdrive/ui/qt/offroad/param_controls.h"
#include "selfdrive/ui/qt/offroad/mapd_areas.h"

class FeaturesPanel : public ListWidget {
  Q_OBJECT
public:
  explicit FeaturesPanel(SettingsWindow *parent);

public slots:
  void expandToggleDescription(const QString &param);

private:
  Params params;
  ParamWatcher *fs_watch;
  std::map<std::string, ParamControl*> toggles;

  void add_lateral_toggles();
  void add_longitudinal_toggles();
  void add_extended_controls();
  void add_map_area_controls();
  void updateMapStates();
  void updateStates();
  void showEvent(QShowEvent *event) override;
  void hideEvent(QHideEvent *event) override;

  ParamDoubleSpinBoxControl* lca_sec_toggle;

  QList<MapdArea> mapd_menu;
  MapdDownloader *mapd;
  ButtonControl *map_areas_btn;
  ButtonControl *map_download_btn;
};
