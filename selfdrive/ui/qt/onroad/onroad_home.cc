#include "selfdrive/ui/qt/onroad/onroad_home.h"

#include <QPainter>
#include <QStackedLayout>

#include "common/params.h"

#include "selfdrive/ui/qt/util.h"

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
  stacked_layout->addWidget(pairing_overlay_);

  // setup stacking order
  alerts->raise();
  pairing_overlay_->raise();

  setAttribute(Qt::WA_OpaquePaintEvent);
  QObject::connect(uiState(), &UIState::uiUpdate, this, &OnroadWindow::updateState);
  QObject::connect(uiState(), &UIState::offroadTransition, this, &OnroadWindow::offroadTransition);
}

void OnroadWindow::createOverlays() {
  // Rear camera overlay (NV12 from uvcd)
  rear_overlay_ = new OverlayCameraWidget("uvcd", VISION_STREAM_REAR, this);
  rear_overlay_->setBorderColor(QColor(255, 255, 255, 220));
  rear_overlay_->setBorderWidth(3);
  rear_overlay_->setCornerRadius(12);
  rear_overlay_->hide();

  // Left side camera overlay (BGR from uvcd)
  left_overlay_ = new OverlayCameraWidget("uvcd", VISION_STREAM_SIDE_LEFT, this);
  left_overlay_->setBorderColor(QColor(0, 200, 255, 220));
  left_overlay_->setBorderWidth(3);
  left_overlay_->setCornerRadius(12);
  left_overlay_->hide();

  // Right side camera overlay (BGR from uvcd)
  right_overlay_ = new OverlayCameraWidget("uvcd", VISION_STREAM_SIDE_RIGHT, this);
  right_overlay_->setBorderColor(QColor(0, 200, 255, 220));
  right_overlay_->setBorderWidth(3);
  right_overlay_->setCornerRadius(12);
  right_overlay_->hide();
}

void OnroadWindow::resizeEvent(QResizeEvent *event) {
  QWidget::resizeEvent(event);
  updateOverlayGeometry();
}

void OnroadWindow::updateOverlayGeometry() {
  const int w = width();
  const int h = height();
  if (w <= 0 || h <= 0) return;

  // Side overlays: 28% width, 62% height, upper portion (avoid rear strip)
  const int side_w = static_cast<int>(w * 0.28);
  const int side_h = static_cast<int>(h * 0.62);
  left_overlay_->setGeometry(0, 0, side_w, side_h);
  right_overlay_->setGeometry(w - side_w, 0, side_w, side_h);

  // Rear overlay: full width, 35% height, bottom strip
  const int rear_h = static_cast<int>(h * 0.35);
  rear_overlay_->setGeometry(0, h - rear_h, w, rear_h);
}

void OnroadWindow::updateOverlayVisibility(const UIState &s) {
  const auto &sm = *(s.sm);
  if (!sm.updated("carState")) return;

  const auto &cs = sm["carState"].getCarState();
  bool in_reverse = cs.getGearShifter() == cereal::CarState::GearShifter::REVERSE;
  bool left_blinker = cs.getLeftBlinker();
  bool right_blinker = cs.getRightBlinker();

  // Rear overlay: visible in reverse gear
  bool show_rear = in_reverse;
  if (show_rear && !rear_overlay_->isVisible()) {
    rear_overlay_->show();
    rear_overlay_->start();
  } else if (!show_rear && rear_overlay_->isVisible()) {
    rear_overlay_->hide();
    rear_overlay_->stop();
  }

  // Left overlay: visible when left blinker active
  bool show_left = left_blinker;
  if (show_left && !left_overlay_->isVisible()) {
    left_overlay_->show();
    left_overlay_->start();
  } else if (!show_left && left_overlay_->isVisible()) {
    left_overlay_->hide();
    left_overlay_->stop();
  }

  // Right overlay: visible when right blinker active
  bool show_right = right_blinker;
  if (show_right && !right_overlay_->isVisible()) {
    right_overlay_->show();
    right_overlay_->start();
  } else if (!show_right && right_overlay_->isVisible()) {
    right_overlay_->hide();
    right_overlay_->stop();
  }

  // Stack active overlays above alerts so they remain visible
  if (show_rear) rear_overlay_->raise();
  if (show_left) left_overlay_->raise();
  if (show_right) right_overlay_->raise();
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
  auto params = Params();
  std::string pin = params.get("BluetoothPairingPin");
  bool active = params.get("BluetoothPairingActive") == "1";

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
