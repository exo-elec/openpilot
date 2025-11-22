#include "selfdrive/ui/qt/widgets/prime.h"

#include <QDateTime>
#include <QGridLayout>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QMouseEvent>
#include <QSizePolicy>
#include <QPushButton>
#include <QDialog>
#include <QPixmap>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>

#include "common/params.h"

// Match sunnypilot's constants exactly
const double MILE_TO_KM = 1.609344;

// Exact copy of sunnypilot's helper function
static QLabel* newLabel(const QString& text, const QString &type) {
  QLabel* label = new QLabel(text);
  label->setProperty("type", type);
  return label;
}

DriveStats::DriveStats(QWidget* parent) : QFrame(parent) {
  metric_ = Params().getBool("IsMetric");

  // Load trip mode from JSON blob (FrogPilot pattern)
  current_trip_mode_ = TRIP_A;
  QJsonObject stats = loadNagasPilotStats();
  if (!stats.isEmpty()) {
    current_trip_mode_ = static_cast<TripMode>(stats.value("TripMode").toInt(0));
  }
  long_press_triggered_ = false;

  // Initialize trip data
  trip_a_ = {0.0f, 0.0f, false};
  trip_b_ = {0.0f, 0.0f, false};

  // Load daily history for rolling week stats
  loadDailyStats();

  // Adjusted layout for prime area with extra left margin to avoid border
  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(55, 25, 30, 25);

  // Extra compact layout for three-row display in prime area
  auto add_stats_layouts = [=](const QString &title, StatsLabels& labels, QLabel** title_label_ref = nullptr) {
    QGridLayout* grid_layout = new QGridLayout;
    grid_layout->setVerticalSpacing(5);
    grid_layout->setContentsMargins(0, 3, 0, 3);

    int row = 0;
    // Only add title and spacer if title is not empty
    if (!title.isEmpty()) {
      QLabel* title_label = newLabel(title, "title");
      grid_layout->addWidget(title_label, row++, 0, 1, 3);
      grid_layout->addItem(new QSpacerItem(0, 12), row++, 0, 1, 1);
      
      // Store reference if requested
      if (title_label_ref) {
        *title_label_ref = title_label;
      }
    }

    grid_layout->addWidget(labels.routes = newLabel("0", "number"), row, 0, Qt::AlignLeft);
    grid_layout->addWidget(labels.distance = newLabel("0", "number"), row, 1, Qt::AlignLeft);
    grid_layout->addWidget(labels.hours = newLabel("0", "number"), row, 2, Qt::AlignLeft);
    grid_layout->addWidget(labels.engagement = newLabel("0", "number"), row, 3, Qt::AlignLeft);

    grid_layout->addWidget(newLabel(tr("Drives"), "unit"), row + 1, 0, Qt::AlignLeft);
    grid_layout->addWidget(labels.distance_unit = newLabel(getDistanceUnit(), "unit"), row + 1, 1, Qt::AlignLeft);
    grid_layout->addWidget(newLabel(tr("Hours"), "unit"), row + 1, 2, Qt::AlignLeft);
    grid_layout->addWidget(newLabel(tr("% nagaspilot"), "unit"), row + 1, 3, Qt::AlignLeft);

    main_layout->addLayout(grid_layout);
  };

  // Three-row layout: All Time (never resets), Past Week (rolling 7 days), Current Trip (A or B)
  add_stats_layouts(tr("ALL TIME"), all_);
  add_stats_layouts(tr("PAST WEEK"), week_);
  add_stats_layouts(current_trip_mode_ == TRIP_A ? tr("TRIP A") : tr("TRIP B"), current_, &trip_mode_label_);

  // Initialize session tracking for recent activity tracking
  std::string distance_str = params_.get("np_trip_total_distance");
  std::string time_str = params_.get("np_trip_uptime_onroad");
  session_start_distance_ = distance_str.empty() ? 0.0f : std::stof(distance_str);
  session_start_time_ = time_str.empty() ? 0.0f : std::stof(time_str);
  session_date_ = QDateTime::currentDateTime().toString("yyyy-MM-dd");

  // For simplicity, use session stats to approximate "past week" activity
  // In a full implementation, this would track daily stats for rolling 7-day calculation

  // Use responsive 2-second refresh for real-time trip data
  refresh_timer_ = new QTimer(this);
  QObject::connect(refresh_timer_, &QTimer::timeout, this, &DriveStats::refreshStats);
  refresh_timer_->start(2000);  // 2 seconds for near real-time updates

  // Training status timer for TSC updates (FrogPilot-style enhancement)
  training_status_timer_ = new QTimer(this);
  QObject::connect(training_status_timer_, &QTimer::timeout, this, &DriveStats::updateTrainingStatus);
  training_status_timer_->start(5000);  // 5 seconds for training status updates

  // Initialize training status tracking
  last_training_active_ = false;
  last_calibration_progress_ = 0.0f;

  // Long press timer for reset functionality
  long_press_timer_ = new QTimer(this);
  long_press_timer_->setSingleShot(true);
  QObject::connect(long_press_timer_, &QTimer::timeout, this, &DriveStats::onLongPressTimeout);

  // Add TSC training status display (FrogPilot-style enhancement)
  setupTrainingStatus();

  // Adjusted styling for prime area (more compact than sunnypilot's dedicated panel)
  setStyleSheet(R"(
    DriveStats {
      background-color: #333333;
      border-radius: 10px;
    }

    QLabel[type="title"] { font-size: 42px; font-weight: 500; color: #86FF4E; }
    QLabel[type="number"] { font-size: 65px; font-weight: 500; }
    QLabel[type="unit"] { font-size: 38px; font-weight: 300; color: #465BEA; }
  )");
}

QJsonObject DriveStats::loadNagasPilotStats() {
  // Load NagasPilotStats JSON blob (FrogPilot pattern)
  std::string stats_json_str = params_.get("NagasPilotStats");
  if (stats_json_str.empty()) {
    return QJsonObject(); // Return empty object if no data
  }

  QJsonParseError error;
  QJsonDocument doc = QJsonDocument::fromJson(QString::fromStdString(stats_json_str).toUtf8(), &error);

  if (error.error != QJsonParseError::NoError) {
    // JSON parsing failed, return empty object
    return QJsonObject();
  }

  return doc.object();
}

void DriveStats::updateStats() {
  // Load NagasPilotStats JSON blob (FrogPilot pattern)
  QJsonObject stats = loadNagasPilotStats();
  bool backend_healthy = !stats.isEmpty();

  // Additional health check using LastUpdate timestamp if available
  if (backend_healthy && stats.contains("LastUpdate")) {
    try {
      long long last_update = stats.value("LastUpdate").toVariant().toLongLong();
      long long current_time = std::chrono::duration_cast<std::chrono::seconds>(
          std::chrono::system_clock::now().time_since_epoch()).count();
      // Backend is healthy if updated within last 30 seconds (smart write timing)
      backend_healthy = (current_time - last_update) < 30;
    } catch (...) {
      backend_healthy = false;
    }
  }

  // Update pattern with backend health indication
  auto update = [=](int routes, double distance_miles, double time_minutes, double engagement_ratio, StatsLabels& labels) {
    if (backend_healthy) {
      labels.routes->setText(QString::number(routes));
      labels.distance->setText(QString::number(int(distance_miles * (metric_ ? MILE_TO_KM : 1))));
      labels.engagement->setText(QString::number((int)engagement_ratio));
    } else {
      // Show "---" when backend is unhealthy
      labels.routes->setText("---");
      labels.distance->setText("---");
      labels.engagement->setText("---");
    }
    labels.distance_unit->setText(getDistanceUnit());
    labels.hours->setText(QString::number((int)(time_minutes / 60)));
  };

  // UI ONLY: EXTRACT from JSON blob (FrogPilot pattern)
  double total_distance = stats.value("TotalDistance").toDouble(0.0);
  double total_time = stats.value("TotalTime").toDouble(0.0);
  int total_drives = stats.value("TotalDrives").toInt(0);
  double lifetime_engagement = stats.value("LifetimeEngagementRatio").toDouble(0.0);

  // UI ONLY: Format for display (data from JSON is pre-validated)
  double total_distance_miles = total_distance * 0.000621371;  // meters to miles
  double total_time_minutes = total_time / 60.0;  // seconds to minutes

  // Extract weekly stats from JSON blob
  double week_distance = stats.value("WeekDistance").toDouble(0.0);
  double week_time = stats.value("WeekTime").toDouble(0.0);
  int week_drives = stats.value("WeekDrives").toInt(0);
  double week_engagement_ratio = stats.value("WeekEngagementRatio").toDouble(0.0);

  // Format weekly stats for display
  double week_distance_miles = week_distance * 0.000621371;  // meters to miles
  double week_time_minutes = week_time / 60.0;  // seconds to minutes

  // Extract trip A/B stats from JSON blob (pre-calculated by backend)
  double trip_a_distance = stats.value("TripADistance").toDouble(0.0);
  double trip_a_time = stats.value("TripATime").toDouble(0.0);
  double trip_b_distance = stats.value("TripBDistance").toDouble(0.0);
  double trip_b_time = stats.value("TripBTime").toDouble(0.0);

  // Update current trip mode from JSON blob
  current_trip_mode_ = static_cast<TripMode>(stats.value("TripMode").toInt(0));

  // Update trip mode label if changed
  trip_mode_label_->setText(current_trip_mode_ == TRIP_A ? tr("TRIP A") : tr("TRIP B"));

  // Get current trip stats for display (FrogPilot pattern)
  double current_trip_distance = (current_trip_mode_ == TRIP_A) ? trip_a_distance : trip_b_distance;
  double current_trip_time = (current_trip_mode_ == TRIP_A) ? trip_a_time : trip_b_time;
  double current_trip_distance_miles = current_trip_distance * 0.000621371;  // meters to miles
  double current_trip_time_minutes = current_trip_time / 60.0;  // seconds to minutes

  // Simple trip active check
  bool trip_active = (current_trip_distance > 0.0 || current_trip_time > 0.0);

  // UI ONLY: Update displays (no backend logic)
  update(total_drives, total_distance_miles, total_time_minutes, lifetime_engagement, all_);
  update(week_drives, week_distance_miles, week_time_minutes, week_engagement_ratio, week_);
  update(trip_active ? 1 : 0, current_trip_distance_miles, current_trip_time_minutes, lifetime_engagement, current_);
}

void DriveStats::refreshStats() {
  updateStats();
}

void DriveStats::mousePressEvent(QMouseEvent *event) {
  if (event->button() == Qt::LeftButton) {
    press_pos_ = event->pos();
    long_press_triggered_ = false;
    
    // Start long press timer (800ms for reset functionality)
    long_press_timer_->start(800);
  }
  QFrame::mousePressEvent(event);
}

void DriveStats::mouseReleaseEvent(QMouseEvent *event) {
  if (event->button() == Qt::LeftButton) {
    long_press_timer_->stop();

    // If not a long press, handle gesture
    if (!long_press_triggered_) {
      QPoint release_pos = event->pos();
      int dx = release_pos.x() - press_pos_.x();
      int dy = release_pos.y() - press_pos_.y();
      int abs_dx = abs(dx);
      int abs_dy = abs(dy);

      // Check for horizontal swipe (left/right trip switching)
      if (abs_dx > 50 && abs_dx > abs_dy * 2) {  // Horizontal swipe must be 50+ pixels and 2x vertical
        if (dx > 0) {
          // Swipe right: TRIP A → TRIP B
          if (current_trip_mode_ == TRIP_A) {
            switchTripMode();
          }
        } else {
          // Swipe left: TRIP B → TRIP A
          if (current_trip_mode_ == TRIP_B) {
            switchTripMode();
          }
        }
      }
      // Check for tap (small movement)
      else if (abs_dx < 20 && abs_dy < 20) {
        // Short tap: switch trip mode (fallback for non-swipe users)
        switchTripMode();
      }
      // Medium movement: ignore (accidental touch)
    }
  }
  QFrame::mouseReleaseEvent(event);
}

void DriveStats::onLongPressTimeout() {
  long_press_triggered_ = true;
  resetCurrentTrip();
}

void DriveStats::switchTripMode() {
  // UI ONLY: Send trip mode switch request to backend
  int new_mode = (current_trip_mode_ == TRIP_A) ? 1 : 0;  // Toggle: A=0, B=1
  params_.put("np_trip_mode", std::to_string(new_mode));

  // UI ONLY: Don't update display immediately - let backend confirmation handle it
  // The updateStats() method will sync current_trip_mode_ from backend parameter
  // This prevents race conditions where UI and backend show different modes

  // UI ONLY: Refresh display to get updated trip data from backend
  updateStats();
}

void DriveStats::resetCurrentTrip() {
  // UI ONLY: Send reset request for currently displayed trip to backend
  int trip_to_reset = (current_trip_mode_ == TRIP_A) ? 0 : 1;  // A=0, B=1
  params_.put("np_trip_reset_request", std::to_string(trip_to_reset));

  // Log for debugging
  printf("UI: Long press reset - requesting reset of %s\n",
         (current_trip_mode_ == TRIP_A) ? "TRIP A" : "TRIP B");

  // UI ONLY: Refresh display - backend will provide reset confirmation via status parameter
  updateStats();
}

void DriveStats::loadDailyStats() {
  // UI ONLY: Simplified - backend provides weekly stats directly
  // No need to load individual daily stats since backend calculates weekly totals
  daily_history_.clear(); // Keep empty since not used anymore

  // UI ONLY: No trip data loading needed - backend manages all trip state
}

void DriveStats::saveDailyStats() {
  // UI ONLY: No daily stats saving needed - backend handles all data persistence
}

void DriveStats::updateWeeklyRollingStats() {
  // UI ONLY: No weekly calculation needed - backend provides weekly totals
}

void DriveStats::showEvent(QShowEvent* event) {
  // Exact copy of sunnypilot's showEvent pattern
  bool metric = Params().getBool("IsMetric");
  if (metric_ != metric) {
    metric_ = metric;
    updateStats();
  }

  // PAST WEEK is a rolling 7-day window - no reset needed
}

// Information panel for right side - read more content with no functions
SetupWidget::SetupWidget(QWidget* parent) : QFrame(parent) {
  main_layout = new QWidget;

  QVBoxLayout *outer_layout = new QVBoxLayout(this);
  outer_layout->setContentsMargins(0, 0, 0, 0);
  outer_layout->addWidget(main_layout);

  QWidget *content = new QWidget;
  QVBoxLayout *content_layout = new QVBoxLayout(content);
  content_layout->setContentsMargins(30, 25, 30, 25);
  content_layout->setSpacing(20);

  // Information panel header - bigger like left panel
  QLabel *info_title = new QLabel(tr("NAGASPILOT"));
  info_title->setStyleSheet("font-size: 60px; font-weight: 600; color: #86FF4E;");
  content_layout->addWidget(info_title);

  // Version/build info - bigger
  QLabel *version_info = new QLabel(tr("Advanced Driving Assistance"));
  version_info->setStyleSheet("font-size: 40px; font-weight: 400; color: #FFFFFF;");
  content_layout->addWidget(version_info);

  content_layout->addSpacing(30);

  // Feature highlights - bigger
  QLabel *features_title = new QLabel(tr("NEXT_SUPPORTS:"));
  features_title->setStyleSheet("font-size: 30px; font-weight: 500; color: #A0A0A0;");
  content_layout->addWidget(features_title);

  QStringList features = {
    tr("• BYD_ATTO3"),
    tr("• BYD_DOLPHIN"),
    tr("• DEEPAL_S05")
  };

  for (const QString &feature : features) {
    QLabel *feature_label = new QLabel(feature);
    // Set BYD_ATTO3, BYD_DOLPHIN, DEEPAL_S05 to white color
    feature_label->setStyleSheet("font-size: 50px; color: #FFFFFF; margin: 8px 0px;");
    content_layout->addWidget(feature_label);
  }

  content_layout->addStretch();

  // Read Me button (similar to original "Pair device" button)
  QPushButton* readMeBtn = new QPushButton(tr("Read Me"));
  readMeBtn->setFixedHeight(120);  // Set explicit height to prevent cutoff
  readMeBtn->setStyleSheet(R"(
    QPushButton {
      font-size: 55px;
      font-weight: 500;
      border-radius: 10px;
      background-color: #465BEA;
      padding: 20px 40px;
      color: white;
      text-align: center;
    }
    QPushButton:pressed {
      background-color: #3A4BC7;
    }
  )");
  content_layout->addWidget(readMeBtn);

  content_layout->addSpacing(15);

  // Connect button to custom dialog with QR code
  QObject::connect(readMeBtn, &QPushButton::clicked, [this]() {
    showInfoDialog();
  });

  QVBoxLayout *content_layout_final = new QVBoxLayout(main_layout);
  content_layout_final->setContentsMargins(0, 0, 0, 0);
  content_layout_final->addWidget(content);

  setStyleSheet(R"(
    SetupWidget {
      border-radius: 10px;
      background-color: #333333;
    }
    SetupWidget:hover {
      background-color: #3B3B3B;
    }
  )");

  // Retain size while hidden
  QSizePolicy sp_retain = sizePolicy();
  sp_retain.setRetainSizeWhenHidden(true);
  setSizePolicy(sp_retain);
}

void SetupWidget::mousePressEvent(QMouseEvent *event) {
  // Button handles the click now, no need for panel-wide clicking
  QFrame::mousePressEvent(event);
}

void SetupWidget::enterEvent(QEvent *event) {
  hovered = true;
  setStyleSheet(R"(
    SetupWidget {
      border-radius: 10px;
      background-color: #3B3B3B;
    }
  )");
  QFrame::enterEvent(event);
}

void SetupWidget::leaveEvent(QEvent *event) {
  hovered = false;
  setStyleSheet(R"(
    SetupWidget {
      border-radius: 10px;
      background-color: #333333;
    }
  )");
  QFrame::leaveEvent(event);
}

void SetupWidget::showInfoDialog() {
  NagaspilotInfoDialog *dialog = new NagaspilotInfoDialog(this);
  dialog->exec();
  dialog->deleteLater();
}

// NagaspilotInfoDialog implementation - whole screen touch to close
NagaspilotInfoDialog::NagaspilotInfoDialog(QWidget *parent) : DialogBase(parent) {
  QHBoxLayout *hlayout = new QHBoxLayout(this);
  hlayout->setContentsMargins(0, 0, 0, 0);
  hlayout->setSpacing(0);

  setStyleSheet("NagaspilotInfoDialog { background-color: #333333; }");

  // text (left side - no close button, just content)
  QVBoxLayout *vlayout = new QVBoxLayout();
  vlayout->setContentsMargins(85, 70, 50, 70);
  vlayout->setSpacing(50);
  hlayout->addLayout(vlayout, 1);

  QLabel *title = new QLabel(tr("NAGASPILOT Information"), this);
  title->setStyleSheet("font-size: 75px; color: #86FF4E; font-weight: bold;");
  title->setWordWrap(true);
  vlayout->addWidget(title);

  vlayout->addSpacing(30);

  QLabel *instructions = new QLabel(tr("Advanced Driver Assistance System\n\nFeatures:\n• Enhanced Lane Keeping\n• Adaptive Cruise Control\n• Traffic Sign Recognition\n• Driver Monitoring\n• Trip Statistics\n\nTap anywhere to close"), this);
  instructions->setStyleSheet("font-size: 47px; font-weight: bold; color: white;");
  instructions->setWordWrap(true);
  vlayout->addWidget(instructions);

  vlayout->addStretch();

  // QR code (right side - like original PairingQRWidget)
  QWidget *qr = new QWidget(this);
  qr->setStyleSheet("background-color: white;");
  hlayout->addWidget(qr, 1);

  // Add QR image to the white background widget
  QVBoxLayout *qr_layout = new QVBoxLayout(qr);
  qr_layout->setContentsMargins(50, 50, 50, 50);

  QLabel *qrLabel = new QLabel();
  QPixmap qrPixmap("selfdrive/assets/images/nagaspilot_qr.png");
  if (!qrPixmap.isNull()) {
    qrLabel->setPixmap(qrPixmap);
    qrLabel->setAlignment(Qt::AlignCenter);
  } else {
    qrLabel->setText(tr("QR Code"));
    qrLabel->setStyleSheet("font-size: 24px; color: black;");
    qrLabel->setAlignment(Qt::AlignCenter);
  }
  qr_layout->addWidget(qrLabel);
}

// Override mouse press event to close dialog when anywhere is clicked
void NagaspilotInfoDialog::mousePressEvent(QMouseEvent *event) {
  if (event->button() == Qt::LeftButton) {
    reject();  // Close dialog on any click
  }
  DialogBase::mousePressEvent(event);
}

// TSC Training Status Setup (FrogPilot-style enhancement)
void DriveStats::setupTrainingStatus() {
  // Create training status labels with FrogPilot-style compact design
  training_status_label_ = new QLabel(tr("TSC: Ready"), this);
  training_status_label_->setStyleSheet("font-size: 14px; color: #888888;");
  training_status_label_->setAlignment(Qt::AlignCenter);
  
  calibration_progress_label_ = new QLabel(tr("Calibration: 0%"), this);
  calibration_progress_label_->setStyleSheet("font-size: 14px; color: #888888;");
  calibration_progress_label_->setAlignment(Qt::AlignCenter);
  
  // Add to main layout (compact placement)
  QVBoxLayout* status_layout = new QVBoxLayout();
  status_layout->addWidget(training_status_label_);
  status_layout->addWidget(calibration_progress_label_);
  status_layout->setSpacing(2);
  status_layout->setContentsMargins(0, 5, 0, 0);
  
  // Find main layout and add status (assuming it's the first layout)
  QVBoxLayout* main_layout = qobject_cast<QVBoxLayout*>(layout());
  if (main_layout) {
    main_layout->addLayout(status_layout);
  }
}

// Update TSC Training Status (FrogPilot-style enhancement)
void DriveStats::updateTrainingStatus() {
  // Get current training status from parameters
  bool training_active = params_.getBool("np_tsc_training_active");
  float calibration_progress = params_.getFloat("np_tsc_calibration_progress");
  bool calibration_complete = params_.getBool("np_tsc_calibration_complete");
  
  // Update training status label
  if (training_active != last_training_active_) {
    last_training_active_ = training_active;
    if (training_active) {
      training_status_label_->setText(tr("TSC: Training"));
      training_status_label_->setStyleSheet("font-size: 14px; color: #4CAF50;");  // Green for active
    } else {
      training_status_label_->setText(tr("TSC: Ready"));
      training_status_label_->setStyleSheet("font-size: 14px; color: #888888;");  // Gray for ready
    }
  }
  
  // Update calibration progress label
  if (std::abs(calibration_progress - last_calibration_progress_) > 0.5f) {  // Only update on significant change
    last_calibration_progress_ = calibration_progress;
    
    if (calibration_complete) {
      calibration_progress_label_->setText(tr("Calibration: Complete"));
      calibration_progress_label_->setStyleSheet("font-size: 14px; color: #4CAF50;");  // Green for complete
    } else if (calibration_progress > 0.0f) {
      calibration_progress_label_->setText(tr("Calibration: %1%").arg(static_cast<int>(calibration_progress)));
      calibration_progress_label_->setStyleSheet("font-size: 14px; color: #FF9800;");  // Orange for in progress
    } else {
      calibration_progress_label_->setText(tr("Calibration: 0%"));
      calibration_progress_label_->setStyleSheet("font-size: 14px; color: #888888;");  // Gray for not started
    }
  }
}