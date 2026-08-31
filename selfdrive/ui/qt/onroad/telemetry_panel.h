#pragma once

#include <QMouseEvent>
#include <QStackedWidget>
#include <QWidget>

#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/onroad/bev_widget.h"

// Simple numeric readout page: speed, steering angle, lead distance/relative speed.
class TelemetryStatsPage : public QWidget {
  Q_OBJECT
public:
  explicit TelemetryStatsPage(QWidget *parent = nullptr);
  void updateState(const UIState &s);

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  bool is_metric = false;
  float vEgo = 0;
  float steeringAngleDeg = 0;
  bool leadOneValid = false;
  float leadOneDRel = 0;
  float leadOneVRel = 0;
};

// Right-side panel that only exists on ExoPilot 02M (RK3576), using the
// screen width beyond the ExoPilot 01M 1024x600 baseline (see
// getTelemetryPanelWidth() in qt_window.h). Swipe left/right to page
// through information; defaults to the existing BEVWidget top-down view
// (reused at full panel size, not the small 130x180 corner overlay
// instance in AnnotatedCameraWidget) rather than reimplementing it. This
// widget is laid out purely in the extra width -- it never touches Sidebar
// or OnroadWindow, so ExoPilot 01M (and anything not RK3576) is unaffected.
class TelemetryPanel : public QWidget {
  Q_OBJECT
public:
  explicit TelemetryPanel(QWidget *parent = nullptr);

public slots:
  void updateState(const UIState &s);

protected:
  void paintEvent(QPaintEvent *event) override;
  void mousePressEvent(QMouseEvent *event) override;
  void mouseReleaseEvent(QMouseEvent *event) override;

private:
  void goToPage(int index);

  QStackedWidget *pages;
  BEVWidget *bevPage;
  TelemetryStatsPage *statsPage;
  QPoint dragStart;
  bool dragging = false;
};
