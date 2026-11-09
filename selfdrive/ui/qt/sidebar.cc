#include "selfdrive/ui/qt/sidebar.h"

#include <QMouseEvent>
#include <QNetworkInterface>

#include "common/params.h"
#include "selfdrive/ui/qt/util.h"

void Sidebar::drawButton(QPainter &p, const QRect &r, const QPixmap &icon, const QString &text, bool pressed) {
  // Same family as the status cards (drawMetric): 14 px radius, soft white
  // outline, a blue tab on the left; filled a step lighter so it reads as a
  // button, darker while pressed.
  p.setPen(Qt::NoPen);
  p.setBrush(pressed ? QColor(0x2a, 0x2a, 0x2a) : QColor(0x4a, 0x4a, 0x4a));
  p.drawRoundedRect(r, 14, 14);

  p.setBrush(theme::GOOD);
  p.setClipRect(r.x() + 4, r.y(), 18, r.height(), Qt::ReplaceClip);
  p.drawRoundedRect(QRect(r.x() + 3, r.y() + 3, 70, r.height() - 6), 12, 12);
  p.setClipping(false);

  QPen pen(QColor(0xff, 0xff, 0xff, 0x55));
  pen.setWidth(2);
  p.setPen(pen);
  p.setBrush(Qt::NoBrush);
  p.drawRoundedRect(r, 14, 14);

  p.setFont(theme::inter(24, QFont::DemiBold));
  const int text_w = p.fontMetrics().horizontalAdvance(text);
  const int gap = 14;
  const QRect content = r.adjusted(22, 0, 0, 0);  // right of the tab
  int x = content.x() + (content.width() - icon.width() - gap - text_w) / 2;
  p.drawPixmap(x, r.y() + (r.height() - icon.height()) / 2, icon);
  p.setPen(theme::WHITE);
  p.drawText(QRect(x + icon.width() + gap, r.y(), text_w + 2, r.height()), Qt::AlignLeft | Qt::AlignVCenter, text);
}

void Sidebar::drawMetric(QPainter &p, const QPair<QString, QString> &label, QColor c, int y) {
  const QRect rect = {15, y, 230, 85};

  p.setPen(Qt::NoPen);
  p.setBrush(QBrush(c));
  p.setClipRect(rect.x() + 4, rect.y(), 18, rect.height(), Qt::ClipOperation::ReplaceClip);
  p.drawRoundedRect(QRect(rect.x() + 3, rect.y() + 3, 70, 80), 12, 12);
  p.setClipping(false);

  QPen pen = QPen(QColor(0xff, 0xff, 0xff, 0x55));
  pen.setWidth(2);
  p.setPen(pen);
  p.setBrush(Qt::NoBrush);
  p.drawRoundedRect(rect, 14, 14);

  p.setPen(QColor(0xff, 0xff, 0xff));
  p.setFont(InterFont(22, QFont::DemiBold));
  p.drawText(rect.adjusted(22, 0, 0, 0), Qt::AlignCenter, label.first + "\n" + label.second);
}

Sidebar::Sidebar(QWidget *parent) : QFrame(parent), onroad(false), flag_pressed(false), settings_pressed(false), mic_indicator_pressed(false) {
  // Dark buttons with white icons (PyQt5 design), not comma's white button
  // images; the offroad home button did nothing, so offroad it is not drawn.
  gear_img = loadPixmap("../assets/icons/settings.png", QSize(32, 32));
  flag_img = loadPixmap("../assets/images/button_flag.png", QSize(34, 34));
  mic_img = loadPixmap("../assets/icons/microphone.png", QSize(22, 22));
  link_img = loadPixmap("../assets/icons/link.png", QSize(36, 36));

  connect(this, &Sidebar::valueChanged, [=] { update(); });

  setAttribute(Qt::WA_OpaquePaintEvent);
  setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Expanding);
  setFixedWidth(theme::SIDEBAR_W);

  QObject::connect(uiState(), &UIState::uiUpdate, this, &Sidebar::updateState);

  pm = std::make_unique<PubMaster>(std::vector<const char*>{"bookmarkButton"});

  // Keys are bluetoothd's (pairing_agent.py writes EOPBluetoothPairing*); the
  // unprefixed names this used to watch do not exist, so PAIRING never showed.
  // Cache BLE pairing status via QFileSystemWatcher instead of a
  // synchronous Params().get() on every updateState() tick (UI_FREQ =
  // 20 Hz) -- same pattern as eop_panel.cc's fs_watch.
  ble_watch = new ParamWatcher(this);
  for (const char *p : {"BluetoothPairingPin", "BluetoothPairingActive", "SPPPairedDevice", "NavPilotPaired"}) {
    ble_watch->addParam(p);
  }
  QObject::connect(ble_watch, &ParamWatcher::paramChanged, [=](const QString &, const QString &) {
    refreshBleCache();
  });
  refreshBleCache();
}

void Sidebar::refreshBleCache() {
  auto params = Params();
  cached_pairing_pin = params.get("BluetoothPairingPin");
  cached_pairing_active = params.get("BluetoothPairingActive") == "1";
  cached_spp_connected = params.get("SPPPairedDevice").length() > 0;
  cached_gatt_connected = params.getBool("NavPilotPaired");
}

void Sidebar::mousePressEvent(QMouseEvent *event) {
  if (onroad && home_btn.contains(event->pos())) {
    flag_pressed = true;
    update();
  } else if (settings_btn.contains(event->pos())) {
    settings_pressed = true;
    update();
  } else if (recording_audio && mic_indicator_btn.contains(event->pos())) {
    mic_indicator_pressed = true;
    update();
  }
}

void Sidebar::mouseReleaseEvent(QMouseEvent *event) {
  if (flag_pressed || settings_pressed || mic_indicator_pressed) {
    flag_pressed = settings_pressed = mic_indicator_pressed = false;
    update();
  }
  if (onroad && home_btn.contains(event->pos())) {
    MessageBuilder msg;
    msg.initEvent().initBookmarkButton();
    pm->send("bookmarkButton", msg);
  } else if (settings_btn.contains(event->pos())) {
    emit openSettings();
  } else if (recording_audio && mic_indicator_btn.contains(event->pos())) {
    emit openSettings(2, "RecordAudio");
  }
}

void Sidebar::offroadTransition(bool offroad) {
  onroad = !offroad;
  update();
}

void Sidebar::updateState(const UIState &s) {
  if (!isVisible()) return;

  auto &sm = *(s.sm);

  networking = networking ? networking : window()->findChild<Networking *>("");
  bool tethering_on = networking && networking->wifi->tethering_on;
  auto deviceState = sm["deviceState"].getDeviceState();
  setProperty("netType", tethering_on ? "Hotspot": network_type[deviceState.getNetworkType()]);
  int strength = tethering_on ? 4 : (int)deviceState.getNetworkStrength();
  setProperty("netStrength", strength > 0 ? strength + 1 : 0);

  ItemStatus wifiStatus;
  auto netType = deviceState.getNetworkType();
  if (netType == cereal::DeviceState::NetworkType::WIFI) {
    int sig = (int)deviceState.getNetworkStrength();
    if (sig >= 3) {
      wifiStatus = {{tr("WIFI"), tr("GOOD")}, good_color};
    } else if (sig >= 1) {
      wifiStatus = {{tr("WIFI"), tr("WEAK")}, warning_color};
    } else {
      wifiStatus = {{tr("WIFI"), tr("LOW")}, warning_color};
    }
  } else if (netType == cereal::DeviceState::NetworkType::ETHERNET) {
    wifiStatus = {{tr("ETH"), tr("OK")}, good_color};
  } else if (netType != cereal::DeviceState::NetworkType::NONE) {
    wifiStatus = {{network_type[netType], tr("ON")}, good_color};
  } else {
    // thermald not running (dev PC) — fall back to Qt network detection
    bool wifi_up = false, eth_up = false;
    for (auto &iface : QNetworkInterface::allInterfaces()) {
      auto flags = iface.flags();
      if (!flags.testFlag(QNetworkInterface::IsUp) || !flags.testFlag(QNetworkInterface::IsRunning)) continue;
      if (flags.testFlag(QNetworkInterface::IsLoopBack)) continue;
      const QString name = iface.name();
      if (name.startsWith("wl")) wifi_up = true;
      else if (name.startsWith("eth") || name.startsWith("en")) eth_up = true;
    }
    if (wifi_up) {
      wifiStatus = {{tr("WIFI"), tr("ON")}, good_color};
    } else if (eth_up) {
      wifiStatus = {{tr("ETH"), tr("OK")}, good_color};
    } else {
      wifiStatus = {{tr("WIFI"), tr("OFF")}, theme::INACTIVE};
    }
  }
  setProperty("wifiStatus", QVariant::fromValue(wifiStatus));

  ItemStatus tempStatus = {{tr("TEMP"), tr("HIGH")}, danger_color};
  auto ts = deviceState.getThermalStatus();
  if (ts == cereal::DeviceState::ThermalStatus::GREEN) {
    tempStatus = {{tr("TEMP"), tr("GOOD")}, good_color};
  } else if (ts == cereal::DeviceState::ThermalStatus::YELLOW) {
    tempStatus = {{tr("TEMP"), tr("OK")}, warning_color};
  }
  setProperty("tempStatus", QVariant::fromValue(tempStatus));

  ItemStatus pandaStatus = {{tr("PANDA"), tr("ON")}, good_color};
  if (s.scene.pandaType == cereal::PandaState::PandaType::UNKNOWN) {
    pandaStatus = {{tr("PANDA"), tr("OFF")}, danger_color};
  }
  setProperty("pandaStatus", QVariant::fromValue(pandaStatus));

  // NavPilot / Bluetooth pairing status -- read from ble_watch's cache
  // (refreshed only on actual param-file change), not a fresh Params()
  // disk read every tick. GATT (BLE) is checked alongside SPP since
  // NavPilot's primary, exclusive companion-app transport per
  // docs/eop/04_Integration/BLE_DESIGN.md is BLE GATT, not classic SPP
  // (that's reserved for legacy OBD scanners) -- without it the sidebar
  // showed "BLE OFF" for the entire time a phone was connected over its
  // actual primary transport.
  ItemStatus bleStatus;
  if (cached_pairing_active && !cached_pairing_pin.empty()) {
    bleStatus = {{tr("BLE"), tr("PAIRING")}, warning_color};
  } else if (cached_gatt_connected || cached_spp_connected) {
    bleStatus = {{tr("BLE"), tr("ON")}, good_color};
  } else {
    bleStatus = {{tr("BLE"), tr("OFF")}, theme::INACTIVE};
  }
  setProperty("bleStatus", QVariant::fromValue(bleStatus));

  setProperty("recordingAudio", s.scene.recording_audio);
}

void Sidebar::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setPen(Qt::NoPen);
  p.setRenderHint(QPainter::Antialiasing);

  p.fillRect(rect(), theme::SIDEBAR_BG);

  // buttons
  drawButton(p, settings_btn, gear_img, tr("Settings"), settings_pressed);
  if (onroad) {
    drawButton(p, home_btn, flag_img, tr("Bookmark"), flag_pressed);
  }
  p.setPen(Qt::NoPen);
  if (recording_audio) {
    p.setBrush(danger_color);
    p.setOpacity(mic_indicator_pressed ? 0.65 : 1.0);
    p.drawRoundedRect(mic_indicator_btn, mic_indicator_btn.height() / 2, mic_indicator_btn.height() / 2);
    int icon_x = mic_indicator_btn.x() + (mic_indicator_btn.width() - mic_img.width()) / 2;
    int icon_y = mic_indicator_btn.y() + (mic_indicator_btn.height() - mic_img.height()) / 2;
    p.drawPixmap(icon_x, icon_y, mic_img);
  }
  p.setOpacity(1.0);

  // network
  int x = 35;
  const QColor gray = theme::INACTIVE;
  for (int i = 0; i < 5; ++i) {
    p.setBrush(i < net_strength ? Qt::white : gray);
    p.drawEllipse(x, 115, 16, 16);
    x += 22;
  }

  p.setFont(InterFont(20));
  p.setPen(QColor(0xff, 0xff, 0xff));
  const QRect r = QRect(35, 150, width() - 60, 30);

  if (net_type == "Hotspot") {
    p.drawPixmap(r.x(), r.y() + (r.height() - link_img.height()) / 2, link_img);
  } else {
    p.drawText(r, Qt::AlignLeft | Qt::AlignVCenter, net_type);
  }

  // metrics: hardware-level only — TEMP / PANDA / WIFI / BLE
  drawMetric(p, temp_status.first, temp_status.second, 180);
  drawMetric(p, panda_status.first, panda_status.second, 265);
  drawMetric(p, wifi_status.first, wifi_status.second, 350);
  drawMetric(p, ble_status.first, ble_status.second, 435);
}
