#include "selfdrive/ui/qt/onroad/onroad_home.h"

#include <QPainter>
#include <QStackedLayout>

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
    CameraWidget *arCam = new CameraWidget("camerad", VISION_STREAM_ROAD, this);
    split->insertWidget(0, arCam);
  }

  stacked_layout->addWidget(split_wrapper);

  alerts = new OnroadAlerts(this);
  alerts->setAttribute(Qt::WA_TransparentForMouseEvents, true);
  stacked_layout->addWidget(alerts);

  // setup stacking order
  alerts->raise();

  setAttribute(Qt::WA_OpaquePaintEvent);
  QObject::connect(uiState(), &UIState::uiUpdate, this, &OnroadWindow::updateState);
  QObject::connect(uiState(), &UIState::offroadTransition, this, &OnroadWindow::offroadTransition);
}

void OnroadWindow::updateNpIndicatorSideState(bool blinker_state, bool bsm_state, bool &show, bool &show_prev, int &count, QColor &color) {
  if (!blinker_state && !bsm_state) {
    show = false;
    count = 0;
  } else {
    count += 1;
  }
  if (bsm_state && blinker_state) {
    show = count % DP_INDICATOR_BLINK_RATE_FAST == 0? !show : show;
    color = DP_INDICATOR_COLOR_BSM;
  } else if (blinker_state) {
    show = count % DP_INDICATOR_BLINK_RATE_STD == 0? !show : show;
    color = DP_INDICATOR_COLOR_BLINKER;
  } else if (bsm_state) {
    show = true;
    color = DP_INDICATOR_COLOR_BSM;
  } else {
    show = false;
  }
}

void OnroadWindow::updateNpIndicatorStates(const UIState &s) {
  const auto cs = (*s.sm)["carState"].getCarState();
  updateNpIndicatorSideState(cs.getLeftBlinker(), cs.getLeftBlindspot(), np_indicator_show_left, np_indicator_show_left_prev, np_indicator_count_left, np_indicator_color_left);
  updateNpIndicatorSideState(cs.getRightBlinker(), cs.getRightBlindspot(), np_indicator_show_right, np_indicator_show_right_prev, np_indicator_count_right, np_indicator_color_right);
}

void OnroadWindow::updateState(const UIState &s) {
  if (!s.scene.started) {
    return;
  }

  np_indicator_show_left_prev = np_indicator_show_left;
  np_indicator_show_right_prev = np_indicator_show_right;
  updateNpIndicatorStates(s);
  bool indicator_states_changed = np_indicator_show_left != np_indicator_show_left_prev || np_indicator_show_right != np_indicator_show_right_prev;

  alerts->updateState(s);
  nvg->updateState(s);

  QColor bgColor = bg_colors[s.scene.alcc_active && s.status == STATUS_DISENGAGED ? STATUS_ALCC : s.status];
  if (bg != bgColor || indicator_states_changed) {
    // repaint border
    bg = bgColor;
    update();
  }
}

void OnroadWindow::offroadTransition(bool offroad) {
  alerts->clear();
}

void OnroadWindow::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.fillRect(rect(), QColor(bg.red(), bg.green(), bg.blue(), 180));
  if (np_indicator_show_left) p.fillRect(QRect(0, 0, width() * 0.2, height()), np_indicator_color_left);
  if (np_indicator_show_right) p.fillRect(QRect(width() * 0.8, 0, width() * 0.2, height()), np_indicator_color_right);
}
