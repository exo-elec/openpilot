#include "selfdrive/ui/qt/network/bluetooth_manager.h"

#include <QDBusConnection>
#include <QDBusInterface>
#include <QDBusReply>
#include <QDebug>

#include "common/swaglog.h"

// Bluetooth Service UUIDs (ADAS-focused)
#define SPP_UUID "00001101-0000-1000-8000-00805f9b34fb"          // Serial Port Profile (ELM327, NavPilot)
#define PAN_UUID "00001115-0000-1000-8000-00805f9b34fb"          // Personal Area Networking (data sharing)
#define OBEX_UUID "00001105-0000-1000-8000-00805f9b34fb"         // OBEX Object Push (file transfer)

bool compare_by_rssi(const BluetoothDevice &a, const BluetoothDevice &b) {
  // Sort by: paired first, then connected, then signal strength, then name
  return std::tuple(a.paired, a.connected, a.rssi, b.name) >
         std::tuple(b.paired, b.connected, b.rssi, a.name);
}

BluetoothManager::BluetoothManager(QObject *parent) : QObject(parent) {
  adapter = getAdapter();
  if (!adapter.isEmpty()) {
    setup();
  } else {
    LOGW("No Bluetooth adapter found");
  }

  scanTimer.setInterval(30000);  // 30 second scan cycles
  scanTimer.setSingleShot(false);
  connect(&scanTimer, &QTimer::timeout, this, &BluetoothManager::scanTimeout);
}

QString BluetoothManager::getAdapter() {
  // GetManagedObjects returns a{oa{sa{sv}}} — object-path keys, not strings.
  // QVariantMap requires string keys so QDBusReply<QVariantMap> always fails.
  // Probe standard BlueZ adapter paths directly via Properties.Get instead.
  for (int i = 0; i < 4; i++) {
    QString path = QString("/org/bluez/hci%1").arg(i);
    QDBusInterface iface(BLUEZ_SERVICE, path, DBUS_PROPERTIES_INTERFACE,
                         QDBusConnection::systemBus());
    QDBusReply<QVariant> reply = iface.call("Get", BLUEZ_ADAPTER_INTERFACE, "Address");
    if (reply.isValid()) {
      qDebug() << "Found Bluetooth adapter:" << path << reply.value().toString();
      return path;
    }
  }
  LOGW("No Bluetooth adapter found on system bus");
  return QString();
}

void BluetoothManager::setup() {
  auto bus = QDBusConnection::systemBus();

  // Listen for device additions/removals
  bus.connect(BLUEZ_SERVICE, "/", DBUS_OBJECT_MANAGER_INTERFACE, "InterfacesAdded",
              this, SLOT(onInterfacesAdded(QDBusObjectPath, QVariantMap)));
  bus.connect(BLUEZ_SERVICE, "/", DBUS_OBJECT_MANAGER_INTERFACE, "InterfacesRemoved",
              this, SLOT(onInterfacesRemoved(QDBusObjectPath, QStringList)));

  // Make sure Bluetooth is powered on
  setBluetoothPowered(true);

  // Initial device scan
  refreshDevices();
}

void BluetoothManager::start() {
  if (adapter.isEmpty()) return;

  requestScan();
  scanTimer.start();
}

void BluetoothManager::stop() {
  scanTimer.stop();

  // Stop scanning
  if (scanning && !adapter.isEmpty()) {
    QDBusInterface adapterIface(BLUEZ_SERVICE, adapter, BLUEZ_ADAPTER_INTERFACE, QDBusConnection::systemBus());
    adapterIface.call("StopDiscovery");
    scanning = false;
    emit scanningChanged(false);
  }
}

void BluetoothManager::requestScan() {
  if (adapter.isEmpty() || scanning) return;

  QDBusInterface adapterIface(BLUEZ_SERVICE, adapter, BLUEZ_ADAPTER_INTERFACE, QDBusConnection::systemBus());
  QDBusReply<void> reply = adapterIface.call("StartDiscovery");

  if (reply.isValid()) {
    scanning = true;
    emit scanningChanged(true);
    qDebug() << "Bluetooth scan started";
  } else {
    qWarning() << "Failed to start Bluetooth scan:" << reply.error().message();
  }
}

void BluetoothManager::scanTimeout() {
  // Refresh device list periodically
  refreshDevices();
}

void BluetoothManager::refreshDevices() {
  if (adapter.isEmpty()) return;

  // Use raw QDBusMessage — GetManagedObjects returns a{oa{sa{sv}}} which Qt
  // cannot unmarshal into QVariantMap (object-path keys ≠ string keys).
  QDBusMessage msg = QDBusMessage::createMethodCall(
      BLUEZ_SERVICE, "/", DBUS_OBJECT_MANAGER_INTERFACE, "GetManagedObjects");
  QDBusMessage reply = QDBusConnection::systemBus().call(msg);

  if (reply.type() != QDBusMessage::ReplyMessage || reply.arguments().isEmpty()) {
    qWarning() << "GetManagedObjects failed";
    return;
  }

  seenDevices.clear();

  // Iterate a{oa{sa{sv}}} via QDBusArgument
  const QDBusArgument arg = reply.arguments().first().value<QDBusArgument>();
  arg.beginMap();
  while (!arg.atEnd()) {
    arg.beginMapEntry();
    QDBusObjectPath objPath;
    arg >> objPath;
    const QString pathStr = objPath.path();

    // Iterate inner a{sa{sv}} — interface name → properties (a{sv})
    QVariantMap deviceProps;
    bool isDevice = false;
    arg.beginMap();
    while (!arg.atEnd()) {
      arg.beginMapEntry();
      QString ifaceName;
      arg >> ifaceName;
      QVariantMap props;
      arg >> props;   // a{sv} → QVariantMap is a built-in Qt D-Bus conversion
      if (ifaceName == BLUEZ_DEVICE_INTERFACE) {
        deviceProps = props;
        isDevice = true;
      }
      arg.endMapEntry();
    }
    arg.endMap();
    arg.endMapEntry();

    if (isDevice && pathStr.startsWith(adapter + "/")) {
      BluetoothDevice device = parseDevice(pathStr, deviceProps);
      seenDevices[device.address] = device;
    }
  }
  arg.endMap();

  emit devicesChanged();
}

BluetoothDevice BluetoothManager::parseDevice(const QString &devicePath, const QVariantMap &properties) {
  BluetoothDevice device;
  device.path = devicePath;
  device.address = properties.value("Address").toString();
  device.name = properties.value("Name").toString();
  device.rssi = properties.value("RSSI", -100).toInt();  // Default to weak signal
  device.paired = properties.value("Paired", false).toBool();
  device.connected = properties.value("Connected", false).toBool();
  device.trusted = properties.value("Trusted", false).toBool();
  device.icon = properties.value("Icon", "computer").toString();

  // Parse UUIDs
  QDBusArgument uuidsArg = properties.value("UUIDs").value<QDBusArgument>();
  uuidsArg.beginArray();
  while (!uuidsArg.atEnd()) {
    QString uuid;
    uuidsArg >> uuid;
    device.uuids.append(uuid.toLower());
  }
  uuidsArg.endArray();

  // Use alias if name is empty
  if (device.name.isEmpty()) {
    device.name = properties.value("Alias").toString();
  }

  // Fallback to address if still no name
  if (device.name.isEmpty()) {
    device.name = device.address;
  }

  // Classify device type and parse service profiles
  device.deviceType = classifyDevice(device.icon, device.uuids, device.name);
  device.serviceProfiles = parseServiceProfiles(device.uuids);

  return device;
}

DeviceType BluetoothManager::classifyDevice(const QString &icon, const QStringList &uuids, const QString &name) const {
  // First check by device name (most reliable for specific ADAS devices)
  if (isOBDDeviceName(name)) {
    return DeviceType::OBD_ADAPTER;
  }

  if (isNavigationDeviceName(name)) {
    return DeviceType::NAVIGATION;
  }

  // Then check by BlueZ icon
  if (icon == "phone") {
    return DeviceType::PHONE;
  } else if (icon == "computer") {
    return DeviceType::COMPUTER;
  }

  // Check by service UUIDs for ADAS-specific devices
  for (const QString &uuid : uuids) {
    // SPP-only devices are typically OBD adapters or navigation devices
    if (uuid.contains(SPP_UUID)) {
      // If only SPP and no phone icon, likely OBD or navigation
      if (uuids.size() == 1) {
        return DeviceType::OBD_ADAPTER;  // Default to OBD for SPP-only devices
      }
    }

  }

  return DeviceType::UNKNOWN;
}

int BluetoothManager::parseServiceProfiles(const QStringList &uuids) const {
  int profiles = static_cast<int>(ServiceProfile::NONE);

  for (const QString &uuid : uuids) {
    if (uuid.contains(SPP_UUID)) profiles |= static_cast<int>(ServiceProfile::SPP);
    if (uuid.contains(PAN_UUID)) profiles |= static_cast<int>(ServiceProfile::PAN);
    if (uuid.contains(OBEX_UUID)) profiles |= static_cast<int>(ServiceProfile::OBEX);
  }

  return profiles;
}

bool BluetoothManager::isOBDDeviceName(const QString &name) const {
  QString lower = name.toLower();
  QStringList obdKeywords = {"elm327", "elm", "obd", "obdii", "vgate", "konnwei", "veepeak", "bafx", "carista", "bluedriver"};

  for (const QString &keyword : obdKeywords) {
    if (lower.contains(keyword)) {
      return true;
    }
  }

  return false;
}

bool BluetoothManager::isNavigationDeviceName(const QString &name) const {
  QString lower = name.toLower();
  QStringList navKeywords = {"here", "tomtom", "garmin", "navman", "gps", "navigator"};

  for (const QString &keyword : navKeywords) {
    if (lower.contains(keyword)) {
      return true;
    }
  }

  return false;
}

QList<BluetoothDevice> BluetoothManager::getDevicesByType(DeviceType type) const {
  QList<BluetoothDevice> devices;
  for (const auto &device : seenDevices) {
    if (device.deviceType == type) {
      devices.append(device);
    }
  }

  // Sort by signal strength
  std::sort(devices.begin(), devices.end(), compare_by_rssi);
  return devices;
}

QVariantMap BluetoothManager::getDeviceProperties(const QString &devicePath) {
  QDBusInterface deviceIface(BLUEZ_SERVICE, devicePath, DBUS_PROPERTIES_INTERFACE, QDBusConnection::systemBus());
  QDBusReply<QVariantMap> reply = deviceIface.call("GetAll", BLUEZ_DEVICE_INTERFACE);

  if (reply.isValid()) {
    return reply.value();
  }
  return QVariantMap();
}

void BluetoothManager::setDeviceProperty(const QString &devicePath, const QString &property, const QVariant &value) {
  QDBusInterface deviceIface(BLUEZ_SERVICE, devicePath, DBUS_PROPERTIES_INTERFACE, QDBusConnection::systemBus());
  deviceIface.call("Set", BLUEZ_DEVICE_INTERFACE, property, QVariant::fromValue(QDBusVariant(value)));
}

void BluetoothManager::pairDevice(const QString &address) {
  if (!seenDevices.contains(address)) return;

  QString devicePath = seenDevices[address].path;
  QDBusInterface deviceIface(BLUEZ_SERVICE, devicePath, BLUEZ_DEVICE_INTERFACE, QDBusConnection::systemBus());

  // Pair asynchronously (BlueZ Pair can take up to 30s with user confirmation)
  QDBusPendingCall call = deviceIface.asyncCall("Pair");
  QDBusPendingCallWatcher *watcher = new QDBusPendingCallWatcher(call, this);

  // Timeout guard — if Pair never responds, clean up and report failure
  QTimer::singleShot(35000, watcher, [this, address, watcher]() {
    if (!watcher) return;
    LOGW("Pairing timed out for %s", address.toStdString().c_str());
    emit pairingFailed(address, "Pairing timed out");
    watcher->deleteLater();
  });

  connect(watcher, &QDBusPendingCallWatcher::finished, this, [this, address](QDBusPendingCallWatcher *w) {
    QDBusPendingReply<> reply = *w;
    if (reply.isError()) {
      QString error = reply.error().message();
      qWarning() << "Pairing failed for" << address << ":" << error;
      emit pairingFailed(address, error);
    } else {
      qDebug() << "Successfully paired with" << address;
      emit pairingSuccess(address);

      // Auto-trust and connect after successful pairing
      if (seenDevices.contains(address)) {
        setDeviceProperty(seenDevices[address].path, "Trusted", true);
        connectDevice(address);
      }
    }
    w->deleteLater();
  });
}

void BluetoothManager::unpairDevice(const QString &address) {
  if (!seenDevices.contains(address)) return;

  // First disconnect if connected
  if (seenDevices[address].connected) {
    disconnectDevice(address);
  }

  // Remove the device
  removeDevice(address);
}

void BluetoothManager::connectDevice(const QString &address) {
  if (!seenDevices.contains(address)) return;

  QString devicePath = seenDevices[address].path;
  QDBusInterface deviceIface(BLUEZ_SERVICE, devicePath, BLUEZ_DEVICE_INTERFACE, QDBusConnection::systemBus());

  QDBusReply<void> reply = deviceIface.call("Connect");
  if (reply.isValid()) {
    DeviceType type = seenDevices[address].deviceType;
    qDebug() << "Connected to" << address << "type:" << static_cast<int>(type);
    emit deviceConnected(address, type);
  } else {
    qWarning() << "Failed to connect to" << address << ":" << reply.error().message();
  }
}

void BluetoothManager::disconnectDevice(const QString &address) {
  if (!seenDevices.contains(address)) return;

  QString devicePath = seenDevices[address].path;
  QDBusInterface deviceIface(BLUEZ_SERVICE, devicePath, BLUEZ_DEVICE_INTERFACE, QDBusConnection::systemBus());

  QDBusReply<void> reply = deviceIface.call("Disconnect");
  if (reply.isValid()) {
    qDebug() << "Disconnected from" << address;
    emit deviceDisconnected(address);
  } else {
    qWarning() << "Failed to disconnect from" << address << ":" << reply.error().message();
  }
}

void BluetoothManager::removeDevice(const QString &address) {
  if (!seenDevices.contains(address)) return;

  QString devicePath = seenDevices[address].path;
  QDBusInterface adapterIface(BLUEZ_SERVICE, adapter, BLUEZ_ADAPTER_INTERFACE, QDBusConnection::systemBus());

  QDBusReply<void> reply = adapterIface.call("RemoveDevice", QVariant::fromValue(QDBusObjectPath(devicePath)));
  if (reply.isValid()) {
    qDebug() << "Removed device" << address;
    seenDevices.remove(address);
    emit devicesChanged();
  } else {
    qWarning() << "Failed to remove device" << address << ":" << reply.error().message();
  }
}

bool BluetoothManager::isBluetoothAvailable() {
  return !adapter.isEmpty();
}

bool BluetoothManager::isBluetoothPowered() {
  if (adapter.isEmpty()) return false;

  QDBusInterface adapterIface(BLUEZ_SERVICE, adapter, DBUS_PROPERTIES_INTERFACE, QDBusConnection::systemBus());
  QDBusReply<QVariant> reply = adapterIface.call("Get", BLUEZ_ADAPTER_INTERFACE, "Powered");

  if (reply.isValid()) {
    return reply.value().toBool();
  }
  return false;
}

void BluetoothManager::setBluetoothPowered(bool powered) {
  if (adapter.isEmpty()) return;

  QDBusInterface adapterIface(BLUEZ_SERVICE, adapter, DBUS_PROPERTIES_INTERFACE, QDBusConnection::systemBus());
  adapterIface.call("Set", BLUEZ_ADAPTER_INTERFACE, "Powered", QVariant::fromValue(QDBusVariant(powered)));

  qDebug() << "Bluetooth powered" << (powered ? "on" : "off");
}

void BluetoothManager::onInterfacesAdded(const QDBusObjectPath &path, const QVariantMap &interfaces) {
  const QString pathStr = path.path();
  if (!pathStr.startsWith(adapter + "/")) return;
  if (!interfaces.contains(BLUEZ_DEVICE_INTERFACE)) return;

  // Inner value is a{sv} delivered as QVariant(QVariantMap) by Qt D-Bus
  QVariantMap deviceProps = interfaces[BLUEZ_DEVICE_INTERFACE].value<QVariantMap>();
  if (deviceProps.isEmpty()) {
    // Fallback: value may be raw QDBusArgument if Qt didn't auto-unmarshal
    QDBusArgument arg = interfaces[BLUEZ_DEVICE_INTERFACE].value<QDBusArgument>();
    arg >> deviceProps;
  }

  BluetoothDevice device = parseDevice(pathStr, deviceProps);
  seenDevices[device.address] = device;
  emit devicesChanged();
}

void BluetoothManager::onInterfacesRemoved(const QDBusObjectPath &path, const QStringList &interfaces) {
  QString pathStr = path.path();

  if (interfaces.contains(BLUEZ_DEVICE_INTERFACE)) {
    // Find and remove the device
    for (auto it = seenDevices.begin(); it != seenDevices.end(); ++it) {
      if (it.value().path == pathStr) {
        seenDevices.erase(it);
        emit devicesChanged();
        break;
      }
    }
  }
}

void BluetoothManager::onPropertiesChanged(const QString &interface, const QVariantMap &changed, const QStringList &invalidated) {
  if (interface == BLUEZ_ADAPTER_INTERFACE && changed.contains("Discovering")) {
    bool discovering = changed["Discovering"].toBool();
    scanning = discovering;
    emit scanningChanged(discovering);
  }
}

void BluetoothManager::onDevicePropertiesChanged(const QString &interface, const QVariantMap &changed, const QStringList &invalidated) {
  Q_UNUSED(invalidated);

  if (interface == BLUEZ_DEVICE_INTERFACE) {
    // Refresh devices when properties change
    refreshDevices();
  }
}
