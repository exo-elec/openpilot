#include "selfdrive/ui/qt/offroad/ngp_panel.h"

void NGPPanel::add_longitudinal_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
    {
      "",
      tr("Longitudinal Ctrl"),
      "",
    },
    {
      "ngp_lon_brsc",
      tr("Bumpy Road Speed Controller (BRSC)"),
      tr("Reduce speed and acceleration on rough pavement, detected from "
         "vertical IMU acceleration. Recovers gradually a few seconds "
         "after the road smooths out."),
    },
  };

  QWidget *label = nullptr;
  bool has_toggle = false;

  for (auto &[param, title, desc] : toggle_defs) {
    if (param.isEmpty()) {
      label = new LabelControl(title, "");
      addItem(label);
      continue;
    }

    has_toggle = true;
    auto toggle = new ParamControl(param, title, desc, "", this);
    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);
    addItem(toggle);
    toggles[param.toStdString()] = toggle;
  }

  // If no toggles were added, hide the label
  if (!has_toggle && label) {
    label->hide();
  }
}

NGPPanel::NGPPanel(SettingsWindow *parent) : ListWidget(parent) {
  add_longitudinal_toggles();
}

void NGPPanel::expandToggleDescription(const QString &param) {
  toggles[param.toStdString()]->showDescription();
}
