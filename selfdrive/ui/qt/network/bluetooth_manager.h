#pragma once

#include <optional>
#include <QtDBus>
#include <QTimer>

// Classic Bluetooth (BR/EDR) Manager for ADAS System
// Uses high-performance Classic Bluetooth for:
// - Data sharing (SPP/PAN/OBEX)
// - OBD diagnostics (SPP)
// - NavPilot mobile app (SPP)
//
// Note: Separate BLE services for ELM327 exist in system/bluetoothd/

#define BLUEZ_SERVICE "org.bluez"
#define BLUEZ_ADAPTER_INTERFACE "org.bluez.Adapter1"
#define BLUEZ_DEVICE_INTERFACE "org.bluez.Device1"
#define DBUS_PROPERTIES_INTERFACE "org.freedesktop.DBus.Properties"
#define DBUS_OBJECT_MANAGER_INTERFACE "org.freedesktop.DBus.ObjectManager"

enum class PairingState {
  UNPAIRED,
  PAIRING,
  PAIRED
};

// Device type classification for ADAS use cases
enum class DeviceType {
  UNKNOWN,
  PHONE,          // Mobile phone (NavPilot app)
  COMPUTER,       // Laptop/Desktop (configuration, data sharing)
  OBD_ADAPTER,    // ELM327 or other OBD-II diagnostic adapter
  NAVIGATION,     // Navigation device
  OTHER           // Other Bluetooth devices
};

// Bluetooth service profiles (ADAS-focused)
enum class ServiceProfile {
  NONE = 0x0000,
  SPP = 0x0001,           // Serial Port Profile (ELM327, NavPilot, data sharing)
  PAN = 0x0004,           // Personal Area Networking (data sharing, tethering)
  OBEX = 0x0008           // Object Exchange (file transfer, data sharing)
};

struct BluetoothDevice {
  QString address;
  QString name;
  int rssi;  // Signal strength
  bool paired;
  bool connected;
  bool trusted;
  QString icon;  // BlueZ device icon
  QString path;  // D-Bus object path
  DeviceType deviceType;  // Classified device type
  QStringList uuids;  // Service UUIDs
  int serviceProfiles;  // Bitfield of ServiceProfile

  // Device classification helpers
  bool isPhone() const { return deviceType == DeviceType::PHONE; }
  bool isOBDAdapter() const { return deviceType == DeviceType::OBD_ADAPTER; }
  bool isNavigationDevice() const { return deviceType == DeviceType::NAVIGATION; }
  bool supportsProfile(ServiceProfile profile) const { return serviceProfiles & static_cast<int>(profile); }
};

bool compare_by_rssi(const BluetoothDevice &a, const BluetoothDevice &b);

class BluetoothManager : public QObject {
  Q_OBJECT

public:
  QMap<QString, BluetoothDevice> seenDevices;  // Key: device address

  explicit BluetoothManager(QObject* parent = nullptr);
  void start();
  void stop();
  void requestScan();
  void pairDevice(const QString &address);
  void unpairDevice(const QString &address);
  void connectDevice(const QString &address);
  void disconnectDevice(const QString &address);
  void removeDevice(const QString &address);
  bool isBluetoothAvailable();
  bool isBluetoothPowered();
  void setBluetoothPowered(bool powered);

  // Device filtering by type
  QList<BluetoothDevice> getDevicesByType(DeviceType type) const;
  QList<BluetoothDevice> getPhones() const { return getDevicesByType(DeviceType::PHONE); }
  QList<BluetoothDevice> getOBDAdapters() const { return getDevicesByType(DeviceType::OBD_ADAPTER); }
  QList<BluetoothDevice> getNavigationDevices() const { return getDevicesByType(DeviceType::NAVIGATION); }

private:
  QString adapter;  // Path to BlueZ adapter (e.g., /org/bluez/hci0)
  QTimer scanTimer;
  bool scanning = false;

  QString getAdapter();
  void setup();
  void refreshDevices();
  QVariantMap getDeviceProperties(const QString &devicePath);
  void setDeviceProperty(const QString &devicePath, const QString &property, const QVariant &value);
  BluetoothDevice parseDevice(const QString &devicePath, const QVariantMap &properties);

  // Device classification
  DeviceType classifyDevice(const QString &icon, const QStringList &uuids, const QString &name) const;
  int parseServiceProfiles(const QStringList &uuids) const;
  bool isOBDDeviceName(const QString &name) const;
  bool isNavigationDeviceName(const QString &name) const;

signals:
  void devicesChanged();
  void pairingFailed(const QString &address, const QString &error);
  void pairingSuccess(const QString &address);
  void scanningChanged(bool scanning);
  void deviceConnected(const QString &address, DeviceType type);
  void deviceDisconnected(const QString &address);

private slots:
  void onInterfacesAdded(const QDBusObjectPath &path, const QVariantMap &interfaces);
  void onInterfacesRemoved(const QDBusObjectPath &path, const QStringList &interfaces);
  void onPropertiesChanged(const QString &interface, const QVariantMap &changed, const QStringList &invalidated);
  void scanTimeout();
  void onDevicePropertiesChanged(const QString &interface, const QVariantMap &changed, const QStringList &invalidated);
};
