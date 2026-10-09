#include "selfdrive/ui/qt/widgets/bluetooth.h"

#include <QHBoxLayout>
#include <QPainter>
#include <QPixmap>
#include <QTimer>

#include "selfdrive/ui/qt/util.h"

// BluetoothDeviceButton implementation

BluetoothDeviceButton::BluetoothDeviceButton(const BluetoothDevice &device, QWidget *parent)
    : QPushButton(parent), device_(device) {
  setFlat(true);
  setFixedHeight(45);
  updateText();

  QObject::connect(this, &QPushButton::clicked, [=]() {
    emit deviceClicked(device_);
  });
}

void BluetoothDeviceButton::updateDevice(const BluetoothDevice &device) {
  device_ = device;
  updateText();
}

QString BluetoothDeviceButton::getDeviceIcon() const {
  // Return emoji icons based on ADAS device type
  switch (device_.deviceType) {
    case DeviceType::PHONE:
      return "📱";
    case DeviceType::COMPUTER:
      return "💻";
    case DeviceType::OBD_ADAPTER:
      return "🔧";
    case DeviceType::NAVIGATION:
      return "🗺️";
    default:
      return "📡";
  }
}

QString BluetoothDeviceButton::getDeviceTypeLabel() const {
  switch (device_.deviceType) {
    case DeviceType::PHONE:
      return "Phone (Voice Control + Data Apps)";
    case DeviceType::COMPUTER:
      return "Computer (Data Sharing)";
    case DeviceType::OBD_ADAPTER:
      return "OBD-II Adapter";
    case DeviceType::NAVIGATION:
      return "Navigation Device (HERE Map ADAS)";
    default:
      return "Bluetooth Device";
  }
}

QString BluetoothDeviceButton::getSignalStrengthIcon() const {
  // RSSI typically ranges from -100 (weak) to -30 (strong)
  if (device_.rssi > -50) return "📶";      // Excellent
  if (device_.rssi > -70) return "📶";      // Good
  if (device_.rssi > -85) return "📶";      // Fair
  return "📶";                               // Weak
}

void BluetoothDeviceButton::updateText() {
  QString status_text;

  if (device_.connected) {
    status_text = "<font color='#00ff00'>● Connected</font>";
  } else if (device_.paired) {
    status_text = "<font color='#888888'>Paired</font>";
  } else {
    status_text = "<font color='#666666'>Not Paired</font>";
  }

  // Build display text
  QString display_text = QString(
    "<div style='text-align: left;'>"
    "<span style='font-size:40px;'>%1 <b>%2</b></span><br>"
    "<span style='font-size:28px; color:#888;'>%3 | %4 | RSSI: %5 dBm</span><br>"
    "<span style='font-size:28px;'>%6</span>"
    "</div>"
  ).arg(getDeviceIcon(),
        device_.name,
        getDeviceTypeLabel(),
        getSignalStrengthIcon(),
        QString::number(device_.rssi),
        status_text);

  setText(display_text);
  setStyleSheet(QString(
    "QPushButton {"
    "  background-color: %1;"
    "  border-radius: 10px;"
    "  padding: 15px;"
    "  text-align: left;"
    "}"
    "QPushButton:pressed {"
    "  background-color: #4a4a4a;"
    "}"
  ).arg(device_.connected ? "#2a5a2a" : (device_.paired ? "#3a3a5a" : "#393939")));
}

// BluetoothUI implementation

BluetoothUI::BluetoothUI(QWidget *parent, BluetoothManager *manager)
    : QWidget(parent), bt_manager(manager) {
  main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(0, 0, 0, 0);
  main_layout->setSpacing(10);

  device_buttons = new QButtonGroup(this);
  device_buttons->setExclusive(false);

  // Status labels
  status_label = new QLabel(this);
  status_label->setStyleSheet("font-size: 40px; padding: 15px; color: #dddddd;");
  main_layout->addWidget(status_label);

  scanning_label = new QLabel(this);
  scanning_label->setStyleSheet("font-size: 32px; padding: 10px; color: #888;");
  scanning_label->setText("Scanning for devices...");
  scanning_label->setVisible(false);
  main_layout->addWidget(scanning_label);

  // Connect signals
  QObject::connect(bt_manager, &BluetoothManager::devicesChanged, this, &BluetoothUI::refresh);
  QObject::connect(bt_manager, &BluetoothManager::scanningChanged, this, [this](bool scanning) {
    scanning_label->setVisible(scanning);
    if (scanning) {
      scanning_label->setText("🔍 Scanning for devices...");
    }
  });

  QObject::connect(bt_manager, &BluetoothManager::pairingSuccess, this, [this](const QString &address) {
    status_label->setText(QString("✅ Successfully paired with %1").arg(address));
    QTimer::singleShot(3000, this, [this]() { status_label->clear(); });
  });

  QObject::connect(bt_manager, &BluetoothManager::pairingFailed, this, [this](const QString &address, const QString &error) {
    status_label->setText(QString("❌ Failed to pair with %1: %2").arg(address, error));
    QTimer::singleShot(5000, this, [this]() { status_label->clear(); });
  });

  refresh();
}

void BluetoothUI::refresh() {
  clearDeviceList();

  if (!bt_manager->isBluetoothAvailable()) {
    QLabel *no_bt = new QLabel("Bluetooth adapter not found", this);
    no_bt->setStyleSheet("font-size: 36px; color: #ff0000; padding: 30px;");
    no_bt->setAlignment(Qt::AlignCenter);
    main_layout->addWidget(no_bt);
    return;
  }

  if (!bt_manager->isBluetoothPowered()) {
    QLabel *bt_off = new QLabel("Bluetooth is powered off", this);
    bt_off->setStyleSheet("font-size: 36px; color: #ff8800; padding: 30px;");
    bt_off->setAlignment(Qt::AlignCenter);
    main_layout->addWidget(bt_off);
    return;
  }

  showDeviceCategories();
}

void BluetoothUI::showDeviceCategories() {
  // Group devices by ADAS function
  QList<BluetoothDevice> obdAdapters = bt_manager->getOBDAdapters();
  QList<BluetoothDevice> navDevices = bt_manager->getNavigationDevices();
  QList<BluetoothDevice> phones = bt_manager->getPhones();
  QList<BluetoothDevice> otherDevices;

  // Collect other ADAS-compatible devices
  for (const auto &device : bt_manager->seenDevices) {
    if (device.deviceType != DeviceType::PHONE &&
        device.deviceType != DeviceType::OBD_ADAPTER &&
        device.deviceType != DeviceType::NAVIGATION) {
      otherDevices.append(device);
    }
  }

  // Sort other devices by signal strength
  std::sort(otherDevices.begin(), otherDevices.end(), compare_by_rssi);

  // Show ADAS-focused categories
  if (!obdAdapters.isEmpty()) {
    addDeviceCategory("🔧 OBD-II Diagnostic Adapters", obdAdapters);
  }

  if (!navDevices.isEmpty()) {
    addDeviceCategory("🗺️ Navigation Devices (HERE Map ADAS)", navDevices);
  }

  if (!phones.isEmpty()) {
    addDeviceCategory("📱 Mobile Devices (Voice Control & Data Apps)", phones);
  }

  if (!otherDevices.isEmpty()) {
    addDeviceCategory("📡 Other Compatible Devices", otherDevices);
  }

  if (bt_manager->seenDevices.isEmpty()) {
    QLabel *no_devices = new QLabel("No Bluetooth devices found\n\nMake sure devices are in pairing mode\n\nSupported: Mobile phones, OBD adapters, HERE map devices", this);
    no_devices->setStyleSheet("font-size: 36px; color: #888; padding: 50px;");
    no_devices->setAlignment(Qt::AlignCenter);
    main_layout->addWidget(no_devices);
  }
}

void BluetoothUI::addDeviceCategory(const QString &title, const QList<BluetoothDevice> &devices) {
  // Category title
  QLabel *category_label = new QLabel(title, this);
  category_label->setStyleSheet("font-size: 42px; font-weight: bold; padding: 20px 10px 10px 10px; color: #fff;");
  main_layout->addWidget(category_label);

  // Add devices in this category
  for (const auto &device : devices) {
    BluetoothDeviceButton *btn = new BluetoothDeviceButton(device, this);
    device_buttons->addButton(btn);
    main_layout->addWidget(btn);

    QObject::connect(btn, &BluetoothDeviceButton::deviceClicked, [this](const BluetoothDevice &device) {
      if (device.paired && device.connected) {
        // Already connected - disconnect
        bt_manager->disconnectDevice(device.address);
      } else if (device.paired) {
        // Paired but not connected - connect
        bt_manager->connectDevice(device.address);
      } else {
        // Not paired - start pairing
        status_label->setText(QString("Pairing with %1...").arg(device.name));
        bt_manager->pairDevice(device.address);
      }
    });
  }
}

void BluetoothUI::clearDeviceList() {
  // Remove all device buttons
  for (auto *button : device_buttons->buttons()) {
    device_buttons->removeButton(button);
    main_layout->removeWidget(button);
    delete button;
  }

  // Remove all widgets except status and scanning labels
  while (main_layout->count() > 2) {
    QLayoutItem *item = main_layout->takeAt(2);
    if (item->widget()) {
      delete item->widget();
    }
    delete item;
  }
}
