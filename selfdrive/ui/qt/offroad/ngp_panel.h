#pragma once

#include "selfdrive/ui/qt/offroad/settings.h"

class NGPPanel : public ListWidget {
  Q_OBJECT
public:
  explicit NGPPanel(SettingsWindow *parent);

public slots:
  void expandToggleDescription(const QString &param);

private:
  Params params;
  std::map<std::string, ParamControl*> toggles;

  void add_longitudinal_toggles();
};
