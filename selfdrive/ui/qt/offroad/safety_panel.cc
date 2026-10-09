#include "selfdrive/ui/qt/offroad/safety_panel.h"

#include <QHBoxLayout>

// Temperature thresholds
const float TEMP_WARNING_C = 75.0f;
const float TEMP_CRITICAL_C = 85.0f;
const float GPS_MIN_ACCURACY_M = 10.0f;
const int IMU_MAX_TIMEOUT_MS = 1000;

SafetyPanel::SafetyPanel(QWidget* parent) : QFrame(parent) {
  QVBoxLayout* layout = new QVBoxLayout(this);
  layout->setContentsMargins(20, 20, 20, 20);
  layout->setSpacing(15);
  
  // Title
  QLabel* title = new QLabel(tr("SAFETY CHECKS"));
  title->setStyleSheet("font-size: 24px; font-weight: bold; color: white;");
  layout->addWidget(title);
  
  layout->addSpacing(20);
  
  // Door status
  QHBoxLayout* door_layout = new QHBoxLayout();
  door_icon = new QLabel("●");
  door_icon->setStyleSheet("font-size: 18px; color: red;");
  door_label = new QLabel(tr("DOOR OPEN"));
  door_label->setStyleSheet("font-size: 22px; color: red;");
  door_layout->addWidget(door_icon);
  door_layout->addWidget(door_label);
  door_layout->addStretch();
  layout->addLayout(door_layout);
  
  // Seatbelt status
  QHBoxLayout* seatbelt_layout = new QHBoxLayout();
  seatbelt_icon = new QLabel("●");
  seatbelt_icon->setStyleSheet("font-size: 18px; color: red;");
  seatbelt_label = new QLabel(tr("SEATBELT UNBUCKLED"));
  seatbelt_label->setStyleSheet("font-size: 22px; color: red;");
  seatbelt_layout->addWidget(seatbelt_icon);
  seatbelt_layout->addWidget(seatbelt_label);
  seatbelt_layout->addStretch();
  layout->addLayout(seatbelt_layout);
  
  // Harness status
  QHBoxLayout* harness_layout = new QHBoxLayout();
  harness_icon = new QLabel("●");
  harness_icon->setStyleSheet("font-size: 18px; color: red;");
  harness_label = new QLabel(tr("HARNESS NOT CONNECTED"));
  harness_label->setStyleSheet("font-size: 22px; color: red;");
  harness_layout->addWidget(harness_icon);
  harness_layout->addWidget(harness_label);
  harness_layout->addStretch();
  layout->addLayout(harness_layout);
  
  // Add separator
  layout->addSpacing(10);
  QFrame* line = new QFrame();
  line->setFrameShape(QFrame::HLine);
  line->setStyleSheet("background-color: #666666;");
  layout->addWidget(line);
  layout->addSpacing(10);
  
  // Extended safety checks label
  QLabel* extended_title = new QLabel(tr("SYSTEM CHECKS"));
  extended_title->setStyleSheet("font-size: 18px; font-weight: bold; color: #aaaaaa;");
  layout->addWidget(extended_title);
  layout->addSpacing(10);
  
  // Camera calibration status
  QHBoxLayout* calibration_layout = new QHBoxLayout();
  calibration_icon = new QLabel("●");
  calibration_icon->setStyleSheet("font-size: 24px; color: orange;");
  calibration_label = new QLabel(tr("CAMERA CALIBRATION REQUIRED"));
  calibration_label->setStyleSheet("font-size: 18px; color: orange;");
  calibration_layout->addWidget(calibration_icon);
  calibration_layout->addWidget(calibration_label);
  calibration_layout->addStretch();
  layout->addLayout(calibration_layout);
  
  // GPS status
  QHBoxLayout* gps_layout = new QHBoxLayout();
  gps_icon = new QLabel("●");
  gps_icon->setStyleSheet("font-size: 24px; color: orange;");
  gps_label = new QLabel(tr("GPS INITIALIZING"));
  gps_label->setStyleSheet("font-size: 18px; color: orange;");
  gps_layout->addWidget(gps_icon);
  gps_layout->addWidget(gps_label);
  gps_layout->addStretch();
  layout->addLayout(gps_layout);
  
  // IMU status
  QHBoxLayout* imu_layout = new QHBoxLayout();
  imu_icon = new QLabel("●");
  imu_icon->setStyleSheet("font-size: 24px; color: orange;");
  imu_label = new QLabel(tr("IMU INITIALIZING"));
  imu_label->setStyleSheet("font-size: 18px; color: orange;");
  imu_layout->addWidget(imu_icon);
  imu_layout->addWidget(imu_label);
  imu_layout->addStretch();
  layout->addLayout(imu_layout);
  
  // Temperature status
  QHBoxLayout* temp_layout = new QHBoxLayout();
  temp_icon = new QLabel("●");
  temp_icon->setStyleSheet("font-size: 24px; color: green;");
  temp_label = new QLabel(tr("TEMPERATURE NORMAL"));
  temp_label->setStyleSheet("font-size: 18px; color: green;");
  temp_layout->addWidget(temp_icon);
  temp_layout->addWidget(temp_label);
  temp_layout->addStretch();
  layout->addLayout(temp_layout);
  
  // Storage status
  QHBoxLayout* storage_layout = new QHBoxLayout();
  storage_icon = new QLabel("●");
  storage_icon->setStyleSheet("font-size: 24px; color: green;");
  storage_label = new QLabel(tr("STORAGE OK"));
  storage_label->setStyleSheet("font-size: 18px; color: green;");
  storage_layout->addWidget(storage_icon);
  storage_layout->addWidget(storage_label);
  storage_layout->addStretch();
  layout->addLayout(storage_layout);
  
  layout->addStretch();
  
  // Safety summary
  safety_summary = new QLabel(tr("❌ CANNOT ENGAGE - SAFETY CHECKS FAILING"));
  safety_summary->setStyleSheet("font-size: 18px; font-weight: bold; color: red; padding: 8px; background-color: rgba(200, 0, 0, 30); border-radius: 10px;");
  safety_summary->setAlignment(Qt::AlignCenter);
  layout->addWidget(safety_summary);
  
  setStyleSheet(R"(
    SafetyPanel {
      background-color: #333333;
      border-radius: 10px;
    }
  )");
}

void SafetyPanel::updateState(const UIState &s) {
  const SubMaster &sm = *(s.sm);
  
  // Get car state
  bool door_open = false;
  bool seatbelt_unbuckled = false;
  bool harness_not_connected = false;
  
  if (sm.valid("carState")) {
    auto car_state = sm["carState"].getCarState();
    door_open = car_state.getDoorOpen();
    seatbelt_unbuckled = car_state.getSeatbeltUnlatched();
  }
  
  if (sm.valid("pandaState")) {
    auto panda_state = sm["pandaState"].getPandaState();
    harness_not_connected = (panda_state.getHarnessStatus() != cereal::HarnessStatus::HARN_STATUS_NORMAL);
  }
  
  // Update door status (safe = door closed)
  door_safe = !door_open;
  updateStatus(door_label, door_icon, door_safe, tr("DOOR CLOSED"), tr("DOOR OPEN"));
  
  // Update seatbelt status (safe = buckled)
  seatbelt_safe = !seatbelt_unbuckled;
  updateStatus(seatbelt_label, seatbelt_icon, seatbelt_safe, tr("SEATBELT BUCKLED"), tr("SEATBELT UNBUCKLED"));
  
  // Update harness status (safe = connected)
  harness_safe = !harness_not_connected;
  updateStatus(harness_label, harness_icon, harness_safe, tr("HARNESS CONNECTED"), tr("HARNESS NOT CONNECTED"));
  
  // Camera calibration status
  if (sm.valid("liveCalibration")) {
    auto calib = sm["liveCalibration"].getLiveCalibration();
    bool calib_valid = calib.getCalStatus() == cereal::CalibrationStatus::CALIBRATED;
    calibration_safe = calib_valid;
    updateStatus(calibration_label, calibration_icon, calibration_safe, 
                 tr("CAMERA CALIBRATED"), tr("CAMERA CALIBRATION REQUIRED"));
  }
  
  // GPS status
  if (sm.valid("gpsLocationExternal") || sm.valid("gpsLocation")) {
    bool gps_valid = false;
    float accuracy = 999.0f;
    
    if (sm.valid("gpsLocationExternal")) {
      auto gps = sm["gpsLocationExternal"].getGpsLocationExternal();
      gps_valid = gps.getHasFix() && gps.getAccuracy() > 0;
      accuracy = gps.getAccuracy();
    } else if (sm.valid("gpsLocation")) {
      auto gps = sm["gpsLocation"].getGpsLocation();
      gps_valid = gps.getHasFix() && gps.getAccuracy() > 0;
      accuracy = gps.getAccuracy();
    }
    
    gps_safe = gps_valid && accuracy < GPS_MIN_ACCURACY_M;
    
    if (gps_safe) {
      gps_label->setText(tr("GPS OK (%1m accuracy)").arg(QString::number(accuracy, 'f', 1)));
      gps_label->setStyleSheet("font-size: 18px; color: green;");
      gps_icon->setStyleSheet("font-size: 24px; color: green;");
    } else if (gps_valid) {
      gps_label->setText(tr("GPS POOR (%1m accuracy)").arg(QString::number(accuracy, 'f', 1)));
      gps_label->setStyleSheet("font-size: 18px; color: orange;");
      gps_icon->setStyleSheet("font-size: 24px; color: orange;");
    } else {
      gps_label->setText(tr("GPS NO FIX"));
      gps_label->setStyleSheet("font-size: 18px; color: orange;");
      gps_icon->setStyleSheet("font-size: 24px; color: orange;");
    }
  }
  
  // IMU status
  if (sm.valid("sensorEvents")) {
    auto sensor_events = sm["sensorEvents"].getSensorEvents();
    bool imu_valid = false;
    uint64_t latest_time = 0;
    
    for (const auto& event : sensor_events) {
      if (event.getSensor() == cereal::SensorType::ACCELEROMETER ||
          event.getSensor() == cereal::SensorType::GYRO_UNCALIBRATED) {
        if (event.getTimestamp() > latest_time) {
          latest_time = event.getTimestamp();
          imu_valid = true;
        }
      }
    }
    
    // Check if IMU data is stale
    if (imu_valid && latest_time > 0) {
      uint64_t current_time = sm["sensorEvents"].getLogMonoTime();
      imu_safe = (current_time - latest_time) < (IMU_MAX_TIMEOUT_MS * 1000000ULL);
    } else {
      imu_safe = false;
    }
    
    updateStatus(imu_label, imu_icon, imu_safe, tr("IMU OK"), tr("IMU ERROR"));
  }
  
  // Temperature status
  if (sm.valid("deviceState")) {
    auto device = sm["deviceState"].getDeviceState();
    float max_temp = 0.0f;
    
    for (float temp : device.getCpuTempC()) {
      max_temp = std::max(max_temp, temp);
    }
    for (float temp : device.getGpuTempC()) {
      max_temp = std::max(max_temp, temp);
    }
    
    updateTemperatureStatus(temp_label, temp_icon, max_temp);
    temp_safe = max_temp < TEMP_CRITICAL_C;
  }
  
  // Storage status - freeSpacePercent is actually % FREE (not used)
  if (sm.valid("deviceState")) {
    auto device = sm["deviceState"].getDeviceState();
    float free_percent = device.getFreeSpacePercent();
    float used_percent = 100.0f - free_percent;
    
    // Safety thresholds: critical when less than 5% free, warning when less than 10% free
    const float STORAGE_FREE_WARNING_PERCENT = 10.0f;
    const float STORAGE_FREE_CRITICAL_PERCENT = 5.0f;
    
    storage_safe = free_percent > STORAGE_FREE_CRITICAL_PERCENT;
    
    if (free_percent > STORAGE_FREE_WARNING_PERCENT) {
      storage_label->setText(tr("STORAGE OK (%1% free)").arg(QString::number(free_percent, 'f', 0)));
      storage_label->setStyleSheet("font-size: 18px; color: green;");
      storage_icon->setStyleSheet("font-size: 24px; color: green;");
    } else if (free_percent > STORAGE_FREE_CRITICAL_PERCENT) {
      storage_label->setText(tr("STORAGE LOW (%1% free)").arg(QString::number(free_percent, 'f', 0)));
      storage_label->setStyleSheet("font-size: 18px; color: orange;");
      storage_icon->setStyleSheet("font-size: 24px; color: orange;");
    } else {
      storage_label->setText(tr("STORAGE CRITICAL (%1% free)").arg(QString::number(free_percent, 'f', 0)));
      storage_label->setStyleSheet("font-size: 18px; color: red;");
      storage_icon->setStyleSheet("font-size: 20px; color: red;");
    }
  }
  
  // Update summary
  bool all_safe = isSafeToDrive();
  if (all_safe) {
    safety_summary->setText(tr("✅ READY TO ENGAGE"));
    safety_summary->setStyleSheet("font-size: 20px; font-weight: bold; color: green; padding: 10px; background-color: rgba(0, 200, 0, 30); border-radius: 10px;");
  } else {
    safety_summary->setText(tr("❌ CANNOT ENGAGE - SAFETY CHECKS FAILING"));
    safety_summary->setStyleSheet("font-size: 20px; font-weight: bold; color: red; padding: 10px; background-color: rgba(200, 0, 0, 30); border-radius: 10px;");
  }
}

void SafetyPanel::updateStatus(QLabel* label, QLabel* icon, bool isSafe, const QString& safeText, const QString& unsafeText) {
  if (isSafe) {
    label->setText(safeText);
    label->setStyleSheet("font-size: 20px; color: green;");
    icon->setStyleSheet("font-size: 18px; color: green;");
  } else {
    label->setText(unsafeText);
    label->setStyleSheet("font-size: 20px; color: red;");
    icon->setStyleSheet("font-size: 18px; color: red;");
  }
}

void SafetyPanel::updateTemperatureStatus(QLabel* label, QLabel* icon, float tempC) {
  if (tempC < TEMP_WARNING_C) {
    label->setText(tr("TEMPERATURE NORMAL (%1°C)").arg(QString::number(tempC, 'f', 0)));
    label->setStyleSheet("font-size: 18px; color: green;");
    icon->setStyleSheet("font-size: 24px; color: green;");
  } else if (tempC < TEMP_CRITICAL_C) {
    label->setText(tr("TEMPERATURE HIGH (%1°C)").arg(QString::number(tempC, 'f', 0)));
    label->setStyleSheet("font-size: 18px; color: orange;");
    icon->setStyleSheet("font-size: 24px; color: orange;");
  } else {
    label->setText(tr("TEMPERATURE CRITICAL (%1°C)").arg(QString::number(tempC, 'f', 0)));
    label->setStyleSheet("font-size: 20px; color: red;");
    icon->setStyleSheet("font-size: 18px; color: red;");
  }
}

bool SafetyPanel::isSafeToDrive() const {
  return door_safe && seatbelt_safe && harness_safe && 
         calibration_safe && gps_safe && imu_safe && 
         temp_safe && storage_safe;
}
