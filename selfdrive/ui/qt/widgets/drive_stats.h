#pragma once

#include <QFrame>
#include <QLabel>
#include <QTimer>
#include <QMouseEvent>
#include <QVBoxLayout>
#include <QGridLayout>
#include <QWidget>

#include "common/params.h"
#include "selfdrive/ui/qt/widgets/input.h"

// Drive stats widget using sunnypilot's proven approach
class DriveStats : public QFrame {
  Q_OBJECT

public:
  explicit DriveStats(QWidget* parent = nullptr);

protected:
  void mousePressEvent(QMouseEvent *event) override;
  void mouseReleaseEvent(QMouseEvent *event) override;

private:
  void showEvent(QShowEvent *event) override;
  void updateStats();
  void switchTripMode();
  void resetCurrentTrip();
  void loadDailyStats();
  void saveDailyStats();
  void updateWeeklyRollingStats();
  inline QString getDistanceUnit() const { return metric_ ? tr("KM") : tr("Miles"); }

  bool metric_;
  Params params_;
  QTimer *refresh_timer_;
  QTimer *long_press_timer_;
  
  // Trip mode management
  enum TripMode { TRIP_A, TRIP_B };
  TripMode current_trip_mode_;
  QLabel *trip_mode_label_;
  
  struct StatsLabels {
    QLabel *routes, *distance, *distance_unit, *hours, *engagement;
  } all_, week_, current_;

  // Session tracking for week and current trip stats
  float session_start_distance_;
  float session_start_time_;
  QString session_date_;
  
  // Trip A/B tracking
  struct TripData {
    float start_distance;
    float start_time;
    bool active;
  } trip_a_, trip_b_;
  
  // Daily rolling window for past week (7 days)
  struct DailyStats {
    float distance;
    float time;
    float engaged_time;  // OpenPilot engaged time for this day
    int drives;
    QString date;
  };
  QList<DailyStats> daily_history_;
  
  // Touch handling
  QPoint press_pos_;
  bool long_press_triggered_;

private slots:
  void refreshStats();
  void onLongPressTimeout();
};

// Information panel for right side - clickable to open settings
class SetupWidget : public QFrame {
  Q_OBJECT
public:
  explicit SetupWidget(QWidget* parent = nullptr);

signals:
  void openSettings(int index = 0, const QString &param = "");

protected:
  void mousePressEvent(QMouseEvent *event) override;
  void enterEvent(QEvent *event) override;
  void leaveEvent(QEvent *event) override;

private slots:
  void showInfoDialog();

private:
  QWidget *main_layout;
  bool hovered = false;
};

// EnhancedOpenPilot Information Dialog
class EopInfoDialog : public DialogBase {
  Q_OBJECT
public:
  explicit EopInfoDialog(QWidget* parent = nullptr);

protected:
  void mousePressEvent(QMouseEvent *event) override;
};

