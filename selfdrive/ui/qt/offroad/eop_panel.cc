#include "selfdrive/ui/qt/offroad/eop_panel.h"

#include <cstdlib>
#include <array>
#include <memory>

#include <QFile>
#include <QFrame>
#include <QLabel>
#include <QProcess>
#include <QDialog>
#include <QListWidget>
#include <QMap>
#include <QVBoxLayout>
#include <QHBoxLayout>

#include "selfdrive/ui/qt/widgets/controls.h"
#include "selfdrive/ui/qt/widgets/input.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"
#include "system/hardware/hw.h"

static QWidget *makeDivider(const QString &title) {
  auto *w = new QWidget();
  w->setFixedHeight(52);
  auto *layout = new QHBoxLayout(w);
  layout->setContentsMargins(40, 16, 40, 0);
  layout->setSpacing(16);

  auto makeLine = [&]() {
    auto *l = new QFrame();
    l->setFrameShape(QFrame::HLine);
    l->setFixedHeight(1);
    l->setStyleSheet("background-color: #3a3a3a;");
    return l;
  };

  auto *lbl = new QLabel(title.toUpper());
  lbl->setStyleSheet("font-size: 15px; font-weight: bold; color: #E4E4E4; letter-spacing: 3px;");
  lbl->setSizePolicy(QSizePolicy::Minimum, QSizePolicy::Preferred);

  layout->addWidget(makeLine(), 1);
  layout->addWidget(lbl, 0, Qt::AlignCenter);
  layout->addWidget(makeLine(), 1);
  return w;
}

void EopPanel::add_lateral_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
      {
          "",
          tr("Lateral Control"),
          "",
      },
      {
          "EOPLatALCC",
          tr("Always-on Lane Centering Control (ALCC)"),
          tr("Keeps lateral control active when cruise main is available."
             "\nCan optionally run without cruise and hold at a stop."),
      },
      {
          "EOPLatRoadEdgeDetection",
          tr("Road Edge Detection (RED)"),
          tr("Block lane change when road edge is detected."),
      },
      {
          "EOPALCCAllowAlways",
          tr("ALCC Without Cruise"),
          tr("Allow ALCC even when cruise main is off. Useful for platforms without LCC button."),
      },
      {
          "EOPALCCHoldAtStandstill",
          tr("ALCC Hold at Standstill"),
          tr("Keep lateral torque active while stopped if ALCC is engaged."),
      },
      {
          "EOPDLPCurvesEnabled",
          tr("Curve Assist (DLP)"),
          tr("Switch to laneless mode pre-emptively for tight curves."),
      },
      {
          "EOPLCAControllerEnabled",
          tr("Lane Change Assistant (LCA)"),
          tr("Multi-camera blind spot detection for safer lane changes."),
      },
  };
  auto lca_speed_toggle = new ParamSpinBoxControl(
      "EOPLatLCASpeed", tr("Lane Change Assist (LCA) Speed:"),
      tr("Off = Disable Lane Change Assist"), "", 0, 160, 5, tr(" km/h"),
      tr("Off"));
  auto alcc_brake_mode = new ButtonParamControl(
      "EOPALCCBrakeMode", QString::fromUtf8("　") + tr("ALCC Brake Behaviour"),
      tr("Choose how ALCC responds when the brake pedal is pressed."
         "\nMaintain - keep steering active."
         "\nPause - hold steering until the brake is released."
         "\nDisengage - fully release ALCC when braking."),
      "", {tr("Maintain"), tr("Pause"), tr("Disengage")});
  auto dlat_mode_control = new ButtonParamControl(
      "EOPDLATMode", QString::fromUtf8("　") + tr("Dynamic Lateral Profile"),
      tr("Select which lane planner to run:"
         "\nLaneful - always use lane lines."
         "\nLaneless - always use end-to-end laneless planner."
         "\nDynamic - switch automatically using DLat heuristics."),
      "", {tr("Laneful"), tr("Laneless"), tr("Dynamic")});

  // Auto Lane Change controls
  auto auto_lane_change_toggle = new ParamControl(
      "EOPAutoLaneChange", tr("Auto Lane Change"),
      tr("Enable automatic lane changes when turn signal is activated."), "",
      this);
  lane_change_delay_slider = new ParamDoubleSpinBoxControl(
      "EOPLaneChangeDelay", tr("Lane Change Delay:"),
      tr("Delay before executing lane change after turn signal activation."),
      "", 0.5, 5.0, 0.1, tr(" s"), tr(""));
  minimum_lane_width_slider = new ParamDoubleSpinBoxControl(
      "EOPMinimumLaneWidth", tr("Minimum Lane Width:"),
      tr("Minimum lane width required for lane change assist."), "", 2.0, 4.0,
      0.1, tr(" m"), tr(""));
  auto one_lane_change_toggle = new ParamControl(
      "EOPOneLaneChange", tr("One Lane Change Only"),
      tr("Limit to one lane change per turn signal activation for safety."), "",
      this);

  QWidget *label = nullptr;
  bool has_toggle = false;

  for (auto &[param, title, desc] : toggle_defs) {
    if (param.isEmpty()) {
      label = new LabelControl(title, "");
      addItem(label);
      addItem(lca_speed_toggle);
      addItem(alcc_brake_mode);
      addItem(dlat_mode_control);
      addItem(auto_lane_change_toggle);
      addItem(lane_change_delay_slider);
      addItem(minimum_lane_width_slider);
      addItem(one_lane_change_toggle);
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

  // If no toggles were added, hide the label
  if (!has_toggle && label) {
    label->hide();
  }
}

void EopPanel::add_enhanced_perception_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
      {
          "",
          QString::fromUtf8("🔍 ") + tr("Enhanced Perception"),
          "",
      },
      {
          "EOPStereoEnabled",
          tr("Stereo Depth Perception"),
          tr("Enable stereo vision for 3D object detection and depth "
             "estimation.\nEssential for accurate distance and height "
             "measurements.\nDisabled by default for upstream compatibility."),
      },
      {
          "EOPLeftCameraEnabled",
          tr("Forward Left Camera Helper"),
          tr("Optional forward-left helper camera for lane change "
             "suggestions.\nBlind-spot logic uses radar; camera assists only "
             "when available."),
      },
      {
          "EOPRightCameraEnabled",
          tr("Forward Right Camera Helper"),
          tr("Optional forward-right helper camera for lane change "
             "suggestions.\nBlind-spot logic uses radar; camera assists only "
             "when available."),
      },
      {
          "EOPSideCamerasSwapped",
          tr("Swap Left/Right Cameras"),
          tr("Enable if side cameras are physically installed swapped.\n"
             "This corrects the data output without rewiring."),
      },
      {
          "EOPGridEnabled",
          tr("Spatial Mapping (gridd)"),
          tr("Enable occupancy grid mapping for spatial awareness.\nCreates "
             "cost map for path planning and obstacle avoidance.\nUses stereo "
             "depth from v4l2d, SceneSeg on NPU Core 1, and optional PP-LiteSeg "
             "on AX650N PCIe.\nDisabled by default for upstream compatibility."),
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

void EopPanel::add_enhanced_controllers_section() {
  bool has_stereo = params.getBool("EOPStereoEnabled");

  addItem(new LabelControl(QString::fromUtf8("🔮 ") + tr("Enhanced Controllers (Stereo Required)"), ""));

  if (!has_stereo) {
    addItem(new LabelControl(tr("Stereo cameras not detected. Enhanced features unavailable."), ""));
  }

  // Stereo Nudge Toggle
  auto nudge_toggle = new ParamControl(
      "EOPNudgeEnabled",
      tr("Stereo Path Nudge"),
      tr("Enable stereo-based path enhancements:\n"
         "• LatNudge — lateral obstacle avoidance using stereo boundaries\n"
         "• LonNudge — speed reduction based on drivable distance and occupancy"),
      "", this);
  nudge_toggle->setEnabled(has_stereo);
  addItem(nudge_toggle);
  toggles["EOPNudgeEnabled"] = nudge_toggle;

  // AEB Toggle (with warning)
  auto aeb_toggle = new ParamControl(
      "EOPAEBEnabled",
      tr("Automatic Emergency Braking (AEB)"),
      tr("⚠️ SAFETY-CRITICAL: Requires extensive testing.\n"
         "RSS-based emergency braking for collision avoidance.\n"
         "Uses radar + vision + monod detections.\n"
         "Disabled by default - enable only after validation."),
      "", this);
  aeb_toggle->setEnabled(has_stereo);
  addItem(aeb_toggle);
  toggles["EOPAEBEnabled"] = aeb_toggle;
  
  // RCD Toggle
  auto rcd_toggle = new ParamControl(
      "EOPRCDEnabled",
      tr("Road Condition Detection (RCD)"),
      tr("Detects wet, icy, snowy, or debris-covered roads.\n"
         "Automatically reduces speed for hazardous conditions.\n"
         "Uses surface data + classical CV analysis."),
      "", this);
  rcd_toggle->setEnabled(has_stereo);
  addItem(rcd_toggle);
  toggles["EOPRCDEnabled"] = rcd_toggle;
}

void EopPanel::add_longitudinal_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
      {
          "",
          tr("Speed Control"),
          "",
      },
      {
          "EOPLonExtRadar",
          tr("Use External Radar"),
          tr("See https://github.com/eFiniLan/openpilot-ext-radar-addon for "
             "more information."),
      },

      {
          "EOPVTSCEnabled",
          tr("Vision Turn Speed Control (VTSC)"),
          tr("Slow down for upcoming curves (0-250m) using vision data."),
      },
      {
          "EOPMTSCEnabled",
          tr("Map Turn Speed Control (MTSC)"),
          tr("Slow down for upcoming curves (250-500m) using OSM data."),
      },
      {
          "EOPTLSCEnabled",
          tr("Traffic Light Speed Control (TLSC)"),
          tr("Slow down for yellow/red lights when no lead vehicle is ahead."),
      },
      {
          "EOPMSLCEnabled",
          tr("Map Speed Limit Control (MSLC)"),
          tr("Automatically adjust speed based on posted speed limits from OSM."),
      },
      {
          "EOPTJAEnabled",
          tr("Traffic Jam Assist (TJA)"),
          tr("Gentle acceleration ramp after stops in stop-and-go traffic."),
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
    if (param == "EOPLonExtRadar" && !vehicle_has_radar_unavailable) {
      continue;
    }
    has_toggle = true;
    auto toggle = new ParamControl(param, title, desc, "", this);
    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);
    addItem(toggle);
    toggles[param.toStdString()] = toggle;
  }

  // DLON Mode Selector
  auto dlon_mode_control = new ButtonParamControl(
      "EOPDLONMode", QString::fromUtf8("　") + tr("Longitudinal Profile"),
      tr("ACC - standard cruise. E2E - experimental. Dynamic - auto-switch."),
      "", {tr("ACC"), tr("E2E"), tr("Dynamic")});
  addItem(dlon_mode_control);

  tsc_lat_accel_toggle = new ParamDoubleSpinBoxControl(
      "EOPTSCTargetLatAccel",
      QString::fromUtf8("　") + tr("Curve Speed Limit:"),
      tr("Max lateral acceleration for curve speed control. Lower = more cautious."),
      "", 1.0, 2.5, 0.1, tr(" m/s²"), tr(""));
  addItem(tsc_lat_accel_toggle);

  // BLE navigation app toggle
  addItem(new ParamControl(
      "EOPNavBleEnabled",
      QString::fromUtf8("　") + tr("BLE Navigation App"),
      tr("Bluetooth connection for wireless destination input via mobile app."),
      "", this));

  // If no toggles were added, hide the label
  if (!has_toggle && label) {
    label->hide();
  }
}

void EopPanel::add_ui_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
      {
          "",
          tr("UI"),
          "",
      },
      {
          "EOPUIRadarTracks",
          tr("Display Radar Tracks"),
          "",
      },
      {
          "EOPUIRainbow",
          tr("Rainbow Driving Path"),
          tr("Why not?"),
      },
      {
          "EOPUIDisplayMode",
          tr("UI Display Mode"),
          tr("Advanced UI display mode with enhanced visualizations."),
      },
  };
  auto hide_hud = new ParamSpinBoxControl(
      "EOPUIHideHudSpeedKph", tr("Hide HUD When Moves above:"),
      tr("To prevent screen burn-in, hide Speed, MAX Speed, and Steering "
         "Icons when the car moves.\nOff = Stock Behavior"),
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
    if (param == "EOPUIRadarTracks" && !vehicle_has_long_ctrl) {
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

void EopPanel::add_car_adaptive_toggles() {
  addItem(new LabelControl(tr("Car Adaptive Tuning (CAT)"),
                           tr("Learns and corrects steer ratio and stiffness from real driving data.")));

  auto cat_manual_sr_toggle = new ParamControl(
      "EOPCATManualSREnabled",
      tr("Use Fixed Steer Ratio"),
      tr("Disable learning and apply a fixed steer ratio instead."),
      "", this);
  addItem(cat_manual_sr_toggle);
  toggles["EOPCATManualSREnabled"] = cat_manual_sr_toggle;
}

void EopPanel::add_recording_controls() {
  auto ensure_bool = [&](const std::string &key, bool val) {
    if (params.get(key).empty()) {
      params.putBool(key, val);
    }
  };

  ensure_bool("EOPRecordEnabled", true);

  addItem(new LabelControl(tr("Recording"),
                           tr("Enable or disable on-road recording")));

  recorders_toggle = new ParamControl(
      "EOPRecordEnabled", tr("Enable On-Road Recording"),
      tr("When enabled and storage is present, recordd runs "
         "automatically for loop recording, impact detection, and snapshots."),
      "", this);
  addItem(recorders_toggle);
}

void EopPanel::add_calibration_section() {
  addItem(new LabelControl(tr("Camera Calibration"),
                           tr("Factory and runtime camera calibration settings.")));

  // Factory Calibration Status
  bool factory_calibrated = params.getBool("EOPFactoryCalibrated");
  QString factory_status = factory_calibrated ? tr("✓ Calibrated") : tr("✗ Not Calibrated");
  auto factory_status_label = new LabelControl(
      tr("Factory Calibration Status"),
      factory_status);
  addItem(factory_status_label);

  // Runtime calibration reset button
  auto reset_runtime_btn = new ButtonControl(
      tr("Reset Calibration"),
      tr("RESET"),
      tr("Reset on-road calibration. Use after remounting camera."));
  QObject::connect(reset_runtime_btn, &ButtonControl::clicked, [=]() {
    if (uiState()->engaged()) {
      ConfirmationDialog::alert(tr("Disengage to reset calibration"), this);
      return;
    }
    if (ConfirmationDialog::confirm(tr("Reset runtime calibration? Factory intrinsics are preserved."), tr("Reset"), this)) {
      params.remove("CalibrationParams");
      params.remove("CameraCalibrationParams");
      params.putBool("OnroadCycleRequested", true);
    }
  });
  addItem(reset_runtime_btn);
}

void EopPanel::add_gps_rtk_toggles() {
  addItem(new LabelControl(tr("🛰 GPS / RTK Corrections"),
                           tr("Requires u-blox ZED-F9P-04B on UART7 "
                              "and an NTRIP-capable internet connection for RTK Fixed mode.")));

  auto rtk_toggle = new ParamControl(
      "EOPRTKEnabled",
      tr("Enable RTK GPS"),
      tr("Activate centimeter-level positioning via u-blox ZED-F9P-04B.\n"
         "Baud is auto-negotiated from 38400 (factory) to 115200 on boot."),
      "", this);
  addItem(rtk_toggle);
  toggles["EOPRTKEnabled"] = rtk_toggle;

  auto ntrip_toggle = new ParamControl(
      "EOPNTRIPEnabled",
      tr("Enable NTRIP Corrections"),
      tr("Receive RTCM3.3 differential corrections for RTK Fixed mode."),
      "", this);
  addItem(ntrip_toggle);
  toggles["EOPNTRIPEnabled"] = ntrip_toggle;
}

void EopPanel::add_vehicle_platform_section() {
  addItem(new LabelControl(tr("Vehicle Platform"),
                           tr("Select your vehicle size category for baseline physics tuning.")));

  // Vehicle type options (ordered by size)
  const std::vector<QString> vehicle_types = {
    tr("A-Hatch"),    // Micro hatchback (Seagull, etc.)
    tr("B-Hatch"),    // Small hatchback (Dolphin, MG4, etc.)
    tr("B-Sedan"),    // Small sedan
    tr("B-SUV"),      // Small SUV (Aion Y, Yuan Up, etc.)
    tr("C-Sedan"),    // Compact sedan (Qin Plus, Aion S, etc.)
    tr("C-SUV"),      // Compact SUV (Atto 3, Song Plus, etc.)
    tr("D-Sedan"),    // Mid-size sedan (Han, Model 3, P7, etc.)
    tr("D-SUV"),      // Mid-size SUV (Model Y, Tang, G6, etc.)
    tr("E-SUV"),      // Full-size SUV (L7/L8/L9, ES8, etc.)
    tr("MPV"),        // Minivan / people mover (D9, X9, Mega, etc.)
  };

  QString current_type = QString::fromStdString(params.get("EOPVehicleType"));
  if (current_type.isEmpty()) {
    current_type = "SUV_C";
    params.put("EOPVehicleType", current_type.toStdString());
  }

  // Map param value to display name
  auto param_to_display = [](const QString &param) -> QString {
    static const QMap<QString, QString> map = {
      {"HATCH_A", QObject::tr("A-Hatch")},
      {"HATCH_B", QObject::tr("B-Hatch")},
      {"SEDAN_B", QObject::tr("B-Sedan")},
      {"SUV_B", QObject::tr("B-SUV")},
      {"SEDAN_C", QObject::tr("C-Sedan")},
      {"SUV_C", QObject::tr("C-SUV")},
      {"SEDAN_D", QObject::tr("D-Sedan")},
      {"SUV_D", QObject::tr("D-SUV")},
      {"SUV_E", QObject::tr("E-SUV")},
      {"MPV", QObject::tr("MPV")},
    };
    return map.value(param, param);
  };

  auto display_to_param = [](const QString &display) -> QString {
    static const QMap<QString, QString> map = {
      {QObject::tr("A-Hatch"), "HATCH_A"},
      {QObject::tr("B-Hatch"), "HATCH_B"},
      {QObject::tr("B-Sedan"), "SEDAN_B"},
      {QObject::tr("B-SUV"), "SUV_B"},
      {QObject::tr("C-Sedan"), "SEDAN_C"},
      {QObject::tr("C-SUV"), "SUV_C"},
      {QObject::tr("D-Sedan"), "SEDAN_D"},
      {QObject::tr("D-SUV"), "SUV_D"},
      {QObject::tr("E-SUV"), "SUV_E"},
      {QObject::tr("MPV"), "MPV"},
    };
    return map.value(display, display);
  };

  auto vehicle_type_btn = new ButtonControl(
      tr("Vehicle Type"),
      param_to_display(current_type),
      tr("Vehicle size category for baseline physics. CAT will refine automatically."));

  QObject::connect(vehicle_type_btn, &ButtonControl::clicked, [=]() {
    QDialog dialog(this);
    dialog.setWindowTitle(tr("Select Vehicle Type"));
    dialog.setStyleSheet("QDialog { background-color: #1a1a1a; }");

    QVBoxLayout *layout = new QVBoxLayout(&dialog);
    QListWidget *list = new QListWidget(&dialog);
    list->setStyleSheet(R"(
      QListWidget {
        background-color: #2a2a2a;
        color: #E4E4E4;
        font-size: 24px;
        border-radius: 10px;
        padding: 10px;
      }
      QListWidget::item {
        padding: 15px;
        border-bottom: 1px solid #3a3a3a;
      }
      QListWidget::item:selected {
        background-color: #33Ab4C;
      }
    )");

    for (const QString &type : vehicle_types) {
      list->addItem(type);
    }

    // Select current item
    QString current_display = param_to_display(QString::fromStdString(params.get("EOPVehicleType")));
    for (int i = 0; i < list->count(); ++i) {
      if (list->item(i)->text() == current_display) {
        list->setCurrentRow(i);
        break;
      }
    }

    layout->addWidget(list);
    dialog.setLayout(layout);
    dialog.resize(450, 500);

    QObject::connect(list, &QListWidget::itemClicked, [&](QListWidgetItem *item) {
      QString selected = display_to_param(item->text());
      params.put("EOPVehicleType", selected.toStdString());
      vehicle_type_btn->setText(param_to_display(selected));
      dialog.accept();
    });

    dialog.exec();
  });

  addItem(vehicle_type_btn);
}

void EopPanel::add_device_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
      {
          "",
          tr("Device"),
          "",
      },
      {
          "EOPDeviceIsRhd",
          tr("Right-Hand Drive Mode"),
          tr("Follow right-hand traffic rules (right-seat driver)."),
      },
      {
          "EOPDeviceBeep",
          tr("Warning Beep"),
          "",
      }};
  std::vector<QString> audible_alert_mode_texts{tr("Std."), tr("Warning"),
                                                tr("Off")};
  ButtonParamControl *audible_alert_mode_setting = new ButtonParamControl(
      "EOPDeviceAudibleAlertMode", tr("Alert Sound"),
      tr("Std - all alerts. Warning - warnings only. Off - silent."),
      "", audible_alert_mode_texts);

  auto auto_shutdown_toggle = new ParamSpinBoxControl(
      "EOPDeviceAutoShutdownIn", tr("Auto Shutdown In:"),
      tr("0 mins = Immediately"), "", -5, 300, 5, tr(" mins"), tr("Off"));

  QWidget *label = nullptr;
  bool has_toggle = false;

  for (auto &[param, title, desc] : toggle_defs) {
    if (param.isEmpty()) {
      label = new LabelControl(title, "");
      addItem(label);
      addItem(auto_shutdown_toggle);
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
  addItem(audible_alert_mode_setting);
  has_toggle = true;

  // If no toggles were added, hide the label
  if (!has_toggle && label) {
    label->hide();
  }
}

EopPanel::EopPanel(SettingsWindow *parent) : ListWidget(parent) {
  is_metric = params.getBool("IsMetric");
  auto cp_bytes = params.get("CarParamsPersistent");
  if (!cp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    capnp::FlatArrayMessageReader cmsg(
        aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();
    vehicle_has_long_ctrl = hasLongitudinalControl(CP);
    vehicle_has_radar_unavailable = CP.getRadarUnavailable();
  }

  // Ensure always-on features: safety-critical and background daemons
  auto ensure_on = [&](const char *key) {
    if (params.get(key).empty()) params.putBool(key, true);
  };
  ensure_on("EOPMapdEnabled");    // auto-started by MTSC/MSLC/NAV
  ensure_on("EOPCATEnabled");     // always learns from driving
  ensure_on("EOPBSDEnabled");     // safety: blind spot detection always active
  ensure_on("EOPDriverDEnabled"); // safety: driver attention always monitored

  // ── DRIVING ─────────────────────────────────────────
  addItem(makeDivider(QString::fromUtf8("🚗  ") + tr("Driving")));
  add_lateral_toggles();
  add_longitudinal_toggles();
  add_safety_toggles();

  // ── ASSISTANCE ───────────────────────────────────────
  addItem(makeDivider(QString::fromUtf8("👁  ") + tr("Assistance")));
  add_driver_toggles();
  add_radar4d_toggles();

  // ── DISPLAY ──────────────────────────────────────────
  addItem(makeDivider(QString::fromUtf8("🖥  ") + tr("Display")));
  add_ui_toggles();

  // ── DEVICE ───────────────────────────────────────────
  addItem(makeDivider(QString::fromUtf8("⚙️  ") + tr("Device")));
  add_recording_controls();
  add_device_toggles();

  // ── SETUP ────────────────────────────────────────────
  addItem(makeDivider(QString::fromUtf8("🔧  ") + tr("Setup")));
  add_calibration_section();
  add_car_adaptive_toggles();

  fs_watch = new ParamWatcher(this);

  // Register all watched params once in the constructor
  for (const char *p : {
    "EOPLatLCASpeed", "EOPLonExtRadar", "EOPDLONMode", "EOPDLATMode",
    "EOPVTSCEnabled", "EOPMTSCEnabled", "EOPNavBleEnabled",
    "EOPLCAControllerEnabled",
    "EOPAutoLaneChange", "EOPLaneChangeDelay", "EOPMinimumLaneWidth", "EOPOneLaneChange",
    "EOPUIDisplayMode",
    "EOPMSLCEnabled",
    "EOPMultiCameraCalibEnabled", "EOPFactoryCalibrated",
    "EOPRearCameraEnabled",
  }) {
    fs_watch->addParam(p);
  }

  QObject::connect(fs_watch, &ParamWatcher::paramChanged,
                   [=](const QString &param_name, const QString &param_value) {
                     updateStates();
                   });

  connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    is_onroad = !offroad;
    updateStates();
  });

  updateStates();
}

void EopPanel::showEvent(QShowEvent *event) { updateStates(); }

void EopPanel::updateStates() {
  if (!isVisible()) return;

  if (vehicle_has_long_ctrl) {
  }

  tsc_lat_accel_toggle->setVisible(params.getBool("EOPVTSCEnabled") || params.getBool("EOPMTSCEnabled"));
}

void EopPanel::add_safety_toggles() {
  addItem(new LabelControl(
      tr("Safety"),
      tr("Blind spot detection and driver safety.")));

  // BSD chime — user audio preference (BSD itself is always on)
  auto bsd_chime_toggle = new ParamControl(
      "EOPBSDChimeEnabled",
      tr("BSD Warning Chime"),
      tr("Audible warning when a fast-approaching vehicle enters the blind spot."),
      "", this);
  addItem(bsd_chime_toggle);

  // BEV Widget toggle
  auto bev_toggle = new ParamControl(
      "EOPBEVWidgetEnabled",
      tr("Bird's Eye View (BEV)"),
      tr("Top-down view of vehicle and surrounding objects on the driving screen."),
      "", this);
  addItem(bev_toggle);
}

void EopPanel::add_voice_ai_toggles() {
  addItem(new LabelControl(
      QString::fromUtf8("🎤 ") + tr("Voice AI"),
      tr("Hands-free assistant. Requires microphone + Hailo-8 accelerator (not available on ExoPilot 01M).")));

  auto voice_toggle = new ParamControl(
      "EOPVoiceEnabled",
      tr("Enable Voice Assistant"),
      tr("Wake word detection and speech-to-text for hands-free control."),
      "", this);
  addItem(voice_toggle);
  toggles["EOPVoiceEnabled"] = voice_toggle;
}

void EopPanel::add_driver_toggles() {
  addItem(new LabelControl(
      tr("Driver Attention"),
      tr("Attention monitoring via steering torque and rear camera.")));

  // Rear camera toggle (display preference only — driver monitor always runs)
  auto rear_cam_toggle = new ParamControl(
      "EOPRearCameraEnabled",
      tr("Rear Camera"),
      tr("USB rear camera for reverse view. Shows when reverse gear engaged."),
      "", this);
  addItem(rear_cam_toggle);
  toggles["EOPRearCameraEnabled"] = rear_cam_toggle;
}

void EopPanel::add_radar4d_toggles() {
  addItem(new LabelControl(
      QString::fromUtf8("📡 ") + tr("4D Short-Range Radar"),
      tr("BGT60TR13C 60GHz FMCW radar, mounted on the stereo camera bar."
         "\nAdds Doppler velocity and short-range (0-15m) forward/blind-spot"
         " detection to the camera fusion pipeline. Requires the sensor to be"
         " physically installed.")));

  auto radar4d_toggle = new ParamControl(
      "EOPRadar4DEnabled",
      tr("Enable 4D Radar"),
      tr("Only enable if the BGT60TR13C radar is physically installed."
         "\nLeave off on hardware without this sensor."),
      "", this);
  addItem(radar4d_toggle);
  toggles["EOPRadar4DEnabled"] = radar4d_toggle;
}

void EopPanel::add_monod_toggles() {
  addItem(new LabelControl(
      QString::fromUtf8("📷 ") + tr("Hailo Long-Range Detection (MonoD)"),
      tr("Hailo-8 NPU-based long-range object detection using tele_road camera.")));

  // Master toggle
  auto monod_toggle = new ParamControl(
      "EOPMonoDEnabled",
      tr("Enable Long-Range Detection"),
      tr("Activate Hailo-8 inference for distant object detection."
         "\nExtends detection range to 500m using 16mm tele_road camera."),
      "", this);
  addItem(monod_toggle);
  toggles["EOPMonoDEnabled"] = monod_toggle;

  // Scene segmentation toggle
  auto sceneseg_toggle = new ParamControl(
      "EOPMonoDSceneSegEnabled",
      tr("Enable Scene Segmentation"),
      tr("Run PP-LiteSeg on tele_road feed for semantic understanding."),
      "", this);
  addItem(sceneseg_toggle);
  toggles["EOPMonoDSceneSegEnabled"] = sceneseg_toggle;

  // Camera toggles
  auto wide_toggle = new ParamControl(
      "EOPMonoDWideEnabled",
      tr("Enable 1.7mm Wide Camera"),
      tr("Use ultra-wide camera for close-range blind spot coverage."),
      "", this);
  addItem(wide_toggle);
  toggles["EOPMonoDWideEnabled"] = wide_toggle;

  auto tele_toggle = new ParamControl(
      "EOPMonoDTeleEnabled",
      tr("Enable 16mm TeleRoad Camera"),
      tr("Use tele_road camera for long-range detection (primary MonoD input)."),
      "", this);
  addItem(tele_toggle);
  toggles["EOPMonoDTeleEnabled"] = tele_toggle;

  // YOLO confidence threshold
  auto yolo_conf = new ParamDoubleSpinBoxControl(
      "EOPMonoDYoloConf",
      tr("YOLO Confidence Threshold:"),
      tr("Minimum confidence for object detection. Lower = more detections but more false positives."),
      "", 0.1, 0.9, 0.05, "", "", 0.5);
  addItem(yolo_conf);

  // Max tracks
  auto max_tracks = new ParamSpinBoxControl(
      "EOPMonoDMaxTracks",
      tr("Max Tracked Objects:"),
      tr("Maximum number of objects to track simultaneously."),
      "", 16, 128, 8, "", "", 64);
  addItem(max_tracks);
}

void EopPanel::add_pointcloud_toggles() {
  addItem(new LabelControl(
      QString::fromUtf8("☁️ ") + tr("3D Point Cloud Recording"),
      tr("Record 3D point clouds for fleet digital twin. Non-critical for driving.")));

  // Master toggle
  auto pc_toggle = new ParamControl(
      "EOPPointcloudEnabled",
      tr("Enable Point Cloud Recording"),
      tr("Save 3D reconstructions from stereo depth to SD card."
         "\nUsed for fleet mapping and digital twin generation."
         "\nDoes not affect core ADAS functionality."),
      "", this);
  addItem(pc_toggle);
  toggles["EOPPointcloudEnabled"] = pc_toggle;

  // Recording rate
  auto pc_rate = new ParamSpinBoxControl(
      "EOPPointcloudRateHz",
      tr("Recording Rate (Hz):"),
      tr("Frame rate for point cloud capture. Higher = more data but more storage."),
      "", 1, 20, 1, " Hz", "", 5);
  addItem(pc_rate);

  // Max storage
  auto pc_storage = new ParamDoubleSpinBoxControl(
      "EOPPointcloudMaxGB",
      tr("Max Storage (GB):"),
      tr("Maximum storage for point clouds. Oldest data auto-deleted when exceeded."),
      "", 1.0, 32.0, 0.5, " GB", "", 4.0);
  addItem(pc_storage);

  // GPU acceleration toggle
  auto pc_gpu = new ParamControl(
      "EOPPointcloudUseGPU",
      tr("Use GPU Acceleration"),
      tr("Use Mali GPU for 3D reprojection. Faster but uses GPU resources."
         "\nFalls back to CPU if GPU unavailable."),
      "", this);
  addItem(pc_gpu);
  toggles["EOPPointcloudUseGPU"] = pc_gpu;
}

void EopPanel::add_globald_toggles() {
  addItem(new LabelControl(
      QString::fromUtf8("🌍 ") + tr("Global Localization"),
      tr("Map-based position refinement using OSM and SGM point cloud matching.")));

  // Master toggle
  auto globald_toggle = new ParamControl(
      "EOPGlobaldEnabled",
      tr("Enable Global Localization"),
      tr("Fuse GPS with OSM road data and SGM point cloud matching."
         "\nProvides accurate lane-level positioning without RTK."),
      "", this);
  addItem(globald_toggle);
  toggles["EOPGlobaldEnabled"] = globald_toggle;

  // SGM Localizer toggle
  auto sgm_localizer_toggle = new ParamControl(
      "EOPSGMLocalizerEnabled",
      tr("Enable SGM Point Cloud Matching"),
      tr("Match live stereo point clouds against SGM 3D map tiles."
         "\nRequires pre-built SGM map tiles in /data/maps/sgm/"),
      "", this);
  addItem(sgm_localizer_toggle);
  toggles["EOPSGMLocalizerEnabled"] = sgm_localizer_toggle;

  // SGM Mode
  auto sgm_mode = new ButtonParamControl(
      "EOPSGMMode",
      tr("SGM Matching Mode"),
      tr("Select localization mode:"
         "\nLive - Match against live point clouds only"
         "\nMap - Match against pre-built SGM tiles only"
         "\nFused - Combine both sources for best accuracy"),
      "", {tr("Live"), tr("Map"), tr("Fused")});
  addItem(sgm_mode);

  // Confidence threshold
  auto sgm_conf = new ParamDoubleSpinBoxControl(
      "EOPSGMConfidenceThreshold",
      tr("Match Confidence Threshold:"),
      tr("Minimum confidence for SGM position match. Higher = more reliable but fewer matches."),
      "", 0.3, 0.95, 0.05, "", "", 0.7);
  addItem(sgm_conf);

  // Max range
  auto sgm_range = new ParamDoubleSpinBoxControl(
      "EOPSGMMaxRange",
      tr("Max Matching Range (m):"),
      tr("Maximum search radius for point cloud matching."),
      "", 20.0, 200.0, 10.0, " m", "", 100.0);
  addItem(sgm_range);

  // Auto tile download toggle (for navigation)
  auto auto_tile_toggle = new ParamControl(
      "EOPAutoTileEnabled",
      tr("Auto-Download Map Tiles"),
      tr("Automatically download OSM and SGM tiles based on GPS location."
         "\nRequires internet connection."),
      "", this);
  addItem(auto_tile_toggle);
  toggles["EOPAutoTileEnabled"] = auto_tile_toggle;

  auto wifi_only_toggle = new ParamControl(
      "EOPAutoTileWifiOnly",
      tr("WiFi-Only Downloads"),
      tr("Only auto-download tiles when connected to WiFi to save cellular data."),
      "", this);
  addItem(wifi_only_toggle);
  toggles["EOPAutoTileWifiOnly"] = wifi_only_toggle;
}

void EopPanel::add_surface_toggles() {
  addItem(new LabelControl(
      QString::fromUtf8("🛣️ ") + tr("Surface Quality Mapping"),
      tr("Road surface condition detection and longitudinal control tuning.")));

  // Grid resolution
  auto grid_res = new ParamDoubleSpinBoxControl(
      "EOPSurfaceGridResolution",
      tr("Grid Resolution (m):"),
      tr("Size of each grid cell for surface quality mapping."),
      "", 0.1, 1.0, 0.05, " m", "", 0.25);
  addItem(grid_res);

  // Grid range
  auto grid_range = new ParamDoubleSpinBoxControl(
      "EOPSurfaceGridRange",
      tr("Grid Forward Range (m):"),
      tr("How far ahead to map surface conditions."),
      "", 30.0, 150.0, 10.0, " m", "", 60.0);
  addItem(grid_range);

  // Grid width
  auto grid_width = new ParamDoubleSpinBoxControl(
      "EOPSurfaceGridWidth",
      tr("Grid Width (m):"),
      tr("Lateral coverage of surface quality grid."),
      "", 10.0, 60.0, 5.0, " m", "", 30.0);
  addItem(grid_width);

  // Long horizon toggle
  auto long_horizon = new ParamControl(
      "EOPSurfaceLongHorizon",
      tr("Enable Long Horizon"),
      tr("Extend surface quality detection to 100m for highway comfort."
         "\nUses more GPU resources."),
      "", this);
  addItem(long_horizon);
  toggles["EOPSurfaceLongHorizon"] = long_horizon;
}

void EopPanel::expandToggleDescription(const QString &param) {
  toggles[param.toStdString()]->showDescription();
}
