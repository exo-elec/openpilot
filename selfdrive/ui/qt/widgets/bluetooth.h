#pragma once

#include <QButtonGroup>
#include <QFrame>
#include <QVBoxLayout>
#include <QPushButton>
#include <QLabel>

#include "selfdrive/ui/qt/network/bluetooth_manager.h"

class BluetoothDeviceButton : public QPushButton {
  Q_OBJECT

public:
  BluetoothDeviceButton(const BluetoothDevice &device, QWidget *parent = nullptr);
  void updateDevice(const BluetoothDevice &device);
  const BluetoothDevice &getDevice() const { return device_; }

private:
  BluetoothDevice device_;
  void updateText();
  QString getDeviceIcon() const;
  QString getDeviceTypeLabel() const;
  QString getSignalStrengthIcon() const;

signals:
  void deviceClicked(const BluetoothDevice &device);
};

class BluetoothUI : public QWidget {
  Q_OBJECT

public:
  explicit BluetoothUI(QWidget *parent, BluetoothManager *manager);

public slots:
  void refresh();

private:
  BluetoothManager *bt_manager;
  QVBoxLayout *main_layout;
  QButtonGroup *device_buttons;
  QLabel *scanning_label;
  QLabel *status_label;

  void showDeviceCategories();
  void addDeviceCategory(const QString &title, const QList<BluetoothDevice> &devices);
  void clearDeviceList();

signals:
  void connectToDevice(const BluetoothDevice &device);
};
