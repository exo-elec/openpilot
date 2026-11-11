#include <cassert>
#include <cmath>
#include <string>
#include <tuple>
#include <vector>

#include <algorithm>

#include <QDateTime>
#include <QFile>
#include <QNetworkInterface>
#include <QDebug>
#include <QEvent>

#include "common/watchdog.h"
#include "common/util.h"
#include "selfdrive/ui/qt/network/networking.h"
#include "selfdrive/ui/qt/offroad/settings.h"
#include "selfdrive/ui/qt/theme.h"
#include "selfdrive/ui/qt/qt_window.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"
#include "selfdrive/ui/qt/offroad/eop_panel.h"
#include "selfdrive/ui/qt/widgets/bluetooth.h"
#include "selfdrive/ui/qt/network/bluetooth_manager.h"

TogglesPanel::TogglesPanel(SettingsWindow *parent) : ListWidget(parent) {
  // Settings -> Driving. Top: the three choices a driver actually makes;
  // then steering & lane change and speed (EopPanel::DRIVING).
  auto enable = new ParamControl("OpenpilotEnabledToggle", tr("Enable ExoPilot"),
      tr("Adaptive cruise control and lane keep assist. Pay attention at all times."),
      "../assets/icons/chffr_wheel.png", this);
  enable->setDescription(enable->getDescription() + tr(" Changing this setting will restart ExoPilot if the car is powered on."));
  QObject::connect(uiState(), &UIState::engagedChanged, [enable](bool engaged) { enable->setEnabled(!engaged); });
  QObject::connect(enable, &ParamControl::toggleFlipped, [=](bool) { params.putBool("OnroadCycleRequested", true); });
  addItem(enable);
  toggles["OpenpilotEnabledToggle"] = enable;

  // Drive Mode replaces openpilot's Experimental mode: DLON picks ACC (rule)
  // or E2E (AI), or switches between them (Dynamic). See DLON.md.
  if (params.get("EOPDlonMode").empty()) params.put("EOPDlonMode", "2");
  drive_mode_setting = new ButtonParamControl("EOPDlonMode", tr("Drive Mode"),
      tr("Highway (Rule): rule-based cruise, best for open roads.\n"
         "Urban (AI): the driving model controls speed, stops for lights.\n"
         "Dynamic: switches between them automatically (recommended)."),
      "../assets/icons/speed_limit.png",
      {tr("Highway (Rule)"), tr("Urban (AI)"), tr("Dynamic")});
  addItem(drive_mode_setting);

  long_personality_setting = new ButtonParamControl("LongitudinalPersonality", tr("Driver Personality"),
      tr("How closely ExoPilot follows lead cars. Standard recommended. "
         "Also changeable while driving: tap the round button at the top right."),
      "../assets/icons/speed_limit.png",
      {tr("Aggressive"), tr("Standard"), tr("Relaxed")});
  addItem(long_personality_setting);

  auto doa = new ParamControl("DisengageOnAccelerator", tr("Disengage on Accelerator"),
      tr("Pressing the accelerator will disengage ExoPilot."),
      "../assets/icons/disengage_on_accelerator.svg", this);
  addItem(doa);
  toggles["DisengageOnAccelerator"] = doa;

  addItem(new EopPanel(parent, EopPanel::DRIVING));

  QObject::connect(uiState(), &UIState::uiUpdate, this, &TogglesPanel::updateState);
}

void TogglesPanel::updateState(const UIState &s) {
  const SubMaster &sm = *(s.sm);

  if (sm.updated("selfdriveState")) {
    auto personality = sm["selfdriveState"].getSelfdriveState().getPersonality();
    if (personality != s.scene.personality && s.scene.started && isVisible()) {
      long_personality_setting->setCheckedButton(static_cast<int>(personality));
    }
    uiState()->scene.personality = personality;
  }
}

void TogglesPanel::expandToggleDescription(const QString &param) {
  if (auto it = toggles.find(param.toStdString()); it != toggles.end()) it->second->showDescription();
}

void TogglesPanel::scrollToToggle(const QString &param) {
  if (auto it = toggles.find(param.toStdString()); it != toggles.end()) {
    auto scroll_area = qobject_cast<QScrollArea*>(parent()->parent());
    if (scroll_area) {
      scroll_area->ensureWidgetVisible(it->second);
    }
  }
}

void TogglesPanel::showEvent(QShowEvent *event) {
  updateToggles();
}

void TogglesPanel::updateToggles() {
  // Drive Mode and personality only act with ExoPilot longitudinal control.
  auto cp_bytes = params.get("CarParamsPersistent");
  if (!cp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();
    const bool has_long = hasLongitudinalControl(CP);
    drive_mode_setting->setEnabled(has_long);
    long_personality_setting->setEnabled(has_long);
  }
}

DevicePanel::DevicePanel(SettingsWindow *parent) : ListWidget(parent) {
  setSpacing(50);
  // Only the last 6 characters: enough to tell devices apart on screen.
  auto tail6 = [](const QString &id) { return id.size() > 6 ? QString::fromUtf8("…") + id.right(6) : id; };
  auto dongle = new LabelControl(tr("Dongle ID"), tail6(getDongleId().value_or(tr("N/A"))),
      tr("This device's identity for logs and uploads."));
  // 8 quick taps anywhere on this row reveal the Advanced page.
  dongle->installEventFilter(this);
  for (auto *child : dongle->findChildren<QWidget *>()) child->installEventFilter(this);
  addItem(dongle);
  addItem(new LabelControl(tr("Serial"), tail6(QString::fromStdString(params.get("HardwareSerial"))),
      tr("The board's hardware serial number.")));

  // Network: refreshed every 2 s while the page is open.
  wifiLbl = new LabelControl(tr("Wi-Fi Signal"), "");
  ipLbl = new LabelControl(tr("IP Address"), "");
  addItem(wifiLbl);
  addItem(ipLbl);
  net_timer = new QTimer(this);
  connect(net_timer, &QTimer::timeout, this, &DevicePanel::updateNetworkInfo);

  // Vehicle (type, RHD) and calibration status
  addItem(new EopPanel(parent, EopPanel::DEVICE_VEHICLE));

  resetCalibBtn = new ButtonControl(tr("Reset Calibration"), tr("RESET"), "");
  connect(resetCalibBtn, &ButtonControl::showDescriptionEvent, this, &DevicePanel::updateCalibDescription);
  connect(resetCalibBtn, &ButtonControl::clicked, [&]() {
    if (!uiState()->engaged()) {
      if (ConfirmationDialog::confirm(tr("Are you sure you want to reset calibration? Factory camera intrinsics are kept."), tr("Reset"), this)) {
        // Check engaged again in case it changed while the dialog was open
        if (!uiState()->engaged()) {
          params.remove("CalibrationParams");
          params.remove("CameraCalibrationParams");
          params.remove("LiveTorqueParameters");
          params.remove("LiveParameters");
          params.remove("LiveParametersV2");
          params.remove("LiveDelay");
          params.putBool("OnroadCycleRequested", true);
          updateCalibDescription();
        }
      }
    } else {
      ConfirmationDialog::alert(tr("Disengage to Reset Calibration"), this);
    }
  });
  addItem(resetCalibBtn);

  // Software: one branch per product, so only check / download / install.
  addItem(settingsSection(tr("Software")));
  addItem(new SoftwarePanel(this));

  addOfflineMaps();

  // Phone, recording, language, power
  addItem(new EopPanel(parent, EopPanel::DEVICE_SYSTEM));

  auto retrainingBtn = new ButtonControl(tr("Review Training Guide"), tr("REVIEW"), "");
  connect(retrainingBtn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to review the training guide?"), tr("Review"), this)) {
      emit reviewTrainingGuide();
    }
  });
  addItem(retrainingBtn);

  auto translateBtn = new ButtonControl(tr("Change Language"), tr("CHANGE"), "");
  connect(translateBtn, &ButtonControl::clicked, [=]() {
    QMap<QString, QString> langs = getSupportedLanguages();
    QString selection = MultiOptionDialog::getSelection(tr("Select a language"), langs.keys(), langs.key(uiState()->language), this);
    if (!selection.isEmpty()) {
      // Sync Language (2-letter TTS code) with UI language selection.
      // Mapping: main_xx[-YY] → 2-letter code; zh-CHS/CHT both → "zh"
      static const QMap<QString, QString> langToEop = {
        {"main_en", "en"}, {"main_de", "de"}, {"main_fr", "fr"},
        {"main_pt-BR", "pt"}, {"main_es", "es"}, {"main_tr", "tr"},
        {"main_ar", "ar"}, {"main_nl", "nl"}, {"main_pl", "pl"},
        {"main_zh-CHS", "zh"}, {"main_zh-CHT", "zh"},
        {"main_ja", "ja"}, {"main_ko", "ko"}, {"main_th", "th"},
      };
      QString langFile = langs[selection];
      QString eopLang = langToEop.value(langFile, "en");
      params.put("Language", eopLang.toStdString());
      params.put("LanguageSetting", langFile.toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    }
  });
  addItem(translateBtn);

  QObject::connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    for (auto btn : {retrainingBtn, translateBtn, mapRegionBtn, mapDownloadBtn}) {
      if (btn) btn->setEnabled(offroad);
    }
  });

  // power buttons
  QHBoxLayout *power_layout = new QHBoxLayout();
  power_layout->setSpacing(30);

  QPushButton *reboot_btn = new QPushButton(tr("Reboot"));
  reboot_btn->setObjectName("reboot_btn");
  power_layout->addWidget(reboot_btn);
  QObject::connect(reboot_btn, &QPushButton::clicked, this, &DevicePanel::reboot);

  QPushButton *poweroff_btn = new QPushButton(tr("Power Off"));
  poweroff_btn->setObjectName("poweroff_btn");
  power_layout->addWidget(poweroff_btn);
  QObject::connect(poweroff_btn, &QPushButton::clicked, this, &DevicePanel::poweroff);

  if (!Hardware::PC()) {
    connect(uiState(), &UIState::offroadTransition, poweroff_btn, &QPushButton::setVisible);
  }

  reboot_btn->setStyleSheet(theme::controlStyle(theme::Control::SECONDARY));
  poweroff_btn->setStyleSheet(theme::controlStyle(theme::Control::DANGER));
  addItem(power_layout);
}

bool DevicePanel::eventFilter(QObject *obj, QEvent *event) {
  // Fast taps arrive as press / double-click alternately, and a press on the
  // value label propagates to the row as well: count each tap once.
  if (event->type() == QEvent::MouseButtonPress || event->type() == QEvent::MouseButtonDblClick) {
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    if (now - last_tap_ms >= 40) {
      dongle_taps = (now - last_tap_ms <= 600) ? dongle_taps + 1 : 1;  // continuous taps only
      last_tap_ms = now;
      if (dongle_taps >= 8) {
        dongle_taps = 0;
        emit advancedUnlocked();
      }
    }
  }
  return ListWidget::eventFilter(obj, event);
}

void DevicePanel::showEvent(QShowEvent *event) {
  updateNetworkInfo();
  net_timer->start(2000);
  ListWidget::showEvent(event);
}

void DevicePanel::hideEvent(QHideEvent *event) {
  net_timer->stop();
  ListWidget::hideEvent(event);
}

void DevicePanel::updateNetworkInfo() {
  // IPv4 of the first interface that is up: Wi-Fi first, then Ethernet.
  QString ip;
  for (const QString &name : {QString("wlan0"), QString("eth0")}) {
    const QNetworkInterface iface = QNetworkInterface::interfaceFromName(name);
    if (!iface.isValid() || !(iface.flags() & QNetworkInterface::IsUp)) continue;
    for (const QNetworkAddressEntry &e : iface.addressEntries()) {
      if (e.ip().protocol() == QAbstractSocket::IPv4Protocol) { ip = e.ip().toString(); break; }
    }
    if (!ip.isEmpty()) break;
  }
  ipLbl->setText(ip.isEmpty() ? tr("not connected") : ip);

  // /proc/net/wireless: "wlan0: 0000   5.  -44.  -91. ..." (link, level dBm, noise).
  // The RTL8822CE's "link" column is not a 0-70 quality, so use the level:
  // -100 dBm -> 0 %, -50 dBm or better -> 100 %.
  QString signal = tr("not connected");
  QFile f("/proc/net/wireless");
  if (f.open(QIODevice::ReadOnly | QIODevice::Text)) {
    for (const QString &line : QString(f.readAll()).split('\n')) {
      const QStringList cols = line.simplified().split(' ');
      if (cols.size() >= 4 && cols[0] == "wlan0:") {
        const int dbm = int(cols[3].toFloat());
        const int quality = std::clamp(2 * (dbm + 100), 0, 100);
        const QString word = quality >= 70 ? tr("excellent") : quality >= 50 ? tr("good") : quality >= 30 ? tr("fair") : tr("weak");
        signal = tr("%1% %2 (%3 dBm)").arg(quality).arg(word).arg(dbm);
      }
    }
  }
  wifiLbl->setText(signal);
}

// Offline maps: OSM extracts for Valhalla routing and the onroad map.
// The UI only records the request (EOPMapDownloadRequest); the tile manager
// downloads/builds and reports progress in EOPMapStatus.
void DevicePanel::addOfflineMaps() {
  static const QMap<QString, QString> regions = {
    {tr("Thailand"), "thailand"}, {tr("Malaysia / Singapore"), "malaysia-singapore"},
    {tr("Japan"), "japan"}, {tr("South Korea"), "south-korea"}, {tr("Taiwan"), "taiwan"},
  };
  addItem(settingsSection(tr("Offline Maps")));

  mapRegionBtn = new ButtonControl(tr("Map Region"), tr("CHANGE"),
      tr("Region used for offline routing, speed limits and the map view."));
  connect(mapRegionBtn, &ButtonControl::clicked, [=]() {
    const QString cur = regions.key(QString::fromStdString(params.get("EOPMapRegion")));
    const QString sel = MultiOptionDialog::getSelection(tr("Select a map region"), regions.keys(), cur, this);
    if (!sel.isEmpty()) {
      params.put("EOPMapRegion", regions[sel].toStdString());
      updateMapStatus();
    }
  });
  addItem(mapRegionBtn);

  mapDownloadBtn = new ButtonControl(tr("Map Data"), tr("DOWNLOAD"),
      tr("Downloads the region's OpenStreetMap data and builds the offline routing tiles. "
         "Large download; Wi-Fi recommended."));
  connect(mapDownloadBtn, &ButtonControl::clicked, [=]() {
    params.put("EOPMapDownloadRequest", params.get("EOPMapRegion"));
    params.put("EOPMapStatus", "requested");
    updateMapStatus();
  });
  addItem(mapDownloadBtn);

  addItem(new ParamControl("EOPAutoTileEnabled", tr("Keep Maps Updated"),
      tr("Refresh the region's map data automatically when it is out of date."), "", this));
  addItem(new ParamControl("EOPAutoTileWifiOnly", tr("Map Updates on Wi-Fi Only"), "", "", this));

  auto watch = new ParamWatcher(this);
  watch->addParam("EOPMapStatus");
  connect(watch, &ParamWatcher::paramChanged, [=](const QString &, const QString &) { updateMapStatus(); });
  updateMapStatus();
}

void DevicePanel::updateMapStatus() {
  static const QMap<QString, QString> names = {
    {"thailand", tr("Thailand")}, {"malaysia-singapore", tr("Malaysia / Singapore")},
    {"japan", tr("Japan")}, {"south-korea", tr("South Korea")}, {"taiwan", tr("Taiwan")},
  };
  QString region = QString::fromStdString(params.get("EOPMapRegion"));
  if (region.isEmpty()) {
    region = "thailand";
    params.put("EOPMapRegion", region.toStdString());
  }
  mapRegionBtn->setValue(names.value(region, region));
  const QString status = QString::fromStdString(params.get("EOPMapStatus"));
  mapDownloadBtn->setValue(status.isEmpty() ? tr("not downloaded") : status);
}

void DevicePanel::updateCalibDescription() {
  QString desc = tr("ExoPilot requires the device to be mounted within 4° left or right and within 5° up or 9° down.");
  std::string calib_bytes = params.get("CalibrationParams");
  if (!calib_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(calib_bytes.data(), calib_bytes.size()));
      auto calib = cmsg.getRoot<cereal::Event>().getLiveCalibration();
      if (calib.getCalStatus() != cereal::LiveCalibrationData::Status::UNCALIBRATED) {
        double pitch = calib.getRpyCalib()[1] * (180 / M_PI);
        double yaw = calib.getRpyCalib()[2] * (180 / M_PI);
        desc += tr(" Your device is pointed %1° %2 and %3° %4.")
                    .arg(QString::number(std::abs(pitch), 'g', 1), pitch > 0 ? tr("down") : tr("up"),
                         QString::number(std::abs(yaw), 'g', 1), yaw > 0 ? tr("left") : tr("right"));
      }
    } catch (kj::Exception) {
      qInfo() << "invalid CalibrationParams";
    }
  }

  int lag_perc = 0;
  std::string lag_bytes = params.get("LiveDelay");
  if (!lag_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(lag_bytes.data(), lag_bytes.size()));
      lag_perc = cmsg.getRoot<cereal::Event>().getLiveDelay().getCalPerc();
    } catch (kj::Exception) {
      qInfo() << "invalid LiveDelay";
    }
  }
  if (lag_perc < 100) {
    desc += tr("\n\nSteering lag calibration is %1% complete.").arg(lag_perc);
  } else {
    desc += tr("\n\nSteering lag calibration is complete.");
  }

  std::string torque_bytes = params.get("LiveTorqueParameters");
  if (!torque_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(torque_bytes.data(), torque_bytes.size()));
      auto torque = cmsg.getRoot<cereal::Event>().getLiveTorqueParameters();
      // don't add for non-torque cars
      if (torque.getUseParams()) {
        int torque_perc = torque.getCalPerc();
        if (torque_perc < 100) {
          desc += tr(" Steering torque response calibration is %1% complete.").arg(torque_perc);
        } else {
          desc += tr(" Steering torque response calibration is complete.");
        }
      }
    } catch (kj::Exception) {
      qInfo() << "invalid LiveTorqueParameters";
    }
  }

  desc += "\n\n";
  desc += tr("Calibration resets rarely needed. Will restart ExoPilot if car is on.");
  resetCalibBtn->setDescription(desc);
}

void DevicePanel::reboot() {
  if (!uiState()->engaged()) {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to reboot?"), tr("Reboot"), this)) {
      // Check engaged again in case it changed while the dialog was open
      if (!uiState()->engaged()) {
        params.putBool("DoReboot", true);
      }
    }
  } else {
    ConfirmationDialog::alert(tr("Disengage to Reboot"), this);
  }
}

void DevicePanel::poweroff() {
  if (!uiState()->engaged()) {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to power off?"), tr("Power Off"), this)) {
      // Check engaged again in case it changed while the dialog was open
      if (!uiState()->engaged()) {
        params.putBool("DoShutdown", true);
      }
    }
  } else {
    ConfirmationDialog::alert(tr("Disengage to Power Off"), this);
  }
}

void SettingsWindow::showEvent(QShowEvent *event) {
  setCurrentPanel(0);
}

void SettingsWindow::setCurrentPanel(int index, const QString &param) {
  if (!param.isEmpty()) {
    // Check if param ends with "Panel" to determine if it's a panel name
    if (param.endsWith("Panel")) {
      QString panelName = param;
      panelName.chop(5); // Remove "Panel" suffix

      // Find the panel by name
      for (int i = 0; i < nav_btns->buttons().size(); i++) {
        if (nav_btns->buttons()[i]->text() == tr(panelName.toStdString().c_str())) {
          index = i;
          break;
        }
      }
    } else {
      // A toggle param name (not a "*Panel" name) always means "go to the
      // Toggles panel and scroll to this entry" -- resolve the index by
      // name instead of trusting the caller's raw `index`, so inserting or
      // reordering a panel here (e.g. Bluetooth) can't silently misroute an
      // openSettings(<stale index>, "SomeToggle") caller to the wrong panel.
      for (int i = 0; i < nav_btns->buttons().size(); i++) {
        if (nav_btns->buttons()[i]->text() == tr("Driving")) {
          index = i;
          break;
        }
      }
      emit expandToggleDescription(param);
      emit scrollToToggle(param);
    }
  }

  panel_widget->setCurrentIndex(index);
  nav_btns->buttons()[index]->setChecked(true);
}

void SettingsWindow::unlockAdvanced() {
  if (!advanced_btn) return;
  advanced_btn->setVisible(true);  // until the UI restarts
  advanced_btn->click();
}

SettingsWindow::SettingsWindow(QWidget *parent) : QFrame(parent) {

  // setup two main layouts
  sidebar_widget = new QWidget;
  QVBoxLayout *sidebar_layout = new QVBoxLayout(sidebar_widget);
  panel_widget = new QStackedWidget();

  // close button
  QPushButton *close_btn = new QPushButton(tr("×"));
  close_btn->setStyleSheet(R"(
    QPushButton {
      font-size: 32px;
      padding-bottom: 4px;
      border-radius: 35px;
      background-color: #292929;
      font-weight: 400;
    }
    QPushButton:pressed {
      background-color: #3B3B3B;
    }
  )");
  close_btn->setFixedSize(70, 70);
  sidebar_layout->addSpacing(25);
  sidebar_layout->addWidget(close_btn, 0, Qt::AlignCenter);
  QObject::connect(close_btn, &QPushButton::clicked, this, &SettingsWindow::closeSettings);

  // setup panels
  DevicePanel *device = new DevicePanel(this);
  QObject::connect(device, &DevicePanel::reviewTrainingGuide, this, &SettingsWindow::reviewTrainingGuide);
  QObject::connect(device, &DevicePanel::advancedUnlocked, this, &SettingsWindow::unlockAdvanced);

  TogglesPanel *toggles = new TogglesPanel(this);
  QObject::connect(this, &SettingsWindow::expandToggleDescription, toggles, &TogglesPanel::expandToggleDescription);
  QObject::connect(this, &SettingsWindow::scrollToToggle, toggles, &TogglesPanel::scrollToToggle);

  auto networking = new Networking(this);

  auto bt_manager = new BluetoothManager(this);
  auto bluetooth_ui = new BluetoothUI(this, bt_manager);

  QList<QPair<QString, QWidget *>> panels = {
    {tr("Device"), device},
    {tr("WiFi"), networking},
    {tr("Bluetooth"), bluetooth_ui},
    {tr("Driving"), toggles},
    {tr("Safety"), new EopPanel(this, EopPanel::SAFETY)},
    {tr("Display"), new EopPanel(this, EopPanel::DISPLAY)},
    // Engineering settings; shown after 8 quick taps on Device -> Dongle ID.
    {tr("Advanced"), new EopPanel(this, EopPanel::ADVANCED)},
  };

  nav_btns = new QButtonGroup(this);
  for (auto &[name, panel] : panels) {
    QPushButton *btn = new QPushButton(name);
    btn->setCheckable(true);
    btn->setChecked(nav_btns->buttons().size() == 0);
    btn->setStyleSheet(R"(
      QPushButton {
        color: grey;
        border: none;
        background: none;
        font-size: 22px;
        font-weight: 500;
      }
      QPushButton:checked {
        color: white;
      }
      QPushButton:pressed {
        color: #ADADAD;
      }
    )");
    btn->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Expanding);
    nav_btns->addButton(btn);
    sidebar_layout->addWidget(btn, 0, Qt::AlignRight);
    if (name == tr("Advanced")) {
      advanced_btn = btn;
      btn->setVisible(false);
    }

    const int lr_margin = (name == tr("WiFi") || name == tr("Bluetooth")) ? 0 : 50;
    panel->setContentsMargins(lr_margin, 25, lr_margin, 25);

    ScrollView *panel_frame = new ScrollView(panel, this);
    panel_widget->addWidget(panel_frame);

    QObject::connect(btn, &QPushButton::clicked, [=, w = panel_frame]() {
      btn->setChecked(true);
      panel_widget->setCurrentWidget(w);
    });
  }
  sidebar_layout->setContentsMargins(25, 25, 50, 25);

  // main settings layout, sidebar + main panel
  QHBoxLayout *main_layout = new QHBoxLayout(this);

  sidebar_widget->setFixedWidth(210);
  main_layout->addWidget(sidebar_widget);
  main_layout->addWidget(panel_widget);

  setStyleSheet(R"(
    * {
      color: white;
      font-size: 24px;
    }
    SettingsWindow {
      background-color: black;
    }
    QStackedWidget, ScrollView {
      background-color: #292929;
      border-radius: 18px;
    }
  )");
}
