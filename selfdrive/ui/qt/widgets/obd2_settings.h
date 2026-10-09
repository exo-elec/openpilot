#pragma once

#include <QWidget>
#include <QLabel>
#include <QPushButton>
#include <QComboBox>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QListWidget>
#include <QStackedWidget>
#include <QCheckBox>
#include <QLineEdit>

#include "selfdrive/ui/ui.h"

/**
 * OBD2Settings - OBD2/ELM327 Configuration UI
 * 
 * Allows users to:
 * - Pair with ELM327 Bluetooth adapters
 * - Configure OBD2 PIDs to monitor
 * - View live OBD2 data
 * - Set up custom PID configurations
 */

class OBD2Settings : public QWidget {
  Q_OBJECT

public:
  explicit OBD2Settings(QWidget* parent = nullptr);
  void updateState(const UIState& s);

private slots:
  void onScanClicked();
  void onConnectClicked();
  void onDisconnectClicked();
  void onPidToggled(int state);
  void onCustomPidAdd();
  void onTabChanged(int index);

private:
  void setupUI();
  void setupConnectionTab();
  void setupPidsTab();
  void setupDataTab();
  void updateDeviceList();
  void updatePidList();
  void updateDataDisplay();

  // Stacked widget for tabs
  QStackedWidget* stack;
  QListWidget* tab_list;
  
  // Connection tab
  QWidget* connection_tab;
  QLabel* status_label;
  QListWidget* device_list;
  QPushButton* scan_btn;
  QPushButton* connect_btn;
  QPushButton* disconnect_btn;
  QLabel* adapter_info_label;
  
  // PIDs tab
  QWidget* pids_tab;
  QListWidget* pid_list;
  QCheckBox* rpm_check;
  QCheckBox* speed_check;
  QCheckBox* coolant_check;
  QCheckBox* throttle_check;
  QCheckBox* voltage_check;
  QCheckBox* dtc_check;
  QLineEdit* custom_pid_input;
  QPushButton* add_pid_btn;
  
  // Data tab
  QWidget* data_tab;
  QLabel* rpm_value;
  QLabel* speed_value;
  QLabel* coolant_value;
  QLabel* throttle_value;
  QLabel* voltage_value;
  QLabel* dtc_value;
  
  // State
  bool is_connected = false;
  QString selected_device;
  
  // Constants
  const int TAB_WIDTH = 350;
};
