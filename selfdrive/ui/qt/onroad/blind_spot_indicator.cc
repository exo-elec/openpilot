#include "selfdrive/ui/qt/onroad/blind_spot_indicator.h"

#include <algorithm>
#include <cmath>

#include <QLinearGradient>
#include <QPainter>

#include "common/params.h"

namespace {

// Width of the fade band at each edge, in px, sized for the 1024x600 panel:
// wide enough to register in peripheral vision, narrow enough to leave the
// lane the driver is actually watching uncovered. Clamped against the real
// width in drawEdge() so it can never eat the middle of a narrow view.
constexpr int EDGE_WIDTH = 90;

// Frames per pulse cycle at warning severity. updateState() runs at the UI
// frame rate (~20 Hz), so this is roughly a 1.2 s cycle.
constexpr int PULSE_PERIOD_FRAMES = 24;

constexpr int CAUTION_ALPHA = 140;
constexpr int WARNING_ALPHA_MIN = 130;
constexpr int WARNING_ALPHA_MAX = 235;

const QColor CAUTION_COLOR(255, 194, 0);
const QColor WARNING_COLOR(255, 60, 60);

}  // namespace

BlindSpotSeverity getBlindSpotSeverity(const UIState &s) {
  const SubMaster &sm = *(s.sm);

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

  BlindSpotSeverity out;
  out.left = std::max(ctrl_left, car_left);
  out.right = std::max(ctrl_right, car_right);
  return out;
}

QColor blindSpotColor(int severity) {
  return severity >= 2 ? WARNING_COLOR : CAUTION_COLOR;
}

BlindSpotIndicator::BlindSpotIndicator(QWidget *parent) : QWidget(parent) {
  // Covers the camera feed, so it must never take input -- the driving
  // screen's own tap handling has to keep reaching what's underneath (see
  // onroad_home.cc).
  setAttribute(Qt::WA_TransparentForMouseEvents);
  setAttribute(Qt::WA_NoSystemBackground);
  setAttribute(Qt::WA_TranslucentBackground);
}

void BlindSpotIndicator::updateState(const UIState &s) {
  enabled = Params().getBool("EOPBlindSpotIndicator");
  if (!enabled) {
    left_severity = right_severity = 0;
    update();
    return;
  }

  const BlindSpotSeverity sev = getBlindSpotSeverity(s);
  left_severity = sev.left;
  right_severity = sev.right;

  pulse_frame = (pulse_frame + 1) % PULSE_PERIOD_FRAMES;

  update();
}

void BlindSpotIndicator::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);

  if (left_severity > 0) {
    drawEdge(p, true, left_severity);
  }
  if (right_severity > 0) {
    drawEdge(p, false, right_severity);
  }
}

void BlindSpotIndicator::drawEdge(QPainter &p, bool left, int severity) {
  QColor color = blindSpotColor(severity);
  int alpha = CAUTION_ALPHA;

  if (severity >= 2) {
    // Raised cosine over the pulse period, so the warning breathes rather
    // than blinking. A hard on/off this far into peripheral vision reads as
    // a distraction; a smooth ramp still draws the eye without startling.
    const float phase = static_cast<float>(pulse_frame) / PULSE_PERIOD_FRAMES;
    const float wave = 0.5f * (1.0f - std::cos(2.0f * static_cast<float>(M_PI) * phase));
    alpha = WARNING_ALPHA_MIN + static_cast<int>((WARNING_ALPHA_MAX - WARNING_ALPHA_MIN) * wave);
  }

  const int w = std::min(EDGE_WIDTH, width() / 4);
  const QRect band = left ? QRect(0, 0, w, height())
                          : QRect(width() - w, 0, w, height());

  QColor at_edge = color;
  at_edge.setAlpha(alpha);
  QColor at_inner = color;
  at_inner.setAlpha(0);

  // Gradient runs from the screen edge inward, whichever side this is.
  QLinearGradient g(left ? band.left() : band.right(), 0,
                    left ? band.right() : band.left(), 0);
  g.setColorAt(0.0, at_edge);
  g.setColorAt(1.0, at_inner);

  p.fillRect(band, g);
}
