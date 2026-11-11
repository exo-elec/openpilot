#include "selfdrive/ui/qt/offroad/eop_panel.h"

#include <cstdlib>
#include <array>
#include <memory>

#include <QFile>
#include <QComboBox>
#include <QFrame>
#include <QLabel>
#include <QProcess>
#include <QDialog>
#include <QListWidget>
#include <QMap>
#include <QVBoxLayout>
#include <QHBoxLayout>

#include "selfdrive/ui/qt/qt_window.h"
#include "selfdrive/ui/qt/widgets/controls.h"
#include "selfdrive/ui/qt/widgets/input.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"
#include "system/hardware/hw.h"

QWidget *settingsSection(const QString &title) {
  auto *w = new QWidget();
  w->setObjectName("settingsSection");  // ListWidget skips its dividers here
  w->setFixedHeight(56);
  auto *layout = new QHBoxLayout(w);
  layout->setContentsMargins(0, 20, 0, 0);
  layout->setSpacing(16);

  auto makeLine = [&]() {
    auto *l = new QFrame();
    l->setFrameShape(QFrame::HLine);
    l->setFixedHeight(1);
    l->setStyleSheet("background-color: #3a3a3a;");
    return l;
  };

  auto *lbl = new QLabel(title.toUpper());
  lbl->setStyleSheet("font-size: 16px; font-weight: bold; color: #E4E4E4; letter-spacing: 3px;");
  lbl->setSizePolicy(QSizePolicy::Minimum, QSizePolicy::Preferred);

  layout->addWidget(makeLine(), 1);
  layout->addWidget(lbl, 0, Qt::AlignCenter);
  layout->addWidget(makeLine(), 1);
  return w;
}

void EopPanel::add_param_toggle(const char *param, const QString &title, const QString &desc) {
  auto toggle = new ParamControl(param, title, desc, "", this);
  toggle->setEnabled(!params.getBool(std::string(param) + "Lock"));
  addItem(toggle);
  toggles[param] = toggle;
}

void EopPanel::add_lateral_toggles() {
  addItem(settingsSection(tr("Steering & Lane Change")));
  add_param_toggle("EOPLatALCC", tr("Always-on Lane Centering (ALCC)"),
      tr("Keeps steering active when cruise main is available."
         "\nCan optionally run without cruise and hold at a stop."));
  add_param_toggle("EOPALCCAllowAlways", tr("ALCC Without Cruise"),
      tr("Allow ALCC even when cruise main is off. Useful for platforms without an LCC button."));
  add_param_toggle("EOPALCCHoldAtStandstill", tr("ALCC Hold at Standstill"),
      tr("Keep steering torque active while stopped if ALCC is engaged."));
  addItem(new ButtonParamControl(
      "EOPALCCBrakeMode", tr("ALCC on Brake"),
      tr("What steering does when the brake pedal is pressed."
         "\nMaintain - keep steering active."
         "\nPause - hold steering until the brake is released."
         "\nDisengage - fully release ALCC when braking."),
      "", {tr("Maintain"), tr("Pause"), tr("Disengage")}));
  add_param_toggle("EOPDLPCurvesEnabled", tr("Curve Assist"),
      tr("Switch to laneless steering early for tight curves."));
  add_param_toggle("EOPLatRoadEdgeDetection", tr("Road Edge Detection"),
      tr("Block a lane change towards a detected road edge."));
  // DLAT (Laneful/Laneless arbitration) is always automatic; no control.

  add_param_toggle("EOPLCAControllerEnabled", tr("Lane Change Assist (LCA)"),
      tr("Blind-spot checks from all cameras before a lane change."));
  addItem(new ParamSpinBoxControl(
      "EOPLatLCASpeed", tr("Lane Change Assist From:"),
      tr("Minimum speed for lane change assist. Off = disabled."), "", 0, 160, 5, tr(" km/h"),
      tr("Off")));
  add_param_toggle("EOPAutoLaneChange", tr("Auto Lane Change"),
      tr("Change lanes automatically when the turn signal is on."));
  lane_change_delay_slider = new ParamDoubleSpinBoxControl(
      "EOPLaneChangeDelay", tr("Lane Change Delay:"),
      tr("Delay before the lane change starts after the turn signal."),
      "", 0.5, 5.0, 0.1, tr(" s"), tr(""));
  addItem(lane_change_delay_slider);
  minimum_lane_width_slider = new ParamDoubleSpinBoxControl(
      "EOPMinimumLaneWidth", tr("Minimum Lane Width:"),
      tr("Narrowest target lane a lane change is allowed into."), "", 2.0, 4.0,
      0.1, tr(" m"), tr(""));
  addItem(minimum_lane_width_slider);
  add_param_toggle("EOPOneLaneChange", tr("One Lane Change per Signal"),
      tr("Change at most one lane per turn signal activation."));
}
void EopPanel::add_enhanced_perception_toggles() {
  std::vector<std::tuple<QString, QString, QString>> toggle_defs{
      {
          "",
          tr("Enhanced Perception"),
          "",
      },
      {
          "StereoEnabled",
          tr("Stereo Depth Perception"),
          tr("Enable stereo vision for 3D object detection and depth "
             "estimation.\nEssential for accurate distance and height "
             "measurements.\nDisabled by default for upstream compatibility."),
      },
      {
          "LeftCameraEnabled",
          tr("Forward Left Camera Helper"),
          tr("Optional forward-left helper camera for lane change "
             "suggestions.\nBlind-spot logic uses radar; camera assists only "
             "when available."),
      },
      {
          "RightCameraEnabled",
          tr("Forward Right Camera Helper"),
          tr("Optional forward-right helper camera for lane change "
             "suggestions.\nBlind-spot logic uses radar; camera assists only "
             "when available."),
      },
      {
          "SideCamerasSwapped",
          tr("Swap Left/Right Cameras"),
          tr("Enable if side cameras are physically installed swapped.\n"
             "This corrects the data output without rewiring."),
      },
      {
          "GridEnabled",
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
      label = settingsSection(title);
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
  bool has_stereo = params.getBool("StereoEnabled");

  addItem(settingsSection(tr("Enhanced Controllers (Stereo Required)")));

  if (!has_stereo) {
    addItem(settingsSection(tr("Stereo cameras not detected. Enhanced features unavailable.")));
  }

  // Stereo Nudge Toggle
  auto nudge_toggle = new ParamControl(
      "NudgeEnabled",
      tr("Stereo Path Nudge"),
      tr("Enable stereo-based path enhancements:\n"
         "• LatNudge — lateral obstacle avoidance using stereo boundaries\n"
         "• LonNudge — speed reduction based on drivable distance and occupancy"),
      "", this);
  nudge_toggle->setEnabled(has_stereo);
  addItem(nudge_toggle);
  toggles["NudgeEnabled"] = nudge_toggle;

  // AEB Toggle (with warning)
  auto aeb_toggle = new ParamControl(
      "AEBEnabled",
      tr("Automatic Emergency Braking (AEB)"),
      tr("⚠️ SAFETY-CRITICAL: Requires extensive testing.\n"
         "RSS-based emergency braking for collision avoidance.\n"
         "Uses radar + vision + monod detections.\n"
         "Disabled by default - enable only after validation."),
      "", this);
  aeb_toggle->setEnabled(has_stereo);
  addItem(aeb_toggle);
  toggles["AEBEnabled"] = aeb_toggle;
  
  // RCD Toggle
  auto rcd_toggle = new ParamControl(
      "RCDEnabled",
      tr("Road Condition Detection (RCD)"),
      tr("Detects wet, icy, snowy, or debris-covered roads.\n"
         "Automatically reduces speed for hazardous conditions.\n"
         "Uses surface data + classical CV analysis."),
      "", this);
  rcd_toggle->setEnabled(has_stereo);
  addItem(rcd_toggle);
  toggles["RCDEnabled"] = rcd_toggle;
}

void EopPanel::add_longitudinal_toggles() {
  addItem(settingsSection(tr("Speed")));
  // DLON's ACC/E2E choice is Drive Mode at the top of this page.
  add_param_toggle("EOPVTSCEnabled", tr("Vision Curve Speed (VTSC)"),
      tr("Slow down for curves 0-250 m ahead, from the camera."));
  add_param_toggle("EOPMTSCEnabled", tr("Map Curve Speed (MTSC)"),
      tr("Slow down for curves 250-500 m ahead, from the offline map."));
  tsc_lat_accel_toggle = new ParamDoubleSpinBoxControl(
      "EOPTSCTargetLatAccel", tr("Curve Speed Limit:"),
      tr("Max lateral acceleration in curves. Lower = slower, more cautious."),
      "", 1.0, 2.5, 0.1, tr(" m/s²"), tr(""));
  addItem(tsc_lat_accel_toggle);
  add_param_toggle("EOPMSLCEnabled", tr("Map Speed Limit (MSLC)"),
      tr("Follow posted speed limits from the offline map."));
  add_param_toggle("EOPTLSCEnabled", tr("Traffic Light Speed (TLSC)"),
      tr("Slow down for yellow/red lights when no car is ahead."));
  add_param_toggle("EOPTJAEnabled", tr("Traffic Jam Assist (TJA)"),
      tr("Gentle acceleration after stops in stop-and-go traffic."));
  add_param_toggle("ngp_lon_brsc", tr("Bumpy Road Speed (BRSC)"),
      tr("Reduce speed on rough pavement, detected from the IMU. "
         "Recovers a few seconds after the road smooths out."));
  if (vehicle_has_radar_unavailable) {
    add_param_toggle("EOPLonExtRadar", tr("Use External Radar"),
        tr("Use the add-on forward radar for lead tracking."));
  }
}
void EopPanel::add_ui_toggles() {
  add_param_toggle("IsMetric", tr("Use Metric System"), tr("Display speed in km/h instead of mph."));
  add_param_toggle("EOPUIDisplayMode", tr("Detailed Driving View"),
      tr("Show extra visualisations on the driving screen."));
  add_param_toggle("EOPBEVWidgetEnabled", tr("Bird's Eye View"),
      tr("Top-down view of the car and surrounding objects on the driving screen."));
  if (vehicle_has_long_ctrl) {
    add_param_toggle("EOPUIRadarTracks", tr("Show Radar Tracks"), "");
  }
  add_param_toggle("EOPUIRainbow", tr("Rainbow Driving Path"), "");
  addItem(new ParamSpinBoxControl(
      "ngp_ui_hide_hud_speed_kph", tr("Hide HUD Above:"),
      tr("Hide speed, set speed and steering icons above this speed to prevent "
         "screen burn-in. Off = always shown."),
      "", 0, 120, 5, tr(" km/h"), tr("Off")));
}
void EopPanel::add_car_adaptive_toggles() {
  addItem(settingsSection(tr("Car Adaptive Tuning")));
  add_param_toggle("EOPCATManualSREnabled", tr("Use Fixed Steer Ratio"),
      tr("Stop learning steer ratio and stiffness from driving; use the fixed value."));
}
void EopPanel::add_recording_controls() {
  if (params.get("EOPRecordEnabled").empty()) params.putBool("EOPRecordEnabled", true);
  addItem(settingsSection(tr("Recording")));
  recorders_toggle = new ParamControl(
      "EOPRecordEnabled", tr("On-Road Recording"),
      tr("Loop recording, impact detection and snapshots when storage is present."),
      "", this);
  addItem(recorders_toggle);
  add_param_toggle("RecordAudio", tr("Record Microphone Audio"),
      tr("Include audio in recordings. Changing this restarts ExoPilot if the car is on."));
}
void EopPanel::add_calibration_section() {
  addItem(settingsSection(tr("Calibration")));
  const bool factory_calibrated = params.getBool("EOPFactoryCalibrated");
  addItem(new LabelControl(tr("Factory Calibration"),
                           factory_calibrated ? tr("calibrated") : tr("not calibrated")));
}
void EopPanel::add_gps_rtk_toggles() {
  addItem(settingsSection(tr("GPS / RTK Corrections")));

  auto rtk_toggle = new ParamControl(
      "RTKEnabled",
      tr("Enable RTK GPS"),
      tr("Activate centimeter-level positioning via u-blox ZED-F9P-04B.\n"
         "Baud is auto-negotiated from 38400 (factory) to 115200 on boot."),
      "", this);
  addItem(rtk_toggle);
  toggles["RTKEnabled"] = rtk_toggle;

  auto ntrip_toggle = new ParamControl(
      "NTRIPEnabled",
      tr("Enable NTRIP Corrections"),
      tr("Receive RTCM3.3 differential corrections for RTK Fixed mode."),
      "", this);
  addItem(ntrip_toggle);
  toggles["NTRIPEnabled"] = ntrip_toggle;
}

void EopPanel::add_vehicle_platform_section() {
  addItem(settingsSection(tr("Vehicle")));

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

  QString current_type = QString::fromStdString(params.get("VehicleType"));
  if (current_type.isEmpty()) {
    current_type = "SUV_C";
    params.put("VehicleType", current_type.toStdString());
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
        background-color: #2f7566;
      }
    )");

    for (const QString &type : vehicle_types) {
      list->addItem(type);
    }

    // Select current item
    QString current_display = param_to_display(QString::fromStdString(params.get("VehicleType")));
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
      params.put("VehicleType", selected.toStdString());
      vehicle_type_btn->setText(param_to_display(selected));
      dialog.accept();
    });

    dialog.exec();
  });

  addItem(vehicle_type_btn);
  add_param_toggle("EOPDeviceIsRhd", tr("Right-Hand Drive"),
      tr("The driver sits on the right (left-hand traffic)."));
}

void EopPanel::add_device_toggles() {
  addItem(settingsSection(tr("Phone")));
  add_param_toggle("EOPNavBleEnabled", tr("Navigation App over Bluetooth"),
      tr("Send destinations from the ExoPilot phone app over Bluetooth."));

  addItem(settingsSection(tr("Power")));
  addItem(new ParamSpinBoxControl(
      "EOPDeviceAutoShutdownIn", tr("Auto Shutdown After:"),
      tr("Time after the car turns off. 0 mins = immediately."), "", -5, 300, 5, tr(" mins"), tr("Off")));
}
EopPanel::EopPanel(SettingsWindow *parent, Page page) : ListWidget(parent) {
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
  ensure_on("MapdEnabled");    // auto-started by MTSC/MSLC/NAV
  ensure_on("CATEnabled");     // always learns from driving
  ensure_on("BSDEnabled");     // safety: blind spot detection always active
  // driveractivityd is always registered onroad; there is no monitoring-off key.

  switch (page) {
    case DRIVING:
      add_lateral_toggles();
      add_longitudinal_toggles();
      break;
    case SAFETY:
      add_safety_toggles();
      break;
    case DISPLAY:
      add_ui_toggles();
      break;
    case DEVICE_VEHICLE:
      add_vehicle_platform_section();
      add_calibration_section();
      break;
    case DEVICE_SYSTEM:
      add_device_toggles();
      add_recording_controls();
      break;
    case ADVANCED:
      // Engineering settings, behind the Dongle ID unlock (owner 2026-11-11).
      add_car_adaptive_toggles();
      add_enhanced_perception_toggles();
      add_enhanced_controllers_section();
      add_monod_toggles();
      add_globald_toggles();
      add_gps_rtk_toggles();
      add_surface_toggles();
      add_pointcloud_toggles();
      break;
  }

  fs_watch = new ParamWatcher(this);

  // Register all watched params once in the constructor
  for (const char *p : {
    "LatLCASpeed", "LonExtRadar",
    "VTSCEnabled", "MTSCEnabled", "NavBleEnabled",
    "LCAControllerEnabled",
    "AutoLaneChange", "LaneChangeDelay", "MinimumLaneWidth", "OneLaneChange",
    "UIDisplayMode",
    "MSLCEnabled",
    "MultiCameraCalibEnabled", "FactoryCalibrated",
    "RearCameraEnabled",
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

  if (tsc_lat_accel_toggle) {
    tsc_lat_accel_toggle->setVisible(params.getBool("EOPVTSCEnabled") || params.getBool("EOPMTSCEnabled"));
  }
}

void EopPanel::add_safety_toggles() {
  addItem(settingsSection(tr("Warnings")));
  add_param_toggle("IsLdwEnabled", tr("Lane Departure Warnings"),
      tr("Alert when drifting over a lane line without the turn signal above 50 km/h."));
  add_param_toggle("EOPBSDChimeEnabled", tr("Blind Spot Chime"),
      tr("Chime when a fast-approaching vehicle enters the blind spot. Blind spot detection itself is always on."));
  addItem(new ButtonParamControl(
      "EOPDeviceAudibleAlertMode", tr("Alert Sound"),
      tr("Standard - all alerts. Warnings - warnings only. Off - silent."),
      "", {tr("Standard"), tr("Warnings"), tr("Off")}));
  add_param_toggle("EOPDeviceBeep", tr("Warning Beep"), "");

  addItem(settingsSection(tr("Driver Attention")));
  // ngp_dm_policy is a string param, so this is a plain MultiButtonControl.
  auto sam = new MultiButtonControl(tr("Steering Activity Monitor"),
      tr("How quickly hands-off time builds up.\nRelaxed - the earlier behaviour.\nTight - warns sooner."),
      "", {tr("Relaxed"), tr("Tight")});
  sam->setCheckedButton(params.get("ngp_dm_policy") == "tight" ? 1 : 0);
  connect(sam, &MultiButtonControl::buttonClicked, this, [=](int id) {
    params.put("ngp_dm_policy", id == 1 ? "tight" : "relaxed");
  });
  addItem(sam);
  add_param_toggle("EOPRearCameraEnabled", tr("Rear Camera View"),
      tr("Show the rear camera when reverse is engaged."));
}
void EopPanel::add_voice_ai_toggles() {
  addItem(settingsSection(tr("Voice Input")));

  auto voice_toggle = new ParamControl(
      "VoiceEnabled",
      tr("Voice Input (Local)"),
      tr("On-device microphone beamforming and voice-activity detection. Offline; no wake word, speech-to-text or cloud assistant yet."),
      "", this);
  addItem(voice_toggle);
  toggles["VoiceEnabled"] = voice_toggle;
}

void EopPanel::add_driver_toggles() {
  // Driver attention rows live in add_safety_toggles().
}
void EopPanel::add_monod_toggles() {
  addItem(settingsSection(tr("Hailo Long-Range Detection (MonoD)")));

  // Master toggle
  auto monod_toggle = new ParamControl(
      "MonoDEnabled",
      tr("Enable Long-Range Detection"),
      tr("Activate Hailo-8 inference for distant object detection."
         "\nExtends detection range to 500m using 16mm tele_road camera."),
      "", this);
  addItem(monod_toggle);
  toggles["MonoDEnabled"] = monod_toggle;

  // Scene segmentation toggle
  auto sceneseg_toggle = new ParamControl(
      "MonoDSceneSegEnabled",
      tr("Enable Scene Segmentation"),
      tr("Run PP-LiteSeg on tele_road feed for semantic understanding."),
      "", this);
  addItem(sceneseg_toggle);
  toggles["MonoDSceneSegEnabled"] = sceneseg_toggle;

  // Camera toggles
  auto wide_toggle = new ParamControl(
      "MonoDWideEnabled",
      tr("Enable 1.7mm Wide Camera"),
      tr("Use ultra-wide camera for close-range blind spot coverage."),
      "", this);
  addItem(wide_toggle);
  toggles["MonoDWideEnabled"] = wide_toggle;

  auto tele_toggle = new ParamControl(
      "MonoDTeleEnabled",
      tr("Enable 16mm TeleRoad Camera"),
      tr("Use tele_road camera for long-range detection (primary MonoD input)."),
      "", this);
  addItem(tele_toggle);
  toggles["MonoDTeleEnabled"] = tele_toggle;

  // YOLO confidence threshold
  auto yolo_conf = new ParamDoubleSpinBoxControl(
      "MonoDYoloConf",
      tr("YOLO Confidence Threshold:"),
      tr("Minimum confidence for object detection. Lower = more detections but more false positives."),
      "", 0.1, 0.9, 0.05, "", "", 0.5);
  addItem(yolo_conf);

  // Max tracks
  auto max_tracks = new ParamSpinBoxControl(
      "MonoDMaxTracks",
      tr("Max Tracked Objects:"),
      tr("Maximum number of objects to track simultaneously."),
      "", 16, 128, 8, "", "", 64);
  addItem(max_tracks);
}

void EopPanel::add_pointcloud_toggles() {
  addItem(settingsSection(tr("3D Point Cloud Recording")));

  // Master toggle
  auto pc_toggle = new ParamControl(
      "PointcloudEnabled",
      tr("Enable Point Cloud Recording"),
      tr("Save 3D reconstructions from stereo depth to SD card."
         "\nUsed for fleet mapping and digital twin generation."
         "\nDoes not affect core ADAS functionality."),
      "", this);
  addItem(pc_toggle);
  toggles["PointcloudEnabled"] = pc_toggle;

  // Recording rate
  auto pc_rate = new ParamSpinBoxControl(
      "PointcloudRateHz",
      tr("Recording Rate (Hz):"),
      tr("Frame rate for point cloud capture. Higher = more data but more storage."),
      "", 1, 20, 1, " Hz", "", 5);
  addItem(pc_rate);

  // Max storage
  auto pc_storage = new ParamDoubleSpinBoxControl(
      "PointcloudMaxGB",
      tr("Max Storage (GB):"),
      tr("Maximum storage for point clouds. Oldest data auto-deleted when exceeded."),
      "", 1.0, 32.0, 0.5, " GB", "", 4.0);
  addItem(pc_storage);

  // GPU acceleration toggle
  auto pc_gpu = new ParamControl(
      "PointcloudUseGPU",
      tr("Use GPU Acceleration"),
      tr("Use Mali GPU for 3D reprojection. Faster but uses GPU resources."
         "\nFalls back to CPU if GPU unavailable."),
      "", this);
  addItem(pc_gpu);
  toggles["PointcloudUseGPU"] = pc_gpu;
}

void EopPanel::add_globald_toggles() {
  addItem(settingsSection(tr("Global Localization")));

  // Master toggle
  auto globald_toggle = new ParamControl(
      "GlobaldEnabled",
      tr("Enable Global Localization"),
      tr("Fuse GPS with OSM road data and SGM point cloud matching."
         "\nProvides accurate lane-level positioning without RTK."),
      "", this);
  addItem(globald_toggle);
  toggles["GlobaldEnabled"] = globald_toggle;

  // SGM Localizer toggle
  auto sgm_localizer_toggle = new ParamControl(
      "SGMLocalizerEnabled",
      tr("Enable SGM Point Cloud Matching"),
      tr("Match live stereo point clouds against SGM 3D map tiles."
         "\nRequires pre-built SGM map tiles in /data/maps/sgm/"),
      "", this);
  addItem(sgm_localizer_toggle);
  toggles["SGMLocalizerEnabled"] = sgm_localizer_toggle;

  // SGM Mode
  auto sgm_mode = new ButtonParamControl(
      "SGMMode",
      tr("SGM Matching Mode"),
      tr("Select localization mode:"
         "\nLive - Match against live point clouds only"
         "\nMap - Match against pre-built SGM tiles only"
         "\nFused - Combine both sources for best accuracy"),
      "", {tr("Live"), tr("Map"), tr("Fused")});
  addItem(sgm_mode);

  // Confidence threshold
  auto sgm_conf = new ParamDoubleSpinBoxControl(
      "SGMConfidenceThreshold",
      tr("Match Confidence Threshold:"),
      tr("Minimum confidence for SGM position match. Higher = more reliable but fewer matches."),
      "", 0.3, 0.95, 0.05, "", "", 0.7);
  addItem(sgm_conf);

  // Max range
  auto sgm_range = new ParamDoubleSpinBoxControl(
      "SGMMaxRange",
      tr("Max Matching Range (m):"),
      tr("Maximum search radius for point cloud matching."),
      "", 20.0, 200.0, 10.0, " m", "", 100.0);
  addItem(sgm_range);

  // Auto tile download toggle (for navigation)
  auto auto_tile_toggle = new ParamControl(
      "AutoTileEnabled",
      tr("Auto-Download Map Tiles"),
      tr("Automatically download OSM and SGM tiles based on GPS location."
         "\nRequires internet connection."),
      "", this);
  addItem(auto_tile_toggle);
  toggles["AutoTileEnabled"] = auto_tile_toggle;

  auto wifi_only_toggle = new ParamControl(
      "AutoTileWifiOnly",
      tr("WiFi-Only Downloads"),
      tr("Only auto-download tiles when connected to WiFi to save cellular data."),
      "", this);
  addItem(wifi_only_toggle);
  toggles["AutoTileWifiOnly"] = wifi_only_toggle;
}

void EopPanel::add_surface_toggles() {
  addItem(settingsSection(tr("Surface Quality Mapping")));

  // Grid resolution
  auto grid_res = new ParamDoubleSpinBoxControl(
      "SurfaceGridResolution",
      tr("Grid Resolution (m):"),
      tr("Size of each grid cell for surface quality mapping."),
      "", 0.1, 1.0, 0.05, " m", "", 0.25);
  addItem(grid_res);

  // Grid range
  auto grid_range = new ParamDoubleSpinBoxControl(
      "SurfaceGridRange",
      tr("Grid Forward Range (m):"),
      tr("How far ahead to map surface conditions."),
      "", 30.0, 150.0, 10.0, " m", "", 60.0);
  addItem(grid_range);

  // Grid width
  auto grid_width = new ParamDoubleSpinBoxControl(
      "SurfaceGridWidth",
      tr("Grid Width (m):"),
      tr("Lateral coverage of surface quality grid."),
      "", 10.0, 60.0, 5.0, " m", "", 30.0);
  addItem(grid_width);

  // Long horizon toggle
  auto long_horizon = new ParamControl(
      "SurfaceLongHorizon",
      tr("Enable Long Horizon"),
      tr("Extend surface quality detection to 100m for highway comfort."
         "\nUses more GPU resources."),
      "", this);
  addItem(long_horizon);
  toggles["SurfaceLongHorizon"] = long_horizon;
}

void EopPanel::expandToggleDescription(const QString &param) {
  toggles[param.toStdString()]->showDescription();
}
