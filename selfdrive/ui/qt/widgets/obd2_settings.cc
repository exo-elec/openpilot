#include "selfdrive/ui/qt/widgets/obd2_settings.h"

#include <QDebug>
#include <QHeaderView>
#include <QScrollArea>

OBD2Settings::OBD2Settings(QWidget* parent) : QWidget(parent) {
  setupUI();
}

void OBD2Settings::setupUI() {
  setFixedWidth(TAB_WIDTH);
  setStyleSheet(R"(
    OBD2Settings {
      background-color: #1a1a1a;
      border-radius: 12px;
    }
    QLabel {
      color: #ffffff;
      font-family: "Inter";
    }
    QPushButton {
      background-color: #4a90d9;
      color: white;
      border: none;
      border-radius: 6px;
      padding: 10px 20px;
      font-size: 14px;
    }
    QPushButton:hover {
      background-color: #5aa0e9;
    }
    QPushButton:pressed {
      background-color: #3a80c9;
    }
    QPushButton#disconnect_btn {
      background-color: #d94a4a;
    }
    QPushButton#disconnect_btn:hover {
      background-color: #e95a5a;
    }
    QListWidget {
      background-color: #2a2a2a;
      border: none;
      border-radius: 8px;
      color: #ffffff;
      font-size: 14px;
      padding: 10px;
    }
    QListWidget::item {
      padding: 8px;
      border-radius: 4px;
    }
    QListWidget::item:selected {
      background-color: #4a90d9;
    }
    QCheckBox {
      color: #ffffff;
      font-size: 14px;
      spacing: 8px;
    }
    QCheckBox::indicator {
      width: 20px;
      height: 20px;
      border-radius: 4px;
      border: 2px solid #4a90d9;
    }
    QCheckBox::indicator:checked {
      background-color: #4a90d9;
    }
    QLineEdit {
      background-color: #2a2a2a;
      border: 1px solid #3a3a3a;
      border-radius: 6px;
      color: #ffffff;
      padding: 8px;
      font-size: 14px;
    }
  )");
  
  QHBoxLayout* main_layout = new QHBoxLayout(this);
  main_layout->setSpacing(15);
  main_layout->setContentsMargins(20, 20, 20, 20);
  
  // Tab list (left side)
  tab_list = new QListWidget();
  tab_list->setFixedWidth(120);
  tab_list->addItem("Connection");
  tab_list->addItem("PIDs");
  tab_list->addItem("Live Data");
  connect(tab_list, &QListWidget::currentRowChanged, this, &OBD2Settings::onTabChanged);
  main_layout->addWidget(tab_list);
  
  // Stacked widget for content
  stack = new QStackedWidget();
  
  setupConnectionTab();
  setupPidsTab();
  setupDataTab();
  
  main_layout->addWidget(stack);
  
  // Select first tab
  tab_list->setCurrentRow(0);
}

void OBD2Settings::setupConnectionTab() {
  connection_tab = new QWidget();
  QVBoxLayout* layout = new QVBoxLayout(connection_tab);
  layout->setSpacing(15);
  
  // Title
  QLabel* title = new QLabel("OBD2 Connection");
  title->setStyleSheet("font-size: 18px; font-weight: 600;");
  layout->addWidget(title);
  
  // Status
  status_label = new QLabel("Status: Disconnected");
  status_label->setStyleSheet("font-size: 14px; color: #888888;");
  layout->addWidget(status_label);
  
  // Device list
  QLabel* devices_label = new QLabel("Available Devices:");
  devices_label->setStyleSheet("font-size: 14px; margin-top: 10px;");
  layout->addWidget(devices_label);
  
  device_list = new QListWidget();
  device_list->setMaximumHeight(150);
  layout->addWidget(device_list);
  
  // Buttons
  QHBoxLayout* btn_layout = new QHBoxLayout();
  
  scan_btn = new QPushButton("Scan");
  connect(scan_btn, &QPushButton::clicked, this, &OBD2Settings::onScanClicked);
  btn_layout->addWidget(scan_btn);
  
  connect_btn = new QPushButton("Connect");
  connect_btn->setEnabled(false);
  connect(connect_btn, &QPushButton::clicked, this, &OBD2Settings::onConnectClicked);
  btn_layout->addWidget(connect_btn);
  
  disconnect_btn = new QPushButton("Disconnect");
  disconnect_btn->setObjectName("disconnect_btn");
  disconnect_btn->setVisible(false);
  connect(disconnect_btn, &QPushButton::clicked, this, &OBD2Settings::onDisconnectClicked);
  btn_layout->addWidget(disconnect_btn);
  
  layout->addLayout(btn_layout);
  
  // Adapter info
  adapter_info_label = new QLabel();
  adapter_info_label->setStyleSheet("font-size: 12px; color: #888888; margin-top: 10px;");
  adapter_info_label->setWordWrap(true);
  layout->addWidget(adapter_info_label);
  
  layout->addStretch();
  stack->addWidget(connection_tab);
}

void OBD2Settings::setupPidsTab() {
  pids_tab = new QWidget();
  QVBoxLayout* layout = new QVBoxLayout(pids_tab);
  layout->setSpacing(15);
  
  // Title
  QLabel* title = new QLabel("OBD2 PIDs");
  title->setStyleSheet("font-size: 18px; font-weight: 600;");
  layout->addWidget(title);
  
  // Description
  QLabel* desc = new QLabel("Select PIDs to monitor:");
  desc->setStyleSheet("font-size: 14px; color: #888888;");
  layout->addWidget(desc);
  
  // Standard PIDs
  rpm_check = new QCheckBox("Engine RPM (010C)");
  rpm_check->setChecked(true);
  connect(rpm_check, &QCheckBox::stateChanged, this, &OBD2Settings::onPidToggled);
  layout->addWidget(rpm_check);
  
  speed_check = new QCheckBox("Vehicle Speed (010D)");
  speed_check->setChecked(true);
  connect(speed_check, &QCheckBox::stateChanged, this, &OBD2Settings::onPidToggled);
  layout->addWidget(speed_check);
  
  coolant_check = new QCheckBox("Coolant Temperature (0105)");
  coolant_check->setChecked(true);
  connect(coolant_check, &QCheckBox::stateChanged, this, &OBD2Settings::onPidToggled);
  layout->addWidget(coolant_check);
  
  throttle_check = new QCheckBox("Throttle Position (0111)");
  throttle_check->setChecked(false);
  connect(throttle_check, &QCheckBox::stateChanged, this, &OBD2Settings::onPidToggled);
  layout->addWidget(throttle_check);
  
  voltage_check = new QCheckBox("Control Module Voltage (0142)");
  voltage_check->setChecked(false);
  connect(voltage_check, &QCheckBox::stateChanged, this, &OBD2Settings::onPidToggled);
  layout->addWidget(voltage_check);
  
  dtc_check = new QCheckBox("Diagnostic Trouble Codes (03)");
  dtc_check->setChecked(true);
  connect(dtc_check, &QCheckBox::stateChanged, this, &OBD2Settings::onPidToggled);
  layout->addWidget(dtc_check);
  
  // Custom PID
  layout->addSpacing(20);
  QLabel* custom_label = new QLabel("Add Custom PID:");
  custom_label->setStyleSheet("font-size: 14px;");
  layout->addWidget(custom_label);
  
  QHBoxLayout* custom_layout = new QHBoxLayout();
  custom_pid_input = new QLineEdit();
  custom_pid_input->setPlaceholderText("e.g., 010C");
  custom_layout->addWidget(custom_pid_input);
  
  add_pid_btn = new QPushButton("Add");
  add_pid_btn->setFixedWidth(80);
  connect(add_pid_btn, &QPushButton::clicked, this, &OBD2Settings::onCustomPidAdd);
  custom_layout->addWidget(add_pid_btn);
  
  layout->addLayout(custom_layout);
  
  layout->addStretch();
  stack->addWidget(pids_tab);
}

void OBD2Settings::setupDataTab() {
  data_tab = new QWidget();
  QVBoxLayout* layout = new QVBoxLayout(data_tab);
  layout->setSpacing(15);
  
  // Title
  QLabel* title = new QLabel("Live OBD2 Data");
  title->setStyleSheet("font-size: 18px; font-weight: 600;");
  layout->addWidget(title);
  
  // Data grid
  auto createDataRow = [this](const QString& label) -> std::pair<QLabel*, QLabel*> {
    QHBoxLayout* row = new QHBoxLayout();
    
    QLabel* name = new QLabel(label);
    name->setStyleSheet("font-size: 14px;");
    name->setFixedWidth(150);
    row->addWidget(name);
    
    QLabel* value = new QLabel("--");
    value->setStyleSheet("font-size: 14px; font-weight: 600; color: #4a90d9;");
    row->addWidget(value);
    
    row->addStretch();
    layout->addLayout(row);
    
    return {name, value};
  };
  
  auto [rpm_name, rpm_val] = createDataRow("Engine RPM:");
  rpm_value = rpm_val;
  
  auto [speed_name, speed_val] = createDataRow("Speed:");
  speed_value = speed_val;
  
  auto [coolant_name, coolant_val] = createDataRow("Coolant Temp:");
  coolant_value = coolant_val;
  
  auto [throttle_name, throttle_val] = createDataRow("Throttle:");
  throttle_value = throttle_val;
  
  auto [voltage_name, voltage_val] = createDataRow("Voltage:");
  voltage_value = voltage_val;
  
  auto [dtc_name, dtc_val] = createDataRow("DTCs:");
  dtc_value = dtc_val;
  
  layout->addStretch();
  stack->addWidget(data_tab);
}

void OBD2Settings::updateState(const UIState& s) {
  // Update connection status
  // TODO: Parse ObdState from s.scene
  
  // Update data values
  // TODO: Parse live OBD2 data
}

void OBD2Settings::updateDeviceList() {
  device_list->clear();
  // TODO: Get list of paired Bluetooth devices
  // For now, add some example devices
  device_list->addItem("OBDLink MX+ (00:00:00:00:00:00)");
  device_list->addItem("ELM327 v1.5 (11:22:33:44:55:66)");
}

void OBD2Settings::onScanClicked() {
  qDebug() << "OBD2: Scanning for devices...";
  status_label->setText("Status: Scanning...");
  updateDeviceList();
  connect_btn->setEnabled(device_list->count() > 0);
  status_label->setText("Status: Found " + QString::number(device_list->count()) + " devices");
}

void OBD2Settings::onConnectClicked() {
  auto item = device_list->currentItem();
  if (!item) return;
  
  selected_device = item->text();
  qDebug() << "OBD2: Connecting to" << selected_device;
  
  // TODO: Send connect command to obd2d
  
  is_connected = true;
  status_label->setText("Status: Connected to " + selected_device);
  status_label->setStyleSheet("font-size: 14px; color: #4ade80;");
  
  scan_btn->setVisible(false);
  connect_btn->setVisible(false);
  disconnect_btn->setVisible(true);
}

void OBD2Settings::onDisconnectClicked() {
  qDebug() << "OBD2: Disconnecting...";
  
  // TODO: Send disconnect command to obd2d
  
  is_connected = false;
  status_label->setText("Status: Disconnected");
  status_label->setStyleSheet("font-size: 14px; color: #888888;");
  
  scan_btn->setVisible(true);
  connect_btn->setVisible(true);
  disconnect_btn->setVisible(false);
}

void OBD2Settings::onPidToggled(int state) {
  QCheckBox* sender = qobject_cast<QCheckBox*>(QObject::sender());
  if (!sender) return;
  
  QString pid_name = sender->text();
  bool enabled = (state == Qt::Checked);
  
  qDebug() << "OBD2: PID" << pid_name << (enabled ? "enabled" : "disabled");
  
  // TODO: Send PID configuration to obd2d
}

void OBD2Settings::onCustomPidAdd() {
  QString pid = custom_pid_input->text().trimmed();
  if (pid.isEmpty()) return;
  
  qDebug() << "OBD2: Adding custom PID" << pid;
  
  // TODO: Validate and add custom PID
  
  custom_pid_input->clear();
}

void OBD2Settings::onTabChanged(int index) {
  stack->setCurrentIndex(index);
}
