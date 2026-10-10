#include "selfdrive/ui/qt/offroad/ngp_panel.h"
#include <QComboBox>
#include <QJsonArray>
#include <QJsonDocument>

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
      tr("Requires live authorization from the configured Panda. Vehicle safety limits remain active."),
    },
    {
      "ngp_lat_road_edge_detection",
      tr("Road Edge Detection (RED)"),
      tr("Block lane change assist when the system detects the road edge.\nNOTE: This will show 'Car Detected in Blindspot' warning.")
    },
    {
      "ngp_lat_dlp_curves",
      tr("Curve Assist (DLP)"),
      tr("Adjust lane-confidence policy for tight curves. This does not select a different steering trajectory."),
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
    {
      "ngp_lon_vtsc",
      tr("Vision Turn Speed Control (VTSC)"),
      tr("Slow down for upcoming curves (0-250m) using vision data."),
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

void NGPPanel::add_extended_controls() {
  addItem(new LabelControl(tr("Additional Features"), ""));
  const std::vector<std::tuple<QString, QString, QString>> definitions{
    {"ngp_lat_soc", tr("SOC · Smart Offset"), tr("Optional bounded offset away from adjacent threats.")},
    {"ngp_lat_edge_guard", tr("RED · Edge Guard"), tr("Optional curvature guard near a road edge.")},
    {"ngp_lat_cat", tr("CAT · Adaptive Tuning"), tr("Use filtered live vehicle parameters.")},
    {"ngp_lat_lca_gap_eval", tr("LCA · Gap Check"), tr("Check the adjacent lane before lane changes.")},
    {"ngp_lat_lca_lane_width", tr("LCA · Lane Width Check"), tr("Check that the adjacent lane has sufficient width.")},
    {"ngp_lon_adaptive_gap", tr("AFG · Adaptive Following Gap"), tr("Adjust following comfort from lead motion.")},
    {"ngp_lon_lc_lead_handoff", tr("LCH · Lane Change Lead Handoff"), tr("Consider camera leads in the target lane.")},
    {"ngp_lon_green_light", tr("GLN · Green Light Notice"), tr("Notify when a planner stop is released.")},
    {"ngp_lon_lead_departure", tr("LDN · Lead Departure Notice"), tr("Notify when the lead vehicle moves away.")},
    {"ngp_map_enabled", tr("OSM · Map Data"), tr("Fetch map data for speed and curve policies.")},
    {"EOPMTSCEnabled", tr("MTSC · Map Turn Speed"), tr("EOP-origin policy for upcoming map curves.")},
    {"EOPMSLCEnabled", tr("MSLC · Map Speed Limit"), tr("EOP-origin posted speed-limit policy.")},
    {"EOPTLSCEnabled", tr("TLSC · Traffic Light Speed"), tr("Requires a valid traffic-light perception source.")},
    {"EOPDDSCEnabled", tr("DDSC · Distraction Speed"), tr("Requires valid driver-awareness data.")},
    {"EOPRCDEnabled", tr("RCD · Road Condition Speed"), tr("Requires a valid road-condition source.")},
    {"ngp_monod_enabled", tr("MONO · Camera Detection"), tr("Requires a provisioned detector model.")},
    {"ngp_pathd_enabled", tr("PATH · Object Protection Planner"), tr("Publish bounded protection proposals. Consumer switches are separate.")},
    {"ngp_lat_pathd", tr("PATH · Lateral Proposals"), tr("Requires the protection planner.")},
    {"ngp_lon_pathd", tr("PATH · Speed Proposals"), tr("Requires the protection planner.")},
    {"EOPPathdNudgesEnabled", tr("NUDGE · Camera Proposals"), tr("Run EOP-origin nudge policies on camera-only inputs.")},
    {"ngp_lon_cutin", tr("CUT · Cut-in Speed"), tr("Requires valid tracked objects.")},
    {"EOPEgpuDrivingEnabled", tr("CHES · Chestnut Driving Model"), tr("Requires detected Chestnut hardware and rebuilt big-model artifacts. Falls back to the small model if loading or warmup fails.")},
    {"ngp_dashboard_enabled", tr("WEB · Status Viewer"), tr("Read-only device and trip viewer at localhost:9091. Remote viewing uses an SSH tunnel.")},
    {"ngp_tripd_enabled", tr("TRIP · Trip Statistics"), tr("Record non-controlling trip statistics.")},
  };
  for (const auto &[key, title, description] : definitions) {
    auto control = new ParamControl(key, title, description, "", this);
    control->setEnabled(!params.getBool((key + "Lock").toStdString()));
    addItem(control);
    toggles[key.toStdString()] = control;
  }
  addItem(new ParamSpinBoxControl("ngp_lat_blinker_pause_mph", tr("BP · Blinker Pause Below"), "", "", 0, 50, 5, tr(" mph"), tr("Off")));
  addItem(new ParamSpinBoxControl("ngp_lat_turn_desire_mph", tr("TURN · Turn Hint Below"), "", "", 0, 50, 5, tr(" mph"), tr("Off")));
  addItem(new ParamSpinBoxControl("ngp_lon_speed_offset_kph", tr("SPO · Cruise Speed Offset"), "", "", -20, 20, 1, tr(" km/h")));
  auto add_choice = [this](const QString &key, const QString &title, const QStringList &options) {
    auto widget = new QWidget(this);
    auto layout = new QHBoxLayout(widget);
    layout->addWidget(new QLabel(title, widget));
    auto combo = new QComboBox(widget);
    combo->addItems(options);
    const auto value = QString::fromStdString(params.get(key.toStdString()));
    combo->setCurrentIndex(std::max(0, combo->findText(value)));
    layout->addWidget(combo);
    addItem(widget);
    connect(combo, qOverload<int>(&QComboBox::activated), this, [=](int index) {
      params.put(key.toStdString(), options[index].toStdString());
    });
  };
  add_choice("ngp_lon_drive_mode", tr("DRV · Drive Mode"), {"custom", "eco", "normal", "sport"});
  add_choice("ngp_lon_accel_profile", tr("ACC · Acceleration Profile"), {"normal", "eco", "sport"});
  add_choice("ngp_dm_policy", tr("SAM · Steering Activity Decay"), {"strict", "relaxed", "tight"});
  addItem(new ParamSpinBoxControl("ngp_dpp_max_mode", tr("DPP · Maximum Planner Mode"), tr("0 idle, 1 shadow, 2 supervise, 3 longitudinal, 4 lateral, 5 both. Requires valid planner inputs."), "", 0, 5, 1));
  addItem(new LabelControl(tr("Device and Display"), ""));
  addItem(new ParamSpinBoxControl("ngp_device_shutdown_minutes", tr("ASD · Offroad Shutdown"), tr("Stock power policy at -1. Configured timeouts retain a five-minute grace and power safeguards."), "", -1, 300, 5, tr(" min"), tr("Stock"), -1));
  addItem(new ParamSpinBoxControl("ngp_device_logger_delay_seconds", tr("RDL · Recording Start Delay"), tr("Delay recorded logs and video at drive start. Diagnostics and control processes keep running."), "", 0, 300, 5, tr(" s"), tr("Off")));
  addItem(new ParamSpinBoxControl("ngp_ui_hide_hud_speed_kph", tr("HUD · Hide Above Speed"), tr("Camera and alerts remain visible."), "", 0, 120, 5, tr(" km/h"), tr("Off")));
  addItem(new ParamSpinBoxControl("ngp_ui_brightness", tr("BRI · Display Brightness"), "", "", 0, 100, 5, tr(" %"), tr("Automatic")));
  addItem(new ButtonParamControl("ngp_device_audible_mode", tr("SND · Engagement Chimes"), tr("Safety warnings remain audible."), "", {tr("Standard"), tr("Quiet")}));
  auto selector = new QComboBox(this);
  selector->addItem(tr("Automatic vehicle detection"), "");
  const auto models = QJsonDocument::fromJson(QByteArray::fromStdString(params.get("ngp_device_vehicle_list"))).array();
  for (const auto &model : models) selector->addItem(model.toString(), model.toString());
  selector->setCurrentIndex(std::max(0, selector->findData(QString::fromStdString(params.get("ngp_device_vehicle_selected")))));
  auto vehicle = new QWidget(this);
  auto row = new QHBoxLayout(vehicle);
  row->addWidget(new QLabel(tr("VEH · Vehicle Selection"), vehicle));
  row->addWidget(selector);
  addItem(vehicle);
  connect(selector, qOverload<int>(&QComboBox::activated), this, [=](int index) {
    params.put("ngp_device_vehicle_selected", selector->itemData(index).toString().toStdString());
    params.remove("CarParamsCache");
  });
}

NGPPanel::NGPPanel(SettingsWindow *parent) : ListWidget(parent) {
  add_lateral_toggles();
  add_longitudinal_toggles();
  add_extended_controls();

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
