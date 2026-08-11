#include "selfdrive/ui/qt/onroad/hud.h"

#include <cmath>

#include <QPainterPath>

#include "selfdrive/ui/qt/util.h"

constexpr int SET_SPEED_NA = 255;

HudRenderer::HudRenderer() {}

void HudRenderer::updateState(const UIState &s) {
  is_metric = s.scene.is_metric;
  status = s.status;

  const SubMaster &sm = *(s.sm);
  if (sm.rcv_frame("carState") < s.scene.started_frame) {
    is_cruise_set = false;
    set_speed = SET_SPEED_NA;
    speed = 0.0;
    return;
  }

  const auto &controls_state = sm["controlsState"].getControlsState();
  const auto &car_state = sm["carState"].getCarState();

  // Handle older routes where vCruiseCluster is not set
  set_speed = car_state.getVCruiseCluster() == 0.0 ? controls_state.getVCruiseDEPRECATED() : car_state.getVCruiseCluster();
  is_cruise_set = set_speed > 0 && set_speed != SET_SPEED_NA;
  is_cruise_available = set_speed != -1;

  if (is_cruise_set && !is_metric) {
    set_speed *= KM_TO_MILE;
  }

  // Handle older routes where vEgoCluster is not set
  v_ego_cluster_seen = v_ego_cluster_seen || car_state.getVEgoCluster() != 0.0;
  float v_ego = v_ego_cluster_seen ? car_state.getVEgoCluster() : car_state.getVEgo();
  speed = std::max<float>(0.0f, v_ego * (is_metric ? MS_TO_KPH : MS_TO_MPH));

  // Speed limit sign: convert m/s to display units (km/h or mph)
  float sl_ms = s.scene.nav_speed_limit_ms;
  nav_speed_limit = (sl_ms > 0.0f) ? std::round(sl_ms * (is_metric ? MS_TO_KPH : MS_TO_MPH)) : 0.0f;

  // EOP: Driver pose status (steering-based monitor — no face fields here)
  show_driver_status = sm.rcv_frame("driverPoseState") >= s.scene.started_frame;
  if (show_driver_status) {
    const auto &driver = sm["driverPoseState"].getDriverPoseState();
    attention_prob = driver.getAttentionProb();
  }
  // Driver overlay data from driverStatus (camera-based: face fields live here)
  if (sm.rcv_frame("driverStatus") >= s.scene.started_frame) {
    const auto &fs = sm["driverStatus"].getDriverStatus();
    driver_detected = fs.getFaceDetected();
    driver_forward = fs.getFaceForward();
    driver_x = fs.getFaceX();
    driver_y = fs.getFaceY();
    driver_yaw = fs.getFaceYaw();
    driver_pitch = fs.getFacePitch();
  }

  // BSD / blinker state
  const auto &cs = car_state;
  left_blinker = cs.getLeftBlinker();
  right_blinker = cs.getRightBlinker();
  // EOP: Fuse vehicle-native BSD with controlsState BSD (radar + Hailo camera)
  const auto &ctrl = sm["controlsState"].getControlsState();
  left_blindspot = cs.getLeftBlindspot() || ctrl.getLeftBlindSpot() > 0;
  right_blindspot = cs.getRightBlindspot() || ctrl.getRightBlindSpot() > 0;
  bsd_pulse += 0.08f;
  if (bsd_pulse > 1.0f) bsd_pulse = 0.0f;

  // Turn-by-turn instruction (navd/Valhalla). Map/route stay on the phone;
  // the device only shows the next maneuver as an arrow + distance.
  show_nav_instruction = sm.valid("navInstruction") && sm.rcv_frame("navInstruction") >= s.scene.started_frame;
  if (show_nav_instruction) {
    const auto &nav = sm["navInstruction"].getNavInstruction();
    nav_maneuver_type = nav.getManeuverType().cStr();
    nav_maneuver_modifier = nav.getManeuverModifier().cStr();
    nav_primary_text = nav.getManeuverPrimaryText().cStr();
    nav_maneuver_distance_m = nav.getManeuverDistance();
    show_nav_instruction = !nav_maneuver_type.isEmpty() && nav_maneuver_type != "none";
  }
}

void HudRenderer::draw(QPainter &p, const QRect &surface_rect) {
  p.save();

  // Draw header gradient
  QLinearGradient bg(0, UI_HEADER_HEIGHT - (UI_HEADER_HEIGHT / 2.5), 0, UI_HEADER_HEIGHT);
  bg.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.45));
  bg.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0));
  p.fillRect(0, 0, surface_rect.width(), UI_HEADER_HEIGHT, bg);


  if (is_cruise_available) {
    drawSetSpeed(p, surface_rect);
  }
  drawCurrentSpeed(p, surface_rect);
  if (nav_speed_limit > 0) {
    drawSpeedLimit(p, surface_rect);
  }

  // EOP: Driver pose status indicator (top-right)
  if (show_driver_status) {
    drawDriverStatus(p, surface_rect);
  }

  // BSD red overlay when changing lane into occupied blind spot
  drawBlindSpotOverlay(p, surface_rect);

  // Turn-by-turn maneuver arrow (top-left) — no map, just the next turn
  if (show_nav_instruction) {
    drawNavInstruction(p, surface_rect);
  }

  p.restore();
}

void HudRenderer::drawSetSpeed(QPainter &p, const QRect &surface_rect) {
  // Draw outer box + border to contain set speed
  const QSize default_size = {110, 130};
  QSize set_speed_size = is_metric ? QSize(125, 130) : default_size;
  QRect set_speed_rect(QPoint(35 + (default_size.width() - set_speed_size.width()) / 2, 25), set_speed_size);

  // Draw set speed box
  p.setPen(QPen(QColor(255, 255, 255, 75), 4));
  p.setBrush(QColor(0, 0, 0, 166));
  p.drawRoundedRect(set_speed_rect, 20, 20);

  // Colors based on status
  QColor max_color = QColor(0xa6, 0xa6, 0xa6, 0xff);
  QColor set_speed_color = QColor(0x72, 0x72, 0x72, 0xff);
  if (is_cruise_set) {
    set_speed_color = QColor(255, 255, 255);
    if (status == STATUS_DISENGAGED) {
      max_color = QColor(255, 255, 255);
    } else if (status == STATUS_OVERRIDE) {
      max_color = QColor(0x91, 0x9b, 0x95, 0xff);
    } else {
      max_color = QColor(0x80, 0xd8, 0xa6, 0xff);
    }
  }

  // Draw "MAX" text
  p.setFont(InterFont(24, QFont::DemiBold));
  p.setPen(max_color);
  p.drawText(set_speed_rect.adjusted(0, 15, 0, 0), Qt::AlignTop | Qt::AlignHCenter, tr("MAX"));

  // Draw set speed
  QString setSpeedStr = is_cruise_set ? QString::number(std::nearbyint(set_speed)) : "–";
  p.setFont(InterFont(32, QFont::Bold));
  p.setPen(set_speed_color);
  p.drawText(set_speed_rect.adjusted(0, 42, 0, 0), Qt::AlignTop | Qt::AlignHCenter, setSpeedStr);
}

void HudRenderer::drawCurrentSpeed(QPainter &p, const QRect &surface_rect) {
  QString speedStr = QString::number(std::nearbyint(speed));

  p.setFont(InterFont(100, QFont::Bold));
  drawText(p, surface_rect.center().x(), 120, speedStr);

  p.setFont(InterFont(38));
  drawText(p, surface_rect.center().x(), 165, is_metric ? tr("km/h") : tr("mph"), 200);
}

void HudRenderer::drawDriverStatus(QPainter &p, const QRect &surface_rect) {
  // Small driver status pill in top-right corner
  QString label = driver_detected ? (driver_forward ? tr("DRIVER") : tr("AWAY")) : tr("NO DRIVER");
  QColor bg_color = driver_detected ? (driver_forward ? QColor(0x00, 0xd8, 0x4a, 0xcc) : QColor(0xff, 0xa5, 0x00, 0xcc))
                                      : QColor(0xff, 0x33, 0x33, 0xcc);

  QFont font = InterFont(16, QFont::DemiBold);
  font.setStyleStrategy(QFont::PreferAntialias);
  p.setFont(font);
  QFontMetrics fm(font);
  int text_w = fm.horizontalAdvance(label);
  int pad_x = 8, pad_y = 4;
  int pill_w = text_w + pad_x * 2;
  int pill_h = fm.height() + pad_y * 2;
  int x = surface_rect.width() - pill_w - 20;
  int y = 20;

  QRect pill(x, y, pill_w, pill_h);
  p.setPen(Qt::NoPen);
  p.setBrush(bg_color);
  p.drawRoundedRect(pill, pill_h / 2, pill_h / 2);

  p.setPen(QColor(0xff, 0xff, 0xff, 0xee));
  p.drawText(pill, Qt::AlignCenter, label);

  // Driver bounding box + gaze arrow overlay
  if (driver_detected) {
    int box_size = 70;
    int fx = int((1.0f - driver_x) * surface_rect.width());
    int fy = int(driver_y * surface_rect.height());
    int alpha = int(180 * (1.0f - attention_prob) + 50);

    // Bounding box with std-modulated alpha
    p.setPen(QPen(QColor(0xff, 0xff, 0xff, alpha), 2));
    p.setBrush(Qt::NoBrush);
    p.drawRoundedRect(fx - box_size / 2, fy - box_size / 2, box_size, box_size, 8, 8);

    // Gaze arrow
    float arrow_len = 25.0f;
    int ax = fx + int(arrow_len * sinf(driver_yaw * 3.14159f / 180.0f));
    int ay = fy - int(arrow_len * sinf(driver_pitch * 3.14159f / 180.0f));
    p.setPen(QPen(QColor(0x00, 0xd8, 0x4a, alpha), 2));
    p.drawLine(fx, fy, ax, ay);
    p.drawEllipse(QPoint(ax, ay), 3, 3);
  }
}

void HudRenderer::drawText(QPainter &p, int x, int y, const QString &text, int alpha) {
  QRect real_rect = p.fontMetrics().boundingRect(text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});

  p.setPen(QColor(0xff, 0xff, 0xff, alpha));
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void HudRenderer::drawBlindSpotOverlay(QPainter &p, const QRect &surface_rect) {
  // Show red side-bar warning when blinker is on AND blind spot is occupied
  int w = surface_rect.width();
  int h = surface_rect.height();
  int bar_w = 24;

  // Pulsing alpha for visibility
  int alpha = 120 + int(80 * sinf(bsd_pulse * 3.14159f * 2.0f));

  // Left side: left blinker + left blind spot
  if (left_blinker && left_blindspot) {
    QLinearGradient grad(0, 0, bar_w, 0);
    grad.setColorAt(0.0, QColor(255, 0, 0, alpha));
    grad.setColorAt(1.0, QColor(255, 0, 0, 0));
    p.setPen(Qt::NoPen);
    p.setBrush(grad);
    p.drawRect(0, 0, bar_w, h);

    // "BLIND SPOT" text rotated vertically on left edge
    p.save();
    p.translate(bar_w + 4, h / 2);
    p.rotate(-90);
    p.setFont(InterFont(18, QFont::Bold));
    p.setPen(QColor(255, 50, 50, 220));
    p.drawText(QRect(-100, 0, 200, 24), Qt::AlignCenter, tr("BLIND SPOT"));
    p.restore();
  }

  // Right side: right blinker + right blind spot
  if (right_blinker && right_blindspot) {
    QLinearGradient grad(w, 0, w - bar_w, 0);
    grad.setColorAt(0.0, QColor(255, 0, 0, alpha));
    grad.setColorAt(1.0, QColor(255, 0, 0, 0));
    p.setPen(Qt::NoPen);
    p.setBrush(grad);
    p.drawRect(w - bar_w, 0, bar_w, h);

    // "BLIND SPOT" text rotated vertically on right edge
    p.save();
    p.translate(w - bar_w - 4, h / 2);
    p.rotate(90);
    p.setFont(InterFont(18, QFont::Bold));
    p.setPen(QColor(255, 50, 50, 220));
    p.drawText(QRect(-100, 0, 200, 24), Qt::AlignCenter, tr("BLIND SPOT"));
    p.restore();
  }
}

void HudRenderer::drawSpeedLimit(QPainter &p, const QRect &surface_rect) {
  // MUTCD-style speed limit sign: white circle, red border, black number.
  // Positioned to the right of the set-speed box, vertically centred with it.
  // Works on both 7" (1024×600) and 9.3" (1600×600) displays — coordinates are
  // relative to the left edge of the camera widget, not the full screen width.

  const int radius = 28;
  const int cx = 193;   // right of set-speed box (~x=35+110+20+28)
  const int cy = 90;    // vertical centre of set-speed box (25 + 130/2)

  // White fill
  p.setPen(Qt::NoPen);
  p.setBrush(QColor(255, 255, 255));
  p.drawEllipse(QPoint(cx, cy), radius, radius);

  // Red border
  p.setPen(QPen(QColor(255, 0, 0), 4));
  p.setBrush(Qt::NoBrush);
  p.drawEllipse(QPoint(cx, cy), radius, radius);

  // Speed number in black
  QString limitStr = QString::number(static_cast<int>(nav_speed_limit));
  p.setPen(QColor(0, 0, 0));
  p.setFont(InterFont(52, QFont::Bold));
  QRect text_rect(cx - radius, cy - radius, radius * 2, radius * 2);
  p.drawText(text_rect, Qt::AlignCenter, limitStr);
}

void HudRenderer::drawNavInstruction(QPainter &p, const QRect &surface_rect) {
  // Turn-by-turn arrow + distance, top-left. No map/tiles on-device —
  // full route lives in the NavPilot phone app; this is just the next maneuver.
  const int pill_w = 180;
  const int pill_h = 130;
  const int x = 20;
  const int y = 20;
  QRect pill(x, y, pill_w, pill_h);

  p.setPen(Qt::NoPen);
  p.setBrush(QColor(0, 0, 0, 166));
  p.drawRoundedRect(pill, 20, 20);

  // Arrow rotation angle from Valhalla maneuver modifier (0deg = straight up)
  bool is_uturn = nav_maneuver_type == "uturn";
  bool is_arrive = nav_maneuver_type == "arrive";
  float angle = 0.0f;
  if (is_uturn) {
    angle = 180.0f;
  } else if (nav_maneuver_modifier == "slight right") {
    angle = 30.0f;
  } else if (nav_maneuver_modifier == "right") {
    angle = 90.0f;
  } else if (nav_maneuver_modifier == "sharp right") {
    angle = 135.0f;
  } else if (nav_maneuver_modifier == "slight left") {
    angle = -30.0f;
  } else if (nav_maneuver_modifier == "left") {
    angle = -90.0f;
  } else if (nav_maneuver_modifier == "sharp left") {
    angle = -135.0f;
  }

  const int arrow_cx = x + pill_w / 2;
  const int arrow_cy = y + 45;

  if (is_arrive) {
    // Destination pin instead of a directional arrow
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(0x00, 0xd8, 0x4a));
    p.drawEllipse(QPoint(arrow_cx, arrow_cy), 22, 22);
    p.setPen(QPen(QColor(255, 255, 255), 4));
    p.drawLine(arrow_cx, arrow_cy - 10, arrow_cx, arrow_cy + 10);
    p.drawLine(arrow_cx - 10, arrow_cy, arrow_cx + 10, arrow_cy);
  } else {
    p.save();
    p.translate(arrow_cx, arrow_cy);
    p.rotate(angle);
    QPainterPath arrow;
    arrow.moveTo(0, -30);
    arrow.lineTo(20, 0);
    arrow.lineTo(8, 0);
    arrow.lineTo(8, 26);
    arrow.lineTo(-8, 26);
    arrow.lineTo(-8, 0);
    arrow.lineTo(-20, 0);
    arrow.closeSubpath();
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(255, 255, 255));
    p.drawPath(arrow);
    p.restore();
  }

  // Distance to maneuver
  QString dist_str;
  if (is_metric) {
    dist_str = (nav_maneuver_distance_m >= 1000.0f)
      ? QString::number(nav_maneuver_distance_m / 1000.0f, 'f', 1) + " km"
      : QString::number(int(nav_maneuver_distance_m / 10.0f) * 10) + " m";
  } else {
    float feet = nav_maneuver_distance_m * 3.28084f;
    dist_str = (feet >= 1000.0f)
      ? QString::number(feet / 5280.0f, 'f', 1) + " mi"
      : QString::number(int(feet / 10.0f) * 10) + " ft";
  }
  p.setFont(InterFont(28, QFont::Bold));
  p.setPen(QColor(255, 255, 255));
  p.drawText(QRect(x, y + 75, pill_w, 30), Qt::AlignCenter, dist_str);

  // Street/instruction name (truncated to fit)
  if (!nav_primary_text.isEmpty()) {
    QFont font = InterFont(16);
    p.setFont(font);
    QFontMetrics fm(font);
    QString elided = fm.elidedText(nav_primary_text, Qt::ElideRight, pill_w - 16);
    p.setPen(QColor(255, 255, 255, 200));
    p.drawText(QRect(x, y + 105, pill_w, 22), Qt::AlignCenter, elided);
  }
}
