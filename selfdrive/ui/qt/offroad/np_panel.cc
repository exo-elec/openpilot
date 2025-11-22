#include "selfdrive/ui/qt/offroad/np_panel.h"
#include <QJsonDocument>
#include <QJsonObject>



void NPPanel::add_lateral_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
    {
      "",
      QString::fromUtf8("🐍 ") + tr("Lateral Ctrl"),
      "",
    },
    {
      "np_dlp_enable",
      tr("Dynamic Lane Profile (DLP)"),
      tr("Master toggle for NagasPilot's laneful/laneless lane control stack."),
    },
    {
      "np_cat_enable",
      tr("Car Adaptive Tuning (CAT)"),
      tr("Smoothly adapts steer ratio and stiffness using live parameters to handle shared fingerprints."),
    },
    {
      "np_cat_manual_sr_enable",
      tr("CAT Manual Steer Ratio"),
      tr("Override adaptive steer ratio with a custom value (0.5–1.5x stock)."),
    },
    {
      "np_alcc_enable",
      tr("Always Lane Centering Control (ALCC)"),
      tr("Keep lateral control engaged even when openpilot is not actively controlling longitudinal."),
    },
    {
      "np_red_enable",
      tr("Road Edge Detection (RED)"),
      tr("Block lane change assist when the system detects the road edge.\nNOTE: This will show 'Car Detected in Blindspot' warning.")
    },
    {
      "np_tsc_enable",
      tr("Turn Speed Controller (TSC)"),
      tr("Use NagasPilot's map/vision-based turn speed controller when available.")
    },
    {
      "np_tsc_use_map",
      tr("TSC Map Data"),
      tr("Allow TSC to slow for turns based on stored curvature maps.")
    },
    {
      "np_tsc_use_vision",
      tr("TSC Vision Data"),
      tr("Allow TSC to slow for turns using live perception signals.")
    },
  };
  auto lca_speed_toggle = new ParamSpinBoxControl("np_lca_min_speed", tr("Lane Change Assist (LCA) Speed:"),
    tr("Off = Disable Lane Change Assist"),
    "", 0, 160, 5, tr(" mph"), tr("Off"));
  lca_sec_toggle = new ParamDoubleSpinBoxControl("np_lca_auto_delay", QString::fromUtf8("　") + tr("Auto Lane Change Assist (LCA) after:"), tr("Off = Disable Auto Lane Change Assist."), "", 0, 5.0, 0.5, tr(" sec"), tr("Off"));

  QWidget *label = nullptr;
  bool has_toggle = false;
  const double sr_min = base_sr > 0 ? base_sr * 0.5 : 5.0;
  const double sr_max = base_sr > 0 ? base_sr * 1.5 : 25.0;
  cat_manual_sr_spin = new ParamDoubleSpinBoxControl("np_cat_manual_sr", tr("Manual Steer Ratio Value"),
    tr("Used when CAT Manual Steer Ratio is enabled. Clamped to 0.5–1.5x stock."),
    "", sr_min, sr_max, 0.1, "", tr("CP default"));
  auto lat_friction_spin = nullptr;
  auto lat_latacc_spin = nullptr;

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

    if (param == "np_cat_manual_sr_enable") {
      addItem(cat_manual_sr_spin);
    }
  }

  cat_status_label = new LabelControl(tr("CAT Status"), tr("Waiting for data"));
  addItem(cat_status_label);

  // If no toggles were added, hide the label
  if (!has_toggle && label) {
    label->hide();
  }
}

void NPPanel::add_longitudinal_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
    {
      "",
      QString::fromUtf8("🐍 ") + tr("Longitudinal Ctrl"),
      "",
    },
    {
      "np_ext_radar_enable",
      tr("Use External Radar"),
      tr("See https://github.com/eFiniLan/openpilot-ext-radar-addon for more information."),
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
    if (param == "np_ext_radar_enable" && !vehicle_has_radar_unavailable) {
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

void NPPanel::add_ui_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
    {
      "",
      QString::fromUtf8("🐍 ") + tr("UI"),
      "",
    },
    {
      "np_ui_radar_tracks",
      tr("Display Radar Tracks"),
      "",
    },
    {
      "np_ui_rainbow_path",
      tr("Rainbow Driving Path"),
      tr("Why not?"),
    },
    {
      "np_cat_debug_onroad",
      tr("Show CAT Debug Onroad"),
      tr("Display CAT confidence/tuned values on the onroad HUD."),
    },
    {
      "np_stack_debug_onroad",
      tr("Show NP Stack Debug Onroad"),
      tr("Display DLP/TSC/DEM status on the onroad HUD."),
    },
  };
  auto hide_hud = new ParamSpinBoxControl("np_ui_hud_hide_speed", tr("Hide HUD When Moves above:"),
    tr("To prevent screen burn-in, hide Speed, MAX Speed, and Steering/DM Icons when the car moves.\nOff = Stock Behavior"),
    "", 0, 120, 5, tr(" km/h"), tr("Off"));

  QWidget *label = nullptr;
  bool has_toggle = false;

  for (auto &[param, title, desc] : toggle_defs) {
    if (param.isEmpty()) {
      label = new LabelControl(title, "");
      addItem(label);
      addItem(hide_hud);
      has_toggle = true;
      continue;
    }
    if (param == "np_ui_radar_tracks" && !vehicle_has_long_ctrl) {
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

void NPPanel::add_device_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
    {
      "",
      QString::fromUtf8("🐍 ") + tr("Device"),
      "",
    },
    {
      "np_device_rhd",
      tr("Enable Right-Hand Drive Mode"),
      tr("Allow openpilot to obey right-hand traffic conventions on right driver seat."),
    },
    {
      "np_device_beep",
      tr("Enable Beep (Warning)"),
      "",
    }
  };
  std::vector<QString> audible_alert_mode_texts{tr("Std."), tr("Warning"), tr("Off")};
  ButtonParamControl* audible_alert_mode_setting = new ButtonParamControl("np_device_alert_mode", tr("Audible Alert Mode"),
                                          tr("Warning - Only emits sound when there is a warning.\nOff - Does not emit any sound at all."),
                                          "",
                                          audible_alert_mode_texts);

  auto auto_shutdown_toggle = new ParamSpinBoxControl("np_device_auto_shutdown", tr("Auto Shutdown In:"), tr("0 mins = Immediately"), "", -5, 300, 5, tr(" mins"), tr("Off"));


  QWidget *label = nullptr;
  bool has_toggle = false;

  const bool lite = getenv("LITE");
  for (auto &[param, title, desc] : toggle_defs) {
    if (param.isEmpty()) {
      label = new LabelControl(title, "");
      addItem(label);
      addItem(auto_shutdown_toggle);
      has_toggle = true;
      continue;
    }
    if ((param == "np_device_rhd" || param == "np_device_monitoring_disable" || param == "np_device_beep") && !lite) {
      continue;
    }

    has_toggle = true;
    auto toggle = new ParamControl(param, title, desc, "", this);
    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);
    addItem(toggle);
    toggles[param.toStdString()] = toggle;
  }
  if (!getenv("DISABLE_DRIVER")) { // lite check
    addItem(audible_alert_mode_setting);
    has_toggle = true;
  }

  // If no toggles were added, hide the label
  if (!has_toggle && label) {
    label->hide();
  }
}

NPPanel::NPPanel(SettingsWindow *parent) : ListWidget(parent) {
  is_metric = params.getBool("IsMetric");
  auto cp_bytes = params.get("CarParamsPersistent");
  if (!cp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();
    brand = QString::fromStdString(CP.getBrand());
    base_sr = CP.getSteerRatio();
    vehicle_has_long_ctrl = hasLongitudinalControl(CP);
    vehicle_has_radar_unavailable = CP.getRadarUnavailable();
  }

  add_lateral_toggles();
  add_longitudinal_toggles();
  add_ui_toggles();
  add_device_toggles();


  fs_watch = new ParamWatcher(this);
  QObject::connect(fs_watch, &ParamWatcher::paramChanged, [=](const QString &param_name, const QString &param_value) {
    updateStates();
  });

  connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    is_onroad = !offroad;
    updateStates();
  });

  updateStates();
}

void NPPanel::showEvent(QShowEvent *event) {
  updateStates();
}

void NPPanel::updateStates() {
  // do fs_watch here
  fs_watch->addParam("np_lca_min_speed");
  fs_watch->addParam("np_ext_radar_enable");
  fs_watch->addParam("np_cat_status");
  fs_watch->addParam("np_cat_manual_sr_enable");
  fs_watch->addParam("np_cat_manual_sr");
  fs_watch->addParam("np_cat_debug_onroad");
  fs_watch->addParam("np_stack_debug_onroad");


  if (!isVisible()) {
    return;
  }

  // do state change logic here
  lca_sec_toggle->setVisible(std::atoi(params.get("np_lca_min_speed").c_str()) > 0);

  // Update CAT status label if changed
  QString cat_json = QString::fromStdString(params.get("np_cat_status"));
  if (cat_status_label && !cat_json.isEmpty() && cat_json != cat_status_text) {
    cat_status_text = cat_json;
    QJsonParseError err;
    QJsonDocument doc = QJsonDocument::fromJson(cat_json.toUtf8(), &err);
    if (err.error == QJsonParseError::NoError && doc.isObject()) {
      QJsonObject obj = doc.object();
      const bool enabled = obj.value("enabled").toBool(false);
      const bool adaptive = obj.value("adaptive").toBool(false);
      const double conf = obj.value("confidence").toDouble(0.0);
      const double sr = obj.value("steerRatio").toDouble(0.0);
      const double stiff = obj.value("stiffnessFactor").toDouble(0.0);
      const int samples = obj.value("samples").toInt(0);
      const QString note = obj.value("note").toString("");
      const QString text = QString("%1 | conf: %2 | sr: %3 | stiff: %4 | samples: %5 | %6")
                              .arg(enabled ? (adaptive ? tr("Adaptive") : tr("Learning")) : tr("Disabled"))
                              .arg(conf, 0, 'f', 2)
                              .arg(sr, 0, 'f', 3)
                              .arg(stiff, 0, 'f', 3)
                              .arg(samples)
                              .arg(note);
      cat_status_label->setText(text);
    }
  }

  if (cat_manual_sr_spin) {
    const bool manual_on = params.getBool("np_cat_manual_sr_enable");
    cat_manual_sr_spin->setVisible(manual_on);
  }

}

void NPPanel::expandToggleDescription(const QString &param) {
  toggles[param.toStdString()]->showDescription();
}
