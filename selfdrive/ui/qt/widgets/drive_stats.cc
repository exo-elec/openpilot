#include "selfdrive/ui/qt/widgets/drive_stats.h"

#include <QDateTime>
#include <QGridLayout>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QMouseEvent>
#include <QSizePolicy>
#include <QPushButton>
#include <QDialog>
#include <QPixmap>

#include "common/params.h"

const double MILE_TO_KM = 1.609344;

static float safe_stof(const std::string &s, float def = 0.0f) {
  if (s.empty()) return def;
  try { return std::stof(s); } catch (...) { return def; }
}

static int safe_stoi(const std::string &s, int def = 0) {
  if (s.empty()) return def;
  try { return std::stoi(s); } catch (...) { return def; }
}

static QLabel* newLabel(const QString& text, const QString &type) {
  QLabel* label = new QLabel(text);
  label->setProperty("type", type);
  return label;
}

DriveStats::DriveStats(QWidget* parent) : QFrame(parent) {
  metric_ = Params().getBool("IsMetric");
  std::string trip_mode_str = params_.get("EOPTripMode");
  current_trip_mode_ = TRIP_A;
  if (!trip_mode_str.empty()) {
    try {
      current_trip_mode_ = static_cast<TripMode>(safe_stoi(trip_mode_str));
    } catch (...) {
      current_trip_mode_ = TRIP_A;
    }
  }
  long_press_triggered_ = false;

  // Initialize trip data
  trip_a_ = {0.0f, 0.0f, false};
  trip_b_ = {0.0f, 0.0f, false};

  // Load daily history for rolling week stats
  loadDailyStats();

  // Adjusted layout for EOP offroad home area with extra left margin to avoid border
  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(30, 15, 20, 15);

  // Extra compact layout for three-row display in EOP offroad home area
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
    grid_layout->addWidget(newLabel(tr("%"), "unit"), row + 1, 3, Qt::AlignLeft);

    main_layout->addLayout(grid_layout);
  };

  // Three-row layout: All Time (never resets), Past Week (rolling 7 days), Current Trip (A or B)
  add_stats_layouts(tr("ALL TIME"), all_);
  add_stats_layouts(tr("PAST WEEK"), week_);
  add_stats_layouts(current_trip_mode_ == TRIP_A ? tr("TRIP A") : tr("TRIP B"), current_, &trip_mode_label_);

  // Initialize session tracking for recent activity tracking
  std::string distance_str = params_.get("EOPTripTotalDistance");
  std::string time_str = params_.get("EOPTripUptimeOnroad");
  session_start_distance_ = safe_stof(distance_str);
  session_start_time_ = safe_stof(time_str);
  session_date_ = QDateTime::currentDateTime().toString("yyyy-MM-dd");

  // For simplicity, use session stats to approximate "past week" activity
  // In a full implementation, this would track daily stats for rolling 7-day calculation

  // Use sunnypilot's 30-second refresh pattern
  refresh_timer_ = new QTimer(this);
  QObject::connect(refresh_timer_, &QTimer::timeout, this, &DriveStats::refreshStats);
  refresh_timer_->start(30000);

  // Long press timer for reset functionality
  long_press_timer_ = new QTimer(this);
  long_press_timer_->setSingleShot(true);
  QObject::connect(long_press_timer_, &QTimer::timeout, this, &DriveStats::onLongPressTimeout);

  // Adjusted styling for EOP offroad home area (more compact than sunnypilot's dedicated panel)
  setStyleSheet(R"(
    DriveStats {
      background-color: #333333;
      border-radius: 10px;
    }

    QLabel[type="title"] { font-size: 22px; font-weight: 500; color: #86FF4E; }
    QLabel[type="number"] { font-size: 34px; font-weight: 500; }
    QLabel[type="unit"] { font-size: 16px; font-weight: 300; color: #465BEA; }
  )");
}

void DriveStats::updateStats() {
  // Exact copy of sunnypilot's update pattern
  auto update = [=](int routes, double distance_miles, double time_minutes, double engagement_ratio, StatsLabels& labels) {
    labels.routes->setText(QString::number(routes));
    labels.distance->setText(QString::number(int(distance_miles * (metric_ ? MILE_TO_KM : 1))));
    labels.distance_unit->setText(getDistanceUnit());
    labels.hours->setText(QString::number((int)(time_minutes / 60)));
    labels.engagement->setText(QString::number((int)engagement_ratio));
  };

  // Get current stats and convert to sunnypilot's format (with safe parsing)
  std::string distance_str = params_.get("EOPTripTotalDistance");
  std::string time_str = params_.get("EOPTripUptimeOnroad");
  std::string drives_str = params_.get("EOPTripTotalDrives");
  
  // Read engagement ratio parameters
  std::string lifetime_engagement_str = params_.get("EOPTripLifetimeEngagementRatio");
  std::string current_engagement_str = params_.get("EOPTripCurrentEngagementRatio");

  float total_distance_meters = safe_stof(distance_str);
  float total_time_seconds = safe_stof(time_str);
  int total_drives = safe_stoi(drives_str);
  
  double lifetime_engagement_ratio = lifetime_engagement_str.empty() ? 0.0 : std::stod(lifetime_engagement_str);
  double current_engagement_ratio = current_engagement_str.empty() ? 0.0 : std::stod(current_engagement_str);

  // Convert to miles and minutes like sunnypilot's API format
  double total_distance_miles = total_distance_meters * 0.000621371; // meters to miles
  double total_time_minutes = total_time_seconds / 60.0; // seconds to minutes

  // Update daily rolling window for proper past week calculation
  updateWeeklyRollingStats();
  
  // Calculate past week stats using proper daily rolling window
  double week_distance_miles = 0.0;
  double week_time_minutes = 0.0;
  double week_engaged_time_seconds = 0.0;
  double week_total_onroad_time_seconds = 0.0;
  int week_drives = 0;
  
  for (const auto& day : daily_history_) {
    week_distance_miles += day.distance * 0.000621371; // meters to miles
    week_time_minutes += day.time / 60.0; // seconds to minutes
    week_engaged_time_seconds += day.engaged_time;
    week_total_onroad_time_seconds += day.time;
    week_drives += day.drives;
  }
  
  // Calculate 7-day rolling engagement ratio
  double week_engagement_ratio = (week_total_onroad_time_seconds > 0) ? 
    (week_engaged_time_seconds / week_total_onroad_time_seconds * 100.0) : 0.0;

  // Update BOTH Trip A and Trip B in background (independent of display mode)
  
  // Calculate Trip A stats
  float trip_a_distance_meters = std::max(0.0f, total_distance_meters - trip_a_.start_distance);
  float trip_a_time_seconds = std::max(0.0f, total_time_seconds - trip_a_.start_time);
  bool trip_a_active = (trip_a_distance_meters > 100.0f || trip_a_time_seconds > 60.0f);
  trip_a_.active = trip_a_active;
  
  // Auto-reset Trip A if it reaches 1000km
  if (trip_a_distance_meters >= 1000000.0f) {
    trip_a_.start_distance = total_distance_meters;
    trip_a_.start_time = total_time_seconds;
    params_.put("EOPTripAStartDistance", std::to_string(trip_a_.start_distance));
    params_.put("EOPTripAStartTime", std::to_string(trip_a_.start_time));
    trip_a_distance_meters = 0.0f;
    trip_a_time_seconds = 0.0f;
  }
  
  // Calculate Trip B stats
  float trip_b_distance_meters = std::max(0.0f, total_distance_meters - trip_b_.start_distance);
  float trip_b_time_seconds = std::max(0.0f, total_time_seconds - trip_b_.start_time);
  bool trip_b_active = (trip_b_distance_meters > 100.0f || trip_b_time_seconds > 60.0f);
  trip_b_.active = trip_b_active;
  
  // Auto-reset Trip B if it reaches 1000km
  if (trip_b_distance_meters >= 1000000.0f) {
    trip_b_.start_distance = total_distance_meters;
    trip_b_.start_time = total_time_seconds;
    params_.put("EOPTripBStartDistance", std::to_string(trip_b_.start_distance));
    params_.put("EOPTripBStartTime", std::to_string(trip_b_.start_time));
    trip_b_distance_meters = 0.0f;
    trip_b_time_seconds = 0.0f;
  }
  
  // Get stats for currently displayed trip
  float current_trip_distance_meters = (current_trip_mode_ == TRIP_A) ? trip_a_distance_meters : trip_b_distance_meters;
  float current_trip_time_seconds = (current_trip_mode_ == TRIP_A) ? trip_a_time_seconds : trip_b_time_seconds;
  double current_trip_distance_miles = current_trip_distance_meters * 0.000621371;
  double current_trip_time_minutes = current_trip_time_seconds / 60.0;
  bool trip_active = (current_trip_mode_ == TRIP_A) ? trip_a_active : trip_b_active;

  // Update displays using sunnypilot's exact pattern
  update(total_drives, total_distance_miles, total_time_minutes, lifetime_engagement_ratio, all_);
  update(week_drives, week_distance_miles, week_time_minutes, week_engagement_ratio, week_); // Proper 7-day rolling
  update(trip_active ? 1 : 0, current_trip_distance_miles, current_trip_time_minutes, current_engagement_ratio, current_);
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
    
    // If not a long press, handle as short touch (switch trip mode)
    if (!long_press_triggered_) {
      QPoint release_pos = event->pos();
      int dx = abs(release_pos.x() - press_pos_.x());
      int dy = abs(release_pos.y() - press_pos_.y());
      
      // Only switch if touch didn't move too much (not a drag)
      if (dx < 20 && dy < 20) {
        switchTripMode();
      }
    }
  }
  QFrame::mouseReleaseEvent(event);
}

void DriveStats::onLongPressTimeout() {
  long_press_triggered_ = true;
  resetCurrentTrip();
}

void DriveStats::switchTripMode() {
  // Switch between Trip A and Trip B
  current_trip_mode_ = (current_trip_mode_ == TRIP_A) ? TRIP_B : TRIP_A;
  
  // Save the new mode
  params_.put("EOPTripMode", std::to_string(static_cast<int>(current_trip_mode_)));
  
  // Update the display
  trip_mode_label_->setText(current_trip_mode_ == TRIP_A ? tr("TRIP A") : tr("TRIP B"));
  
  // Force immediate refresh to show new trip data
  updateStats();
}

void DriveStats::resetCurrentTrip() {
  // Get current totals
  std::string distance_str = params_.get("EOPTripTotalDistance");
  std::string time_str = params_.get("EOPTripUptimeOnroad");
  
  float total_distance_meters = safe_stof(distance_str);
  float total_time_seconds = safe_stof(time_str);
  
  // Reset the current trip (A or B)
  TripData& current_trip = (current_trip_mode_ == TRIP_A) ? trip_a_ : trip_b_;
  current_trip.start_distance = total_distance_meters;
  current_trip.start_time = total_time_seconds;
  current_trip.active = false;
  
  // Save trip data
  QString trip_prefix = (current_trip_mode_ == TRIP_A) ? "EOPTripA" : "EOPTripB";
  params_.put((trip_prefix + "_StartDistance").toStdString(), std::to_string(current_trip.start_distance));
  params_.put((trip_prefix + "_StartTime").toStdString(), std::to_string(current_trip.start_time));
  
  // Force immediate refresh
  updateStats();
}

void DriveStats::loadDailyStats() {
  // Load up to 7 days of daily statistics for rolling window
  daily_history_.clear();
  
  for (int i = 0; i < 7; i++) {
    QDateTime date = QDateTime::currentDateTime().addDays(-i);
    QString date_str = date.toString("yyyy-MM-dd");
    
    DailyStats day_stats;
    day_stats.date = date_str;
    std::string dist_str = params_.get(("Daily_" + date_str + "_Distance").toStdString());
    std::string time_str = params_.get(("Daily_" + date_str + "_Time").toStdString());
    std::string engaged_time_str = params_.get(("Daily_" + date_str + "_EngagedTime").toStdString());
    std::string drives_str = params_.get(("Daily_" + date_str + "_Drives").toStdString());
    
    day_stats.distance = 0.0f;
    day_stats.time = 0.0f;
    day_stats.engaged_time = 0.0f;
    day_stats.drives = 0;
    
    try {
      day_stats.distance = safe_stof(dist_str);
      day_stats.time = safe_stof(time_str);
      day_stats.engaged_time = safe_stof(engaged_time_str);
      day_stats.drives = safe_stoi(drives_str);
    } catch (...) {
      // Keep default values on error
    }
    
    // Only add if there's actual data
    if (day_stats.distance > 0 || day_stats.time > 0 || day_stats.drives > 0) {
      daily_history_.append(day_stats);
    }
  }
  
  // Load trip data - initialize from current totals if no saved data
  std::string total_distance_str = params_.get("EOPTripTotalDistance");
  std::string total_time_str = params_.get("EOPTripUptimeOnroad");
  float current_total_distance = 0.0f;
  float current_total_time = 0.0f;
  
  try {
    current_total_distance = safe_stof(total_distance_str);
    current_total_time = safe_stof(total_time_str);
  } catch (...) {
    // Keep default values on error
  }
  
  std::string trip_a_dist_str = params_.get("EOPTripAStartDistance");
  std::string trip_a_time_str = params_.get("EOPTripAStartTime");
  std::string trip_b_dist_str = params_.get("EOPTripBStartDistance");
  std::string trip_b_time_str = params_.get("EOPTripBStartTime");
  
  trip_a_.start_distance = current_total_distance;
  trip_a_.start_time = current_total_time;
  trip_b_.start_distance = current_total_distance;
  trip_b_.start_time = current_total_time;
  
  try {
    trip_a_.start_distance = safe_stof(trip_a_dist_str);
    trip_a_.start_time = safe_stof(trip_a_time_str);
    trip_b_.start_distance = safe_stof(trip_b_dist_str);
    trip_b_.start_time = safe_stof(trip_b_time_str);
  } catch (...) {
    // Keep default values on error
  }
  
  // If these are new trips (no saved data), save the initial values
  if (trip_a_dist_str.empty()) {
    params_.put("EOPTripAStartDistance", std::to_string(trip_a_.start_distance));
    params_.put("EOPTripAStartTime", std::to_string(trip_a_.start_time));
  }
  if (trip_b_dist_str.empty()) {
    params_.put("EOPTripBStartDistance", std::to_string(trip_b_.start_distance));
    params_.put("EOPTripBStartTime", std::to_string(trip_b_.start_time));
  }
}

void DriveStats::saveDailyStats() {
  QString today = QDateTime::currentDateTime().toString("yyyy-MM-dd");
  
  // Get current totals
  std::string distance_str = params_.get("EOPTripTotalDistance");
  std::string time_str = params_.get("EOPTripUptimeOnroad");
  std::string drives_str = params_.get("EOPTripTotalDrives");
  
  float total_distance = safe_stof(distance_str);
  float total_engaged_time = safe_stof(time_str);
  int total_drives = safe_stoi(drives_str);
  
  // Calculate today's activity (difference from session start)
  float today_distance = std::max(0.0f, total_distance - session_start_distance_);
  float today_engaged_time = std::max(0.0f, total_engaged_time - session_start_time_);
  
  // For total onroad time, we need a session start reference - use engaged time as approximation for now
  float today_total_onroad_time = std::max(today_engaged_time, today_engaged_time * 1.2f); // Estimate 20% more total time
  
  // Estimate today's drives (simplified)
  int today_drives = std::max(0, total_drives - static_cast<int>(session_start_distance_ / 1000.0f));
  
  // Save today's stats
  params_.put(("Daily_" + today + "_Distance").toStdString(), std::to_string(today_distance));
  params_.put(("Daily_" + today + "_Time").toStdString(), std::to_string(today_total_onroad_time));
  params_.put(("Daily_" + today + "_EngagedTime").toStdString(), std::to_string(today_engaged_time));
  params_.put(("Daily_" + today + "_Drives").toStdString(), std::to_string(today_drives));
}

void DriveStats::updateWeeklyRollingStats() {
  // Save current day's stats
  saveDailyStats();
  
  // Clean up old daily stats (keep only 7 days)
  QDateTime cutoff = QDateTime::currentDateTime().addDays(-7);
  QString cutoff_str = cutoff.toString("yyyy-MM-dd");
  
  // Remove stats older than 7 days
  for (int i = 8; i < 30; i++) { // Clean up to 30 days back
    QDateTime old_date = QDateTime::currentDateTime().addDays(-i);
    QString old_date_str = old_date.toString("yyyy-MM-dd");
    
    params_.remove(("Daily_" + old_date_str + "_Distance").toStdString());
    params_.remove(("Daily_" + old_date_str + "_Time").toStdString());
    params_.remove(("Daily_" + old_date_str + "_EngagedTime").toStdString());
    params_.remove(("Daily_" + old_date_str + "_Drives").toStdString());
  }
  
  // Reload daily history
  loadDailyStats();
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

  // Information panel header
  QLabel *info_title = new QLabel(tr("EnhancedOpenPilot"));
  info_title->setStyleSheet("font-size: 26px; font-weight: 600; color: #86FF4E;");
  content_layout->addWidget(info_title);

  // Version/build info
  QLabel *version_info = new QLabel(tr("Advanced Driving Assistance"));
  version_info->setStyleSheet("font-size: 22px; font-weight: 400; color: #FFFFFF;");
  version_info->setWordWrap(true);
  content_layout->addWidget(version_info);

  content_layout->addSpacing(15);

  // Feature highlights
  QLabel *features_title = new QLabel(tr("NEXT SUPPORTS:"));
  features_title->setStyleSheet("font-size: 20px; font-weight: 500; color: #A0A0A0;");
  content_layout->addWidget(features_title);

  QStringList features = {
    tr("• B-SUV / C-SUV / D-SUV"),
    tr("• B-Sedan / C-Sedan / D-Sedan"),
    tr("• MPV")
  };

  for (const QString &feature : features) {
    QLabel *feature_label = new QLabel(feature);
    feature_label->setStyleSheet("font-size: 17px; color: #FFFFFF; margin: 2px 0px;");
    feature_label->setWordWrap(true);
    content_layout->addWidget(feature_label);
  }

  content_layout->addStretch();

  // Read Me button
  QPushButton* readMeBtn = new QPushButton(tr("Dashboard"));
  readMeBtn->setFixedHeight(44);
  readMeBtn->setStyleSheet(R"(
    QPushButton {
      font-size: 22px;
      font-weight: 500;
      border-radius: 10px;
      background-color: #465BEA;
      padding: 0px 20px;
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
  EopInfoDialog *dialog = new EopInfoDialog(this);
  dialog->exec();
  dialog->deleteLater();
}

// EopInfoDialog implementation - full-width dark dialog, tap to close
EopInfoDialog::EopInfoDialog(QWidget *parent) : DialogBase(parent) {
  QVBoxLayout *vlayout = new QVBoxLayout(this);
  vlayout->setContentsMargins(48, 40, 48, 40);
  vlayout->setSpacing(20);

  setStyleSheet("EopInfoDialog { background-color: #2a2a2a; }");

  QLabel *title = new QLabel(tr("ExoPilot — Advanced ADAS"), this);
  title->setStyleSheet("font-size: 28px; color: #86FF4E; font-weight: bold;");
  title->setWordWrap(true);
  vlayout->addWidget(title);

  QLabel *subtitle = new QLabel(tr("RK3588 · ExoPilot 01M / 01L"), this);
  subtitle->setStyleSheet("font-size: 18px; color: #888888;");
  vlayout->addWidget(subtitle);

  vlayout->addSpacing(8);

  QLabel *body = new QLabel(
    tr("• Adaptive Lane Centering (ALC) — laneful and laneless\n"
       "• Adaptive Cruise Control with speed sign recognition\n"
       "• Blind Spot Detection (BSD) with proximity chime\n"
       "• Driver Attention Monitoring via steering + rear camera\n"
       "• NavPilot — OSM turn-by-turn routing\n"
       "• Bird's Eye View (BEV) object overlay\n"
       "• Bluetooth NavPilot app pairing (iOS + Android)\n"
       "• Trip statistics and drive logging"), this);
  body->setStyleSheet("font-size: 19px; color: #e0e0e0; line-height: 160%;");
  body->setWordWrap(true);
  vlayout->addWidget(body);

  vlayout->addStretch();

  QLabel *hint = new QLabel(tr("Tap anywhere to close"), this);
  hint->setStyleSheet("font-size: 16px; color: #555555;");
  hint->setAlignment(Qt::AlignCenter);
  vlayout->addWidget(hint);
}

// Override mouse press event to close dialog when anywhere is clicked
void EopInfoDialog::mousePressEvent(QMouseEvent *event) {
  if (event->button() == Qt::LeftButton) {
    reject();  // Close dialog on any click
  }
  DialogBase::mousePressEvent(event);
}
