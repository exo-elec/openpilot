#pragma once

#include <QLabel>

#include "selfdrive/ui/qt/onroad/alerts.h"
#include "selfdrive/ui/qt/onroad/annotated_camera.h"
#include "selfdrive/ui/qt/util.h"
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
  // Cached via ParamWatcher instead of a synchronous Params().get() on
  // every updateState() tick (UI_FREQ = 20 Hz, the entire onroad session)
  // -- see sidebar.cc's ble_watch for the same pattern.
  ParamWatcher *pairing_watch_ = nullptr;
  std::string cached_pairing_pin_;
  bool cached_pairing_active_ = false;
  void refreshPairingCache();

private slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);
};
