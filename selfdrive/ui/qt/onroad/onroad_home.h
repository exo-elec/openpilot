#pragma once

#include <QLabel>

#include "selfdrive/ui/qt/onroad/alerts.h"
#include "selfdrive/ui/qt/onroad/annotated_camera.h"
#include "selfdrive/ui/qt/widgets/overlay_camera.h"

class OnroadWindow : public QWidget {
  Q_OBJECT

public:
  OnroadWindow(QWidget* parent = 0);

protected:
  void resizeEvent(QResizeEvent *event) override;

private:
  void createOverlays();
  void updateOverlayGeometry();
  void updateOverlayVisibility(const UIState &s);
  void updatePairingOverlay();
  void paintEvent(QPaintEvent *event);
  void mousePressEvent(QMouseEvent* e) override;
  OnroadAlerts *alerts;
  AnnotatedCameraWidget *nvg;
  QColor bg = bg_colors[STATUS_DISENGAGED];
  QHBoxLayout* split;

  // Camera overlays (PIP)
  OverlayCameraWidget *rear_overlay_ = nullptr;
  OverlayCameraWidget *left_overlay_ = nullptr;
  OverlayCameraWidget *right_overlay_ = nullptr;

  // Bluetooth pairing PIN overlay
  QLabel *pairing_overlay_ = nullptr;

private slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);
};
