#pragma once

#include <QPainter>
#include "selfdrive/ui/ui.h"

class HudRenderer : public QObject {
  Q_OBJECT

public:
  HudRenderer();
  void updateState(const UIState &s);
  void draw(QPainter &p, const QRect &surface_rect);

private:
  void drawSetSpeed(QPainter &p, const QRect &surface_rect);
  void drawCurrentSpeed(QPainter &p, const QRect &surface_rect);
  void drawSpeedLimit(QPainter &p, const QRect &surface_rect);
  void drawDriverStatus(QPainter &p, const QRect &surface_rect);
  void drawBlindSpotOverlay(QPainter &p, const QRect &surface_rect);
  void drawNavInstruction(QPainter &p, const QRect &surface_rect);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);

  float speed = 0;
  float set_speed = 0;
  bool is_cruise_set = false;
  bool is_cruise_available = true;
  bool is_metric = false;
  bool v_ego_cluster_seen = false;
  int status = STATUS_DISENGAGED;

  // Speed limit display (0 = hide sign)
  float nav_speed_limit = 0;  // km/h or mph depending on is_metric

  // EOP: Driver pose status indicator
  bool driver_detected = false;
  bool driver_forward = false;
  float attention_prob = 1.0f;
  bool show_driver_status = false;
  float driver_x = 0.5f;
  float driver_y = 0.5f;
  float driver_yaw = 0.0f;
  float driver_pitch = 0.0f;
  // BSD / blind spot state
  bool left_blinker = false;
  bool right_blinker = false;
  bool left_blindspot = false;
  bool right_blindspot = false;
  float bsd_pulse = 0.0f;

  // Turn-by-turn instruction overlay (NavPilot destination sync + local
  // Valhalla routing via navd). Device shows a maneuver arrow + distance
  // only — full map/route stays on the phone.
  bool show_nav_instruction = false;
  QString nav_maneuver_type;
  QString nav_maneuver_modifier;
  QString nav_primary_text;
  float nav_maneuver_distance_m = 0.0f;
};
