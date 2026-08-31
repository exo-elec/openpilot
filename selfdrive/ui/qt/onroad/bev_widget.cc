#include "selfdrive/ui/qt/onroad/bev_widget.h"

#include <cmath>
#include <algorithm>

BEVWidget::BEVWidget(QWidget *parent) : QWidget(parent) {
  // No default size or mouse-transparency here -- both are presentation
  // choices specific to how each caller places this widget (a small
  // click-through corner overlay vs. a full telemetry-panel page that
  // needs mouse events for swipe gestures), not something this widget
  // should assume. See callers: AnnotatedCameraWidget, TelemetryPanel.
}

void BEVWidget::updateState(const UIState &s) {
  const SubMaster &sm = *(s.sm);
  
  enabled = Params().getBool("EOPBEVWidgetEnabled");
  if (!enabled) {
    data_valid = false;
    update();  // repaint to reflect the now-invalid state -- this widget no
               // longer self-hides, so a caller showing it needs a fresh
               // "nothing to show" paint, not stale content left on screen.
    return;
  }

  // Check if model data is available
  if (sm.rcv_frame("modelV2") < s.scene.started_frame ||
      sm.rcv_frame("radarState") < s.scene.started_frame) {
    data_valid = false;
    update();
    return;
  }
  
  data_valid = true;
  
  const auto &model = sm["modelV2"].getModelV2();
  const auto &radar = sm["radarState"].getRadarState();
  
  // Update lane lines
  const auto &lane_lines_data = model.getLaneLines();
  const auto &lane_probs = model.getLaneLineProbs();
  for (int i = 0; i < 4 && i < lane_lines_data.size(); ++i) {
    lane_lines[i].points.clear();
    lane_lines[i].prob = lane_probs[i];
    const auto &line = lane_lines_data[i];
    const auto &x = line.getX();
    const auto &y = line.getY();
    for (int j = 0; j < x.size() && j < y.size(); ++j) {
      // Convert from car space (x=forward, y=left) to widget coords
      // Widget: x=right, y=up (forward)
      float wx = center.x() - y[j] * scale;
      float wy = center.y() - x[j] * scale;
      lane_lines[i].points.push_back(QPointF(wx, wy));
    }
  }
  
  // Update road edges
  const auto &edges_data = model.getRoadEdges();
  const auto &edge_stds = model.getRoadEdgeStds();
  for (int i = 0; i < 2 && i < edges_data.size(); ++i) {
    road_edges[i].points.clear();
    road_edges[i].std = edge_stds[i];
    const auto &edge = edges_data[i];
    const auto &x = edge.getX();
    const auto &y = edge.getY();
    for (int j = 0; j < x.size() && j < y.size(); ++j) {
      float wx = center.x() - y[j] * scale;
      float wy = center.y() - x[j] * scale;
      road_edges[i].points.push_back(QPointF(wx, wy));
    }
  }
  
  // Update leads
  const auto &lead_one = radar.getLeadOne();
  leads[0].status = lead_one.getStatus();
  leads[0].dRel = lead_one.getDRel();
  leads[0].yRel = lead_one.getYRel();
  leads[0].vRel = lead_one.getVRel();
  leads[0].pos = QPointF(center.x() - leads[0].yRel * scale,
                         center.y() - leads[0].dRel * scale);
  
  const auto &lead_two = radar.getLeadTwo();
  leads[1].status = lead_two.getStatus();
  leads[1].dRel = lead_two.getDRel();
  leads[1].yRel = lead_two.getYRel();
  leads[1].vRel = lead_two.getVRel();
  leads[1].pos = QPointF(center.x() - leads[1].yRel * scale,
                         center.y() - leads[1].dRel * scale);
  
  // Update blind spot state from controlsState + carState (fused).
  // leftBlindSpot/rightBlindSpot are Int8 severity (0=off, 1=caution,
  // 2=warning, per cereal/log.capnp) -- preserve that, not just presence,
  // so drawBlindSpots()'s severity-2 flashing-warning-triangle branch can
  // actually trigger. carState's leftBlindspot/rightBlindspot are plain
  // bools with no severity of their own, so fusing them in can only raise
  // the severity to at least "caution", never suppress a real "warning"
  // from controlsState.
  //
  // Read each source into a local first, defaulting to 0 when that source
  // isn't valid right now, rather than assigning straight into
  // left_blind_spot/right_blind_spot conditionally -- the latter would let
  // a stale-but-still-severity-2 value from a past controlsState drop
  // survive indefinitely (std::max can only raise a value, never lower it)
  // if controlsState alone went stale while carState/modelV2/radarState
  // stayed valid, leaving a phantom warning-triangle on screen with no way
  // to clear itself.
  int ctrl_left = 0, ctrl_right = 0;
  if (sm.valid("controlsState")) {
    const auto &ctrl = sm["controlsState"].getControlsState();
    ctrl_left = ctrl.getLeftBlindSpot();
    ctrl_right = ctrl.getRightBlindSpot();
  }
  int car_left = 0, car_right = 0;
  if (sm.valid("carState")) {
    const auto &cs = sm["carState"].getCarState();
    car_left = cs.getLeftBlindspot() ? 1 : 0;
    car_right = cs.getRightBlindspot() ? 1 : 0;
  }
  left_blind_spot = std::max(ctrl_left, car_left);
  right_blind_spot = std::max(ctrl_right, car_right);
  
  // Visibility is the caller's decision now (see isShowing() in the
  // header) -- this widget only draws itself.
  update();
}

void BEVWidget::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);
  
  // Update center
  center = QPointF(width() / 2.0f, height() - 40.0f);
  
  // Background
  p.fillRect(rect(), QColor(0, 0, 0, 180));
  
  // Draw grid
  drawGrid(p);

  // The rest reflects live model/radar data -- draw it only while that data
  // is actually valid. Previously this widget self-hid via setVisible()
  // whenever !data_valid, which masked that the point/lead arrays below are
  // never cleared on the invalid path (they just hold their last values);
  // now that a caller can keep it visible while disabled/invalid (e.g.
  // TelemetryPanel, mid-swipe or with EOPBEVWidgetEnabled off), that would
  // otherwise paint stale lane lines/leads instead of an empty grid.
  if (data_valid) {
    drawRoadEdges(p);
    drawLaneLines(p);
    drawLeads(p);
    drawBlindSpots(p);
  }

  // Draw vehicle
  drawVehicle(p);
  
  // Border
  p.setPen(QPen(QColor(255, 255, 255, 100), 1));
  p.setBrush(Qt::NoBrush);
  p.drawRoundedRect(rect().adjusted(1, 1, -1, -1), 8, 8);
}

void BEVWidget::drawGrid(QPainter &p) {
  p.setPen(QPen(QColor(255, 255, 255, 30), 1));
  
  // Draw distance circles (every 10m)
  for (int dist_m = 10; dist_m <= 50; dist_m += 10) {
    float radius = dist_m * scale;
    float cy = center.y() - radius;
    if (cy > 0) {
      p.drawEllipse(QPointF(center.x(), center.y()), radius, radius);
    }
  }
  
  // Draw center line
  p.setPen(QPen(QColor(255, 255, 255, 50), 1, Qt::DashLine));
  p.drawLine(QPointF(center.x(), 0), QPointF(center.x(), height()));
}

void BEVWidget::drawRoadEdges(QPainter &p) {
  for (int i = 0; i < 2; ++i) {
    if (road_edges[i].points.size() < 2) continue;
    
    QPolygonF poly;
    for (const auto &pt : road_edges[i].points) {
      poly.append(pt);
    }
    
    // Color: red-ish for edges, alpha based on confidence
    float alpha = std::clamp(1.0f - road_edges[i].std, 0.3f, 0.8f);
    p.setPen(QPen(QColor(255, 80, 80, int(alpha * 255)), 2));
    p.drawPolyline(poly);
  }
}

void BEVWidget::drawLaneLines(QPainter &p) {
  for (int i = 0; i < 4; ++i) {
    if (lane_lines[i].points.size() < 2) continue;
    
    QPolygonF poly;
    for (const auto &pt : lane_lines[i].points) {
      poly.append(pt);
    }
    
    // Color based on lane line index
    QColor color;
    if (i == 0) color = QColor(255, 200, 50);   // Left outer
    else if (i == 1) color = QColor(255, 255, 100); // Left inner
    else if (i == 2) color = QColor(255, 255, 100); // Right inner
    else color = QColor(255, 200, 50);              // Right outer
    
    float alpha = std::clamp(lane_lines[i].prob, 0.3f, 0.9f);
    color.setAlpha(int(alpha * 255));
    
    p.setPen(QPen(color, 2));
    p.drawPolyline(poly);
  }
}

void BEVWidget::drawVehicle(QPainter &p) {
  // Draw ego vehicle as a simple rectangle
  float car_w = 1.8f * scale;  // 1.8m wide
  float car_l = 4.5f * scale;  // 4.5m long
  
  QRectF car_rect(center.x() - car_w / 2, center.y() - car_l / 2, car_w, car_l);
  
  // Body
  p.setPen(QPen(QColor(0, 200, 255), 2));
  p.setBrush(QColor(0, 200, 255, 100));
  p.drawRoundedRect(car_rect, 4, 4);
  
  // Direction indicator (small triangle at front)
  QPointF front_triangle[3] = {
    QPointF(center.x(), center.y() - car_l / 2 - 8),
    QPointF(center.x() - 6, center.y() - car_l / 2),
    QPointF(center.x() + 6, center.y() - car_l / 2),
  };
  p.setBrush(QColor(0, 200, 255));
  p.drawPolygon(front_triangle, 3);
}

void BEVWidget::drawLeads(QPainter &p) {
  for (int i = 0; i < 2; ++i) {
    if (!leads[i].status) continue;
    
    const auto &lead = leads[i];
    
    // Lead vehicle size (assume 1.8m x 4.5m)
    float lead_w = 1.8f * scale;
    float lead_l = 4.5f * scale;
    
    QRectF lead_rect(lead.pos.x() - lead_w / 2, lead.pos.y() - lead_l / 2, lead_w, lead_l);
    
    // Color based on distance
    QColor color;
    if (lead.dRel < 10.0f) {
      color = QColor(255, 50, 50);  // Red - close
    } else if (lead.dRel < 30.0f) {
      color = QColor(255, 200, 50); // Yellow - medium
    } else {
      color = QColor(50, 255, 50);  // Green - far
    }
    
    p.setPen(QPen(color, 2));
    p.setBrush(QColor(color.red(), color.green(), color.blue(), 80));
    p.drawRoundedRect(lead_rect, 3, 3);
    
    // Relative speed indicator
    if (std::abs(lead.vRel) > 1.0f) {
      QString speed_text = QString::number(int(lead.vRel * 3.6)) + " km/h";
      p.setFont(QFont("Inter", 8));
      p.setPen(QPen(Qt::white));
      p.drawText(lead_rect.topLeft() + QPointF(2, -4), speed_text);
    }
  }
}

void BEVWidget::drawBlindSpots(QPainter &p) {
  // Left blind spot indicator
  if (left_blind_spot > 0) {
    QColor color = (left_blind_spot == 2) ? QColor(255, 50, 50) : QColor(255, 200, 50);
    p.setPen(QPen(color, 3));
    p.setBrush(Qt::NoBrush);
    
    // Draw arc on left side
    QRectF left_zone(center.x() - 55, center.y() - 40, 55, 80);
    p.drawArc(left_zone, 90 * 16, 90 * 16);  // Left side arc
    
    if (left_blind_spot == 2) {
      // Flashing warning - draw filled triangle
      QPointF warning_triangle[3] = {
        QPointF(center.x() - 60, center.y() - 30),
        QPointF(center.x() - 45, center.y() - 20),
        QPointF(center.x() - 60, center.y() - 10),
      };
      p.setBrush(color);
      p.drawPolygon(warning_triangle, 3);
    }
  }
  
  // Right blind spot indicator
  if (right_blind_spot > 0) {
    QColor color = (right_blind_spot == 2) ? QColor(255, 50, 50) : QColor(255, 200, 50);
    p.setPen(QPen(color, 3));
    p.setBrush(Qt::NoBrush);
    
    // Draw arc on right side
    QRectF right_zone(center.x(), center.y() - 40, 55, 80);
    p.drawArc(right_zone, 0, 90 * 16);  // Right side arc
    
    if (right_blind_spot == 2) {
      QPointF warning_triangle[3] = {
        QPointF(center.x() + 60, center.y() - 30),
        QPointF(center.x() + 45, center.y() - 20),
        QPointF(center.x() + 60, center.y() - 10),
      };
      p.setBrush(color);
      p.drawPolygon(warning_triangle, 3);
    }
  }
}
