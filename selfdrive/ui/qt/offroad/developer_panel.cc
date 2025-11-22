#include "selfdrive/ui/qt/offroad/developer_panel.h"
#include "selfdrive/ui/qt/widgets/ssh_keys.h"
#include "selfdrive/ui/qt/widgets/controls.h"
#include <QJsonDocument>
#include <QJsonObject>

DeveloperPanel::DeveloperPanel(SettingsWindow *parent) : ListWidget(parent) {
  adbToggle = new ParamControl("AdbEnabled", tr("Enable ADB"),
            tr("ADB (Android Debug Bridge) allows connecting to your device over USB or over the network. See https://docs.comma.ai/how-to/connect-to-comma for more info."), "");
  addItem(adbToggle);

  // SSH keys
  addItem(new SshToggle());
  addItem(new SshControl());

  joystickToggle = new ParamControl("JoystickDebugMode", tr("Joystick Debug Mode"), "", "");
  QObject::connect(joystickToggle, &ParamControl::toggleFlipped, [=](bool state) {
    params.putBool("LongitudinalManeuverMode", false);
    longManeuverToggle->refresh();
  });
  addItem(joystickToggle);

  longManeuverToggle = new ParamControl("LongitudinalManeuverMode", tr("Longitudinal Maneuver Mode"), "", "");
  QObject::connect(longManeuverToggle, &ParamControl::toggleFlipped, [=](bool state) {
    params.putBool("JoystickDebugMode", false);
    joystickToggle->refresh();
  });
  addItem(longManeuverToggle);

  experimentalLongitudinalToggle = new ParamControl(
    "AlphaLongitudinalEnabled",
    tr("openpilot Longitudinal Control (Alpha)"),
    QString("<b>%1</b><br><br>%2")
      .arg(tr("WARNING: openpilot longitudinal control is in alpha for this car and will disable Automatic Emergency Braking (AEB)."))
      .arg(tr("On this car, openpilot defaults to the car's built-in ACC instead of openpilot's longitudinal control. "
              "Enable this to switch to openpilot longitudinal control. Enabling Experimental mode is recommended when enabling openpilot longitudinal control alpha.")),
    ""
  );
  experimentalLongitudinalToggle->setConfirmation(true, false);
  QObject::connect(experimentalLongitudinalToggle, &ParamControl::toggleFlipped, [=]() {
    updateToggles(offroad);
  });
  addItem(experimentalLongitudinalToggle);

  // Joystick and longitudinal maneuvers should be hidden on release branches
  is_release = params.getBool("IsReleaseBranch");

  // Toggles should be not available to change in onroad state
  QObject::connect(uiState(), &UIState::offroadTransition, this, &DeveloperPanel::updateToggles);

  // error logs
  QPushButton* error_log_btn = new QPushButton(QObject::tr("Show Last Errors"));
  error_log_btn->setObjectName("error_log_btn");

  error_log_btn->setStyleSheet(R"(
    #error_log_btn { height: 120px; border-radius: 15px; background-color: #393939; }
    #error_log_btn:pressed { background-color: #4a4a4a; }
  )");

  addItem(error_log_btn);

  QObject::connect(error_log_btn, &QPushButton::clicked, [=]() {
    ConfirmationDialog::rich(QString::fromStdString(params.get("np_device_last_log")), parent);
  });

  np_metrics = new LabelControl(tr("NP Metrics"), tr("Waiting..."));
  addItem(np_metrics);
  refreshNpMetrics();
}

void DeveloperPanel::updateToggles(bool _offroad) {
  for (auto btn : findChildren<ParamControl *>()) {
    btn->setVisible(!is_release);

    /*
     * experimentalLongitudinalToggle should be toggelable when:
     * - visible, and
     * - during onroad & offroad states
     */
    if (btn != experimentalLongitudinalToggle) {
      btn->setEnabled(_offroad);
    }
  }

  // longManeuverToggle and experimentalLongitudinalToggle should not be toggleable if the car does not have longitudinal control
  auto cp_bytes = params.get("CarParamsPersistent");
  if (!cp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();

    if (!CP.getAlphaLongitudinalAvailable() || is_release) {
      params.remove("AlphaLongitudinalEnabled");
      experimentalLongitudinalToggle->setEnabled(false);
    }

    /*
     * experimentalLongitudinalToggle should be visible when:
     * - is not a release branch, and
     * - the car supports experimental longitudinal control (alpha)
     */
    experimentalLongitudinalToggle->setVisible(CP.getAlphaLongitudinalAvailable() && !is_release);

    longManeuverToggle->setEnabled(hasLongitudinalControl(CP) && _offroad);
  } else {
    longManeuverToggle->setEnabled(false);
    experimentalLongitudinalToggle->setVisible(false);
  }
  experimentalLongitudinalToggle->refresh();

  offroad = _offroad;
  refreshNpMetrics();
}

void DeveloperPanel::showEvent(QShowEvent *event) {
  updateToggles(offroad);
}

void DeveloperPanel::refreshNpMetrics() {
  QString cat_json = QString::fromStdString(params.get("np_cat_status"));
  QString text = tr("NP telemetry unavailable");
  if (!cat_json.isEmpty()) {
    QJsonParseError err;
    QJsonDocument doc = QJsonDocument::fromJson(cat_json.toUtf8(), &err);
    if (err.error == QJsonParseError::NoError && doc.isObject()) {
      auto obj = doc.object();
      const QString mode = obj.value("manualOverride").toBool(false) ? "Manual" :
                           (obj.value("adaptive").toBool(false) ? "Adaptive" : "Learning");
      const double conf = obj.value("confidence").toDouble(0.0);
      const double sr = obj.value("steerRatio").toDouble(0.0);
      const double stiff = obj.value("stiffnessFactor").toDouble(0.0);
      const int samples = obj.value("samples").toInt(0);
      text = QString("CAT %1 | conf %2 | sr %3 | stiff %4 | samples %5")
               .arg(mode)
               .arg(conf, 0, 'f', 2)
               .arg(sr, 0, 'f', 3)
               .arg(stiff, 0, 'f', 3)
               .arg(samples);
    }
  }
  // Append stack telemetry if available
  QString stack_json = QString::fromStdString(params.get("np_stack_status"));
  if (!stack_json.isEmpty()) {
    QJsonParseError err;
    QJsonDocument doc = QJsonDocument::fromJson(stack_json.toUtf8(), &err);
    if (err.error == QJsonParseError::NoError && doc.isObject()) {
      auto obj = doc.object();
      auto dlp = obj.value("dlp").toObject();
      auto tsc = obj.value("tsc").toObject();
      auto dem = obj.value("dem").toObject();
      QStringList parts;
      parts << QString("DLP %1/%2 conf %3")
                   .arg(dlp.value("available").toBool() ? tr("avail") : tr("na"))
                   .arg(dlp.value("active").toBool() ? tr("on") : tr("off"))
                   .arg(dlp.value("confidence").toDouble(), 0, 'f', 2);
      parts << QString("TSC %1 state %2 v %3 m %4%5")
                   .arg(tsc.value("active").toBool() ? tr("on") : tr("off"))
                   .arg(tsc.value("state").toInt())
                   .arg(tsc.value("visionSpeed").toDouble(), 0, 'f', 1)
                   .arg(tsc.value("mapSpeed").toDouble(), 0, 'f', 1)
                   .arg(tsc.value("mapStale").toBool() ? tr(" stale") : "");
      parts << QString("DEM %1 health %2")
                   .arg(dem.value("active").toBool() ? tr("on") : tr("off"))
                   .arg(dem.value("health").toDouble(), 0, 'f', 2);
      text += "\n" + parts.join(" | ");
    }
  }
  np_metrics->setText(text);
}
