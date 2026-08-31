#pragma once

#include <QPainter>
#include <QWidget>
#include "selfdrive/ui/ui.h"

/**
 * BEVWidget - Bird's Eye View visualization widget
 *
 * Top-down view of vehicle and surrounding objects.
 * Uses modelV2 lane lines, road edges, and radarState leads.
 *
 * Reused at two sizes: a small corner overlay in AnnotatedCameraWidget, and
 * full panel size as TelemetryPanel's default page. Takes no opinion on its
 * own size (callers size it, e.g. via setFixedSize()) or visibility --
 * isShowing() reports whether there's anything meaningful to show, and each
 * caller decides what to do with that (AnnotatedCameraWidget hides itself;
 * TelemetryPanel's page-switching already owns visibility, so it just draws
 * an empty grid when this is false).
 */
class BEVWidget : public QWidget {
  Q_OBJECT

public:
  explicit BEVWidget(QWidget *parent = nullptr);
  void updateState(const UIState &s);
  bool isShowing() const { return enabled && data_valid; }

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  void drawGrid(QPainter &p);
  void drawLaneLines(QPainter &p);
  void drawRoadEdges(QPainter &p);
  void drawVehicle(QPainter &p);
  void drawLeads(QPainter &p);
  void drawBlindSpots(QPainter &p);

  // Scale: pixels per meter
  float scale = 8.0f;
  
  // View center (vehicle position in widget coords)
  QPointF center;
  
  // Cached data
  struct LaneLine {
    std::vector<QPointF> points;
    float prob = 0.0f;
  };
  LaneLine lane_lines[4];
  
  struct RoadEdge {
    std::vector<QPointF> points;
    float std = 0.0f;
  };
  RoadEdge road_edges[2];
  
  struct Lead {
    QPointF pos;
    float dRel = 0.0f;
    float yRel = 0.0f;
    float vRel = 0.0f;
    bool status = false;
  };
  Lead leads[2];
  
  // Blind spot state
  int left_blind_spot = 0;   // 0=off, 1=caution, 2=warning
  int right_blind_spot = 0;
  
  bool enabled = false;
  bool data_valid = false;
};
