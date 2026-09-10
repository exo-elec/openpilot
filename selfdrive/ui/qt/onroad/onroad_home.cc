#include "selfdrive/ui/qt/onroad/onroad_home.h"

#include <QPainter>
#include <QStackedLayout>

#include "common/params.h"

#include "selfdrive/ui/qt/util.h"

namespace {
// Resting border for the side-camera PIPs. Replaced by the blind-spot
// colour while that side is occupied -- see updateOverlayVisibility().
const QColor SIDE_OVERLAY_BORDER(0, 200, 255, 220);
constexpr int SIDE_OVERLAY_BORDER_PX = 3;
constexpr int BLIND_SPOT_BORDER_PX = 12;
}  // namespace


OnroadWindow::OnroadWindow(QWidget *parent) : QWidget(parent) {
  QVBoxLayout *main_layout  = new QVBoxLayout(this);
  main_layout->setMargin(UI_BORDER_SIZE);
  QStackedLayout *stacked_layout = new QStackedLayout;
  stacked_layout->setStackingMode(QStackedLayout::StackAll);
  main_layout->addLayout(stacked_layout);

  nvg = new AnnotatedCameraWidget(VISION_STREAM_ROAD, this);

  QWidget * split_wrapper = new QWidget;
  split = new QHBoxLayout(split_wrapper);
  split->setContentsMargins(0, 0, 0, 0);
  split->setSpacing(0);
  split->addWidget(nvg);

  if (getenv("DUAL_CAMERA_VIEW")) {
    CameraWidget *arCam = new CameraWidget("v4l2d", VISION_STREAM_ROAD, this);
    split->insertWidget(0, arCam);
  }

  stacked_layout->addWidget(split_wrapper);

  // Camera PIP overlays (reverse / turn-signal)
  createOverlays();
  stacked_layout->addWidget(rear_overlay_);
  stacked_layout->addWidget(left_overlay_);
  stacked_layout->addWidget(right_overlay_);
  updateOverlayGeometry();

  alerts = new OnroadAlerts(this);
  alerts->setAttribute(Qt::WA_TransparentForMouseEvents, true);
  stacked_layout->addWidget(alerts);

  // Pairing PIN overlay (top-right corner)
  pairing_overlay_ = new QLabel(this);
  pairing_overlay_->setAlignment(Qt::AlignCenter);
  pairing_overlay_->setStyleSheet(R"(
    QLabel {
      color: #ffcc00;
      background-color: rgba(0, 0, 0, 180);
      border-radius: 12px;
      padding: 12px 24px;
      font-size: 32px;
      font-weight: bold;
      font-family: Inter;
    }
  )");
  pairing_overlay_->hide();
  // Same reasoning as the camera overlays above -- this is a status label,
  // not an interactive control, so it shouldn't block clicks to whatever's
  // underneath (e.g. ExperimentalButton) while a pairing PIN is shown.
  pairing_overlay_->setAttribute(Qt::WA_TransparentForMouseEvents);
  stacked_layout->addWidget(pairing_overlay_);

  // setup stacking order
  alerts->raise();
  pairing_overlay_->raise();

  setAttribute(Qt::WA_OpaquePaintEvent);
  QObject::connect(uiState(), &UIState::uiUpdate, this, &OnroadWindow::updateState);
  QObject::connect(uiState(), &UIState::offroadTransition, this, &OnroadWindow::offroadTransition);

  pairing_watch_ = new ParamWatcher(this);
  pairing_watch_->addParam("BluetoothPairingPin");
  pairing_watch_->addParam("BluetoothPairingActive");
  QObject::connect(pairing_watch_, &ParamWatcher::paramChanged, [=](const QString &, const QString &) {
    refreshPairingCache();
  });
  refreshPairingCache();
}

void OnroadWindow::refreshPairingCache() {
  auto params = Params();
  cached_pairing_pin_ = params.get("BluetoothPairingPin");
  cached_pairing_active_ = params.get("BluetoothPairingActive") == "1";
}

void OnroadWindow::createOverlays() {
  // Rear camera overlay (NV12 from uvcd)
  rear_overlay_ = new OverlayCameraWidget("uvcd", VISION_STREAM_REAR, this);
  rear_overlay_->setBorderColor(QColor(255, 255, 255, 220));
  rear_overlay_->setSourceEdge(OverlayCameraWidget::SourceEdge::Bottom);
  rear_overlay_->setBorderWidth(3);
  rear_overlay_->setCornerRadius(12);
  rear_overlay_->hide();
  // Purely informational PIP, same as `alerts` below -- let clicks pass
  // through to whatever's underneath (e.g. ExperimentalButton)
  // instead of this overlay silently swallowing them while shown.
  rear_overlay_->setAttribute(Qt::WA_TransparentForMouseEvents);

  // Left side camera overlay (BGR from uvcd)
  left_overlay_ = new OverlayCameraWidget("uvcd", VISION_STREAM_SIDE_LEFT, this);
  left_overlay_->setBorderColor(SIDE_OVERLAY_BORDER);
  left_overlay_->setSourceEdge(OverlayCameraWidget::SourceEdge::Left);
  left_overlay_->setBorderWidth(3);
  left_overlay_->setCornerRadius(12);
  left_overlay_->hide();
  left_overlay_->setAttribute(Qt::WA_TransparentForMouseEvents);

  // Right side camera overlay (BGR from uvcd)
  right_overlay_ = new OverlayCameraWidget("uvcd", VISION_STREAM_SIDE_RIGHT, this);
  right_overlay_->setBorderColor(SIDE_OVERLAY_BORDER);
  right_overlay_->setSourceEdge(OverlayCameraWidget::SourceEdge::Right);
  right_overlay_->setBorderWidth(3);
  right_overlay_->setCornerRadius(12);
  right_overlay_->hide();
  right_overlay_->setAttribute(Qt::WA_TransparentForMouseEvents);
}

void OnroadWindow::resizeEvent(QResizeEvent *event) {
  QWidget::resizeEvent(event);
  updateOverlayGeometry();
}

void OnroadWindow::updateOverlayGeometry() {
  const int w = width();
  const int h = height();
  if (w <= 0 || h <= 0) return;

  // All three take the whole screen. When the driver signals, the side
  // camera is the thing to look at -- the old 28% corner tile made them
  // squint at the one image that mattered. Only one is ever up at a time
  // (see updateOverlayVisibility): hazards show no camera at all, and
  // reverse outranks a blinker.
  const QRect full(0, 0, w, h);
  left_overlay_->setGeometry(full);
  right_overlay_->setGeometry(full);
  rear_overlay_->setGeometry(full);
}

void OnroadWindow::updateOverlayVisibility(const UIState &s) {
  const auto &sm = *(s.sm);
  if (!sm.updated("carState")) return;

  const auto &cs = sm["carState"].getCarState();
  bool in_reverse = cs.getGearShifter() == cereal::CarState::GearShifter::REVERSE;
  bool left_blinker = cs.getLeftBlinker();
  bool right_blinker = cs.getRightBlinker();

  // Both blinkers means hazards, not an intent to move sideways -- leave the
  // road view alone. Only a single blinker opens a side camera.
  bool hazards = left_blinker && right_blinker;

  // Rear overlay: visible in reverse gear
  bool show_rear = in_reverse;
  if (show_rear && !rear_overlay_->isVisible()) {
    rear_overlay_->show();
    rear_overlay_->start();
  } else if (!show_rear && rear_overlay_->isVisible()) {
    rear_overlay_->hide();
    rear_overlay_->stop();
  }

  // Left overlay: single left blinker, and not while reversing -- the rear
  // camera is the one that matters then.
  bool show_left = left_blinker && !hazards && !in_reverse;
  if (show_left && !left_overlay_->isVisible()) {
    left_overlay_->show();
    left_overlay_->start();
  } else if (!show_left && left_overlay_->isVisible()) {
    left_overlay_->hide();
    left_overlay_->stop();
  }

  // Right overlay: single right blinker, same exclusions as left.
  bool show_right = right_blinker && !hazards && !in_reverse;
  if (show_right && !right_overlay_->isVisible()) {
    right_overlay_->show();
    right_overlay_->start();
  } else if (!show_right && right_overlay_->isVisible()) {
    right_overlay_->hide();
    right_overlay_->stop();
  }

  // Blind spot recolours the side overlay's border. BlindSpotIndicator's
  // edge bands live inside nvg, and a full-screen side overlay covers nvg
  // completely -- so exactly when the driver signals toward a car that is
  // already alongside, the bands are hidden entirely. Putting the warning on
  // the overlay's own border keeps it on the image being looked at, instead
  // of leaving no signal at all at the worst possible moment. Severity comes
  // from the same shared getBlindSpotSeverity(), so the bands and the border
  // can never disagree.
  const BlindSpotSeverity bs = getBlindSpotSeverity(s);
  left_overlay_->setBorderColor(bs.left > 0 ? blindSpotColor(bs.left) : SIDE_OVERLAY_BORDER);
  right_overlay_->setBorderColor(bs.right > 0 ? blindSpotColor(bs.right) : SIDE_OVERLAY_BORDER);
  // Full screen turns a 3px frame into a thin line around a large image, so
  // widen it when it is carrying a warning rather than just identifying the
  // camera.
  left_overlay_->setBorderWidth(bs.left > 0 ? BLIND_SPOT_BORDER_PX : SIDE_OVERLAY_BORDER_PX);
  right_overlay_->setBorderWidth(bs.right > 0 ? BLIND_SPOT_BORDER_PX : SIDE_OVERLAY_BORDER_PX);

  // Sides first, then rear, so reverse wins if both somehow apply; alerts
  // stay above every camera -- a full-screen image must never bury one.
  if (show_left) left_overlay_->raise();
  if (show_right) right_overlay_->raise();
  if (show_rear) rear_overlay_->raise();
  alerts->raise();
}

void OnroadWindow::mousePressEvent(QMouseEvent* e) {
  QWidget::mousePressEvent(e);
}

void OnroadWindow::updateState(const UIState &s) {
  if (!s.scene.started) {
    return;
  }

  alerts->updateState(s);
  nvg->updateState(s);
  updateOverlayVisibility(s);
  updatePairingOverlay();

  QColor bgColor = bg_colors[s.scene.alcc_active && s.status == STATUS_DISENGAGED ? STATUS_ALCC : s.status];
  if (bg != bgColor) {
    // repaint border
    bg = bgColor;
    update();
  }
}

void OnroadWindow::updatePairingOverlay() {
  const std::string &pin = cached_pairing_pin_;
  bool active = cached_pairing_active_;

  if (active && !pin.empty()) {
    pairing_overlay_->setText(QString("PIN: %1").arg(QString::fromStdString(pin)));
    pairing_overlay_->adjustSize();
    // Position in top-right corner with padding
    int x = width() - pairing_overlay_->width() - 30;
    int y = 30;
    pairing_overlay_->move(x, y);
    pairing_overlay_->show();
    pairing_overlay_->raise();
  } else {
    pairing_overlay_->hide();
  }
}

void OnroadWindow::offroadTransition(bool offroad) {
  alerts->clear();
}

void OnroadWindow::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.fillRect(rect(), QColor(bg.red(), bg.green(), bg.blue(), 255));
}
