#pragma once

#include <memory>

#include <QImage>
#include <QWidget>

#include "msgq/visionipc/visionipc_client.h"
#include "selfdrive/ui/ui.h"

// Turn-by-turn panel around the map frames upstream's mapsd publishes (selfdrive/navigation), a Qt port of
// upstream openpilot's mici NavigationPanel (selfdrive/ui/mici/onroad/navigation.py, nav alpha #39066).
class NavigationPanel : public QWidget {
  Q_OBJECT

public:
  explicit NavigationPanel(QWidget *parent = nullptr);
  void updateState(const UIState &s);
  static bool wanted();  // NavigationEnabled and a destination set: the panel takes the right of the onroad view

private:
  void paintEvent(QPaintEvent *event) override;
  void receiveFrame();
  bool routing() const;
  bool mapReady() const;
  QString formatDistance(float meters) const;

  std::unique_ptr<VisionIpcClient> client;
  double last_connect = 0;
  double frame_time = 0;
  QImage map;
  bool is_metric = true;
  bool gps_fix = false;
  bool has_route = false;
  QString modifier, eta;
  float maneuver_distance = 0;
};
