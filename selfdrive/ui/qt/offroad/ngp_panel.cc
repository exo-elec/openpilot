#include "selfdrive/ui/qt/offroad/ngp_panel.h"

void NGPPanel::add_lateral_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
    {
      "",
      tr("Lateral Ctrl"),
      "",
    },
    {
      "ngp_lat_alcc",
      tr("Always-on Lane Centering Control (ALCC)"),
      "",
    },
    {
      "ngp_lat_road_edge_detection",
      tr("Road Edge Detection (RED)"),
      tr("Block lane change assist when the system detects the road edge.\nNOTE: This will show 'Car Detected in Blindspot' warning.")
    },
  };
  // Default values below match the registered defaults in common/params_keys.h
  // (ngp_lat_lca_speed=20, ngp_lat_lca_auto_sec=0.0) -- Params::get() has no
  // registered-default fallback of its own, unlike the Python side's
  // return_default=True (see modeld.py), so a fresh device with no param file
  // yet written would otherwise show 0/Off here while the daemon actually runs at 20.
  auto lca_speed_toggle = new ParamSpinBoxControl("ngp_lat_lca_speed", tr("LCA Speed:"), tr("Off = Disable LCA\n1 mph ≈ 1.2 km/h"), "", 0, 100, 5, tr(" mph"), tr("Off"), 20);
  lca_sec_toggle = new ParamDoubleSpinBoxControl("ngp_lat_lca_auto_sec", QString::fromUtf8("　") + tr("Auto Lane Change after:"), tr("Off = Disable Auto Lane Change."), "", 0, 5.0, 0.5, tr(" sec"), tr("Off"), 0.0);

  QWidget *label = nullptr;
  bool has_toggle = false;

  for (auto &[param, title, desc] : toggle_defs) {
    if (param.isEmpty()) {
      label = new LabelControl(title, "");
      addItem(label);
      addItem(lca_speed_toggle);
      addItem(lca_sec_toggle);
      has_toggle = true;
      continue;
    }

    has_toggle = true;
    auto toggle = new ParamControl(param, title, desc, "", this);
    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);
    addItem(toggle);
    toggles[param.toStdString()] = toggle;
  }

  // DLAT (Dynamic Lateral Profile) is a default, always-on behavior of this
  // branch -- automatic Laneful/Laneless confidence arbitration, not a
  // user-selectable mode. No panel control by design.

  // If no toggles were added, hide the label
  if (!has_toggle && label) {
    label->hide();
  }
}

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

  // DLON (Dynamic Longitudinal Profile) is a default, always-on behavior of
  // this branch -- automatic ACC/E2E switching, not a user-selectable mode.
  // No master enable toggle and no panel control by design: users cannot
  // force pure E2E (Experimental) or pure ACC directly.

  // If no toggles were added, hide the label
  if (!has_toggle && label) {
    label->hide();
  }
}

NGPPanel::NGPPanel(SettingsWindow *parent) : ListWidget(parent) {
  add_lateral_toggles();
  add_longitudinal_toggles();

  fs_watch = new ParamWatcher(this);
  QObject::connect(fs_watch, &ParamWatcher::paramChanged, [=](const QString &param_name, const QString &param_value) {
    updateStates();
  });

  connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    updateStates();
  });

  updateStates();
}

void NGPPanel::showEvent(QShowEvent *event) {
  updateStates();
}

void NGPPanel::updateStates() {
  // do fs_watch here
  fs_watch->addParam("ngp_lat_lca_speed");

  if (!isVisible()) {
    return;
  }

  // do state change logic here
  lca_sec_toggle->setVisible(std::atoi(params.get("ngp_lat_lca_speed").c_str()) > 0);
}

void NGPPanel::expandToggleDescription(const QString &param) {
  toggles[param.toStdString()]->showDescription();
}
