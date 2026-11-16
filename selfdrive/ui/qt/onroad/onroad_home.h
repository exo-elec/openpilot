#pragma once

#include "selfdrive/ui/qt/onroad/navigation.h"

#include "selfdrive/ui/qt/onroad/alerts.h"
#include "selfdrive/ui/qt/onroad/annotated_camera.h"

class OnroadWindow : public QWidget {
  Q_OBJECT

public:
  OnroadWindow(QWidget* parent = 0);

private:
  void paintEvent(QPaintEvent *event);
  OnroadAlerts *alerts;
  AnnotatedCameraWidget *nvg;
  QColor bg = bg_colors[STATUS_DISENGAGED];
  QHBoxLayout* split;
  NavigationPanel *navigation;  // upstream nav alpha: right of the camera while NavigationEnabled and a destination is set
  double nav_checked = 0;

private slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);
};
