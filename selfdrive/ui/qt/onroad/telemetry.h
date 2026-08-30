#pragma once

#include <QMouseEvent>
#include <QStackedWidget>
#include <QWidget>

#include "selfdrive/ui/ui.h"

// Top-down "gridd" style page: ego car centered, the front-radar lead plotted
// by distance, and the existing left/right BSM booleans shown as edge chips.
// There is no corner-radar or side-camera message in this tree yet (see
// nagaspilot/docs/COMMA3_MONOD_GRIDD_STUDY.md) so this page only ever draws
// data that genuinely exists today -- it does not invent a live corner feed.
class TelemetryGridPage : public QWidget {
  Q_OBJECT
public:
  explicit TelemetryGridPage(QWidget *parent = nullptr);
  void updateState(const UIState &s);

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  bool leftBlindspot = false;
  bool rightBlindspot = false;
  bool leadOneValid = false;
  float leadOneDRel = 0;
  float leadOneVRel = 0;
};

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

// Right-side panel that only exists on screens wider than the baseline
// comma-three panel (see getTelemetryPanelWidth() in qt_window.h). Swipe
// left/right to page through information; defaults to the gridd top-down
// page. This widget is laid out purely in the extra width beyond that
// baseline -- it never touches Sidebar or OnroadWindow, so device 01 (and
// any screen at or under the baseline width) is completely unaffected.
class TelemetryWidget : public QWidget {
  Q_OBJECT
public:
  explicit TelemetryWidget(QWidget *parent = nullptr);

public slots:
  void updateState(const UIState &s);

protected:
  void paintEvent(QPaintEvent *event) override;
  void mousePressEvent(QMouseEvent *event) override;
  void mouseReleaseEvent(QMouseEvent *event) override;

private:
  void goToPage(int index);

  QStackedWidget *pages;
  TelemetryGridPage *gridPage;
  TelemetryStatsPage *statsPage;
  QPoint dragStart;
  bool dragging = false;
};
