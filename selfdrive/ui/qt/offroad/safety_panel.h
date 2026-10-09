#pragma once

#include <QFrame>
#include <QLabel>
#include <QVBoxLayout>

#include "selfdrive/ui/ui.h"

class SafetyPanel : public QFrame {
  Q_OBJECT

public:
  explicit SafetyPanel(QWidget* parent = 0);
  void updateState(const UIState &s);
  bool isSafeToDrive() const;

private:
  void updateStatus(QLabel* label, QLabel* icon, bool isSafe, const QString& safeText, const QString& unsafeText);
  void updateTemperatureStatus(QLabel* label, QLabel* icon, float tempC);
  
  // Basic safety
  QLabel* door_label;
  QLabel* door_icon;
  QLabel* seatbelt_label;
  QLabel* seatbelt_icon;
  QLabel* harness_label;
  QLabel* harness_icon;
  
  // Extended safety checks
  QLabel* calibration_label;
  QLabel* calibration_icon;
  QLabel* gps_label;
  QLabel* gps_icon;
  QLabel* imu_label;
  QLabel* imu_icon;
  QLabel* temp_label;
  QLabel* temp_icon;
  QLabel* storage_label;
  QLabel* storage_icon;
  
  QLabel* safety_summary;
  
  // Safety state
  bool door_safe = false;
  bool seatbelt_safe = false;
  bool harness_safe = false;
  bool calibration_safe = false;
  bool gps_safe = false;
  bool imu_safe = false;
  bool temp_safe = false;
  bool storage_safe = false;
  

};
