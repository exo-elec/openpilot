#pragma once

#include <QWidget>

#include "selfdrive/ui/ui.h"

/**
 * BlindSpotIndicator - edge blind-spot warning
 *
 * Vertical bands down the left and right edges of the camera view, fading
 * inward. Amber at caution severity, red at warning severity, with the
 * warning level breathing rather than blinking.
 *
 * Replaces the 130x180 bottom-right BEV mini-map that used to carry this
 * alongside lane lines, road edges and leads -- all three of which the main
 * camera view already draws directly. An edge band reads in peripheral
 * vision, which is the entire point of a blind-spot warning: a small
 * top-down map has to be looked at and interpreted, exactly when the
 * driver's eyes should be going to the mirror instead. Same approach
 * sunnypilot takes with its blind-spot barrier.
 *
 * Sized by the caller to cover the camera view (AnnotatedCameraWidget hands
 * it the full rect on resize). isShowing() reports whether either side is
 * active; the caller owns visibility.
 */
class BlindSpotIndicator : public QWidget {
  Q_OBJECT

public:
  explicit BlindSpotIndicator(QWidget *parent = nullptr);
  void updateState(const UIState &s);
  bool isShowing() const { return enabled && (left_severity > 0 || right_severity > 0); }

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  void drawEdge(QPainter &p, bool left, int severity);

  // 0 = clear, 1 = caution, 2 = warning -- the Int8 severity carried by
  // controlsState.leftBlindSpot/rightBlindSpot (cereal/log.capnp).
  int left_severity = 0;
  int right_severity = 0;
  bool enabled = false;

  // Warning-level pulse, advanced once per updateState() rather than from a
  // QTimer: updateState() is already driven at the UI frame rate by
  // AnnotatedCameraWidget, so a timer would be a second clock, and a
  // resource to own and tear down, for nothing.
  int pulse_frame = 0;
};
