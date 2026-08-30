#include "selfdrive/ui/qt/onroad/telemetry.h"

#include <algorithm>
#include <cmath>

#include <QPainter>
#include <QVBoxLayout>

#include "selfdrive/ui/qt/util.h"

namespace {
const QColor kBsmColor(0xff, 0xff, 0, 255);       // matches OnroadWindow::DP_INDICATOR_COLOR_BSM
const QColor kPanelBg(0, 0, 0, 200);
const QColor kEgoColor(255, 255, 255, 230);
const QColor kLeadColor(201, 34, 49, 230);        // matches model.cc lead chevron red

constexpr float kMaxLeadRangeM = 120.0f;
}  // namespace

// ----- TelemetryGridPage -----

TelemetryGridPage::TelemetryGridPage(QWidget *parent) : QWidget(parent) {}

void TelemetryGridPage::updateState(const UIState &s) {
  const SubMaster &sm = *(s.sm);
  if (sm.rcv_frame("carState") < s.scene.started_frame) {
    leftBlindspot = rightBlindspot = leadOneValid = false;
    update();
    return;
  }

  const auto &car_state = sm["carState"].getCarState();
  leftBlindspot = car_state.getLeftBlindspot();
  rightBlindspot = car_state.getRightBlindspot();

  leadOneValid = false;
  if (sm.alive("radarState")) {
    const auto &lead_one = sm["radarState"].getRadarState().getLeadOne();
    leadOneValid = lead_one.getStatus();
    if (leadOneValid) {
      leadOneDRel = lead_one.getDRel();
      leadOneVRel = lead_one.getVRel();
    }
  }
  update();
}

void TelemetryGridPage::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);
  p.fillRect(rect(), kPanelBg);

  p.setPen(Qt::white);
  p.setFont(QFont("Inter", 18, QFont::DemiBold));
  p.drawText(QRect(0, 12, width(), 30), Qt::AlignHCenter, tr("TOP VIEW"));

  // Lane guide
  const int laneMargin = width() * 0.22;
  p.setPen(QPen(QColor(255, 255, 255, 60), 3, Qt::DashLine));
  p.drawLine(laneMargin, 50, laneMargin, height() - 90);
  p.drawLine(width() - laneMargin, 50, width() - laneMargin, height() - 90);

  // Ego car, anchored near the bottom
  const int egoW = width() * 0.22, egoH = egoW * 1.6;
  const QRect egoRect(width() / 2 - egoW / 2, height() - 90 - egoH, egoW, egoH);
  p.setPen(Qt::NoPen);
  p.setBrush(kEgoColor);
  p.drawRoundedRect(egoRect, 8, 8);

  // Lead vehicle, placed above the ego car scaled by distance. Front radar
  // only -- there is no corner-radar or side-camera source yet, so nothing
  // else is drawn here.
  if (leadOneValid) {
    const float frac = std::clamp(leadOneDRel / kMaxLeadRangeM, 0.0f, 1.0f);
    const int travel = egoRect.top() - 60;
    const int leadCenterY = egoRect.top() - static_cast<int>(frac * travel);
    const int leadW = egoW, leadH = egoH;
    const QRect leadRect(width() / 2 - leadW / 2, leadCenterY - leadH / 2, leadW, leadH);
    p.setBrush(leadOneVRel < -1.0f ? kLeadColor : QColor(255, 255, 255, 200));
    p.drawRoundedRect(leadRect, 8, 8);

    p.setPen(Qt::white);
    p.setFont(QFont("Inter", 14));
    p.drawText(QRect(0, leadRect.top() - 26, width(), 22), Qt::AlignHCenter,
               QString("%1m  %2%3").arg(leadOneDRel, 0, 'f', 0)
                   .arg(leadOneVRel >= 0 ? "+" : "").arg(leadOneVRel, 0, 'f', 1));
  }

  // Blind-spot chips -- real BSM booleans, same semantics as the full-screen
  // flash in OnroadWindow, just given a persistent readout here.
  const int chipW = width() * 0.16, chipH = 60;
  const QRect leftChip(8, height() - chipH - 10, chipW, chipH);
  const QRect rightChip(width() - chipW - 8, height() - chipH - 10, chipW, chipH);
  p.setBrush(leftBlindspot ? kBsmColor : QColor(255, 255, 255, 30));
  p.drawRoundedRect(leftChip, 6, 6);
  p.setBrush(rightBlindspot ? kBsmColor : QColor(255, 255, 255, 30));
  p.drawRoundedRect(rightChip, 6, 6);

  p.setPen(leftBlindspot ? Qt::black : Qt::white);
  p.setFont(QFont("Inter", 13, QFont::DemiBold));
  p.drawText(leftChip, Qt::AlignCenter, tr("L BLIND"));
  p.setPen(rightBlindspot ? Qt::black : Qt::white);
  p.drawText(rightChip, Qt::AlignCenter, tr("R BLIND"));
}

// ----- TelemetryStatsPage -----

TelemetryStatsPage::TelemetryStatsPage(QWidget *parent) : QWidget(parent) {}

void TelemetryStatsPage::updateState(const UIState &s) {
  is_metric = s.scene.is_metric;
  const SubMaster &sm = *(s.sm);
  if (sm.rcv_frame("carState") < s.scene.started_frame) {
    vEgo = steeringAngleDeg = 0;
    leadOneValid = false;
    update();
    return;
  }

  const auto &car_state = sm["carState"].getCarState();
  vEgo = std::max<float>(0.0f, car_state.getVEgo() * (is_metric ? MS_TO_KPH : MS_TO_MPH));
  steeringAngleDeg = car_state.getSteeringAngleDeg();

  leadOneValid = false;
  if (sm.alive("radarState")) {
    const auto &lead_one = sm["radarState"].getRadarState().getLeadOne();
    leadOneValid = lead_one.getStatus();
    if (leadOneValid) {
      leadOneDRel = lead_one.getDRel();
      leadOneVRel = lead_one.getVRel();
    }
  }
  update();
}

void TelemetryStatsPage::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);
  p.fillRect(rect(), kPanelBg);

  p.setPen(Qt::white);
  p.setFont(QFont("Inter", 18, QFont::DemiBold));
  p.drawText(QRect(0, 12, width(), 30), Qt::AlignHCenter, tr("STATS"));

  auto drawRow = [&](int y, const QString &label, const QString &value) {
    p.setFont(QFont("Inter", 14));
    p.setPen(QColor(255, 255, 255, 150));
    p.drawText(QRect(20, y, width() - 40, 24), Qt::AlignLeft, label);
    p.setFont(QFont("Inter", 26, QFont::DemiBold));
    p.setPen(Qt::white);
    p.drawText(QRect(20, y + 22, width() - 40, 40), Qt::AlignLeft, value);
  };

  int y = 60;
  drawRow(y, tr("SPEED"), QString("%1 %2").arg(vEgo, 0, 'f', 0).arg(is_metric ? tr("km/h") : tr("mph")));
  y += 80;
  drawRow(y, tr("STEERING ANGLE"), QString("%1°").arg(steeringAngleDeg, 0, 'f', 1));
  y += 80;
  drawRow(y, tr("LEAD DISTANCE"), leadOneValid ? QString("%1 m").arg(leadOneDRel, 0, 'f', 0) : tr("--"));
  y += 80;
  drawRow(y, tr("LEAD REL. SPEED"), leadOneValid ? QString("%1 m/s").arg(leadOneVRel, 0, 'f', 1) : tr("--"));
}

// ----- TelemetryWidget -----

constexpr int kDotsBarHeight = 28;

TelemetryWidget::TelemetryWidget(QWidget *parent) : QWidget(parent) {
  QVBoxLayout *main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(0, 0, 0, 0);
  main_layout->setSpacing(0);

  pages = new QStackedWidget(this);
  gridPage = new TelemetryGridPage(this);
  statsPage = new TelemetryStatsPage(this);
  pages->addWidget(gridPage);  // index 0: default gridd top-down view
  pages->addWidget(statsPage);
  main_layout->addWidget(pages, 1);

  // Transparent strip left for the page-dot indicator, painted by this
  // widget's own paintEvent -- kept as a plain (non-opaque) child so it
  // doesn't paint over that indicator.
  QWidget *dotsSpacer = new QWidget(this);
  dotsSpacer->setFixedHeight(kDotsBarHeight);
  dotsSpacer->setAttribute(Qt::WA_TransparentForMouseEvents);
  main_layout->addWidget(dotsSpacer);

  QObject::connect(uiState(), &UIState::uiUpdate, this, &TelemetryWidget::updateState);
}

void TelemetryWidget::updateState(const UIState &s) {
  if (!s.scene.started) return;
  gridPage->updateState(s);
  statsPage->updateState(s);
  update();
}

void TelemetryWidget::goToPage(int index) {
  const int count = pages->count();
  pages->setCurrentIndex(((index % count) + count) % count);
  update();
}

void TelemetryWidget::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);
  p.fillRect(rect(), Qt::black);
  // Page dots
  const int count = pages->count(), dotR = 5, spacing = 18;
  const int totalW = (count - 1) * spacing;
  int x = width() / 2 - totalW / 2;
  for (int i = 0; i < count; ++i) {
    p.setBrush(i == pages->currentIndex() ? Qt::white : QColor(255, 255, 255, 80));
    p.setPen(Qt::NoPen);
    p.drawEllipse(QPoint(x, height() - kDotsBarHeight / 2), dotR, dotR);
    x += spacing;
  }
}

void TelemetryWidget::mousePressEvent(QMouseEvent *event) {
  dragStart = event->pos();
  dragging = true;
}

void TelemetryWidget::mouseReleaseEvent(QMouseEvent *event) {
  if (!dragging) return;
  dragging = false;
  const int dx = event->pos().x() - dragStart.x();
  const int dy = event->pos().y() - dragStart.y();
  constexpr int kSwipeThreshold = 60;
  if (std::abs(dx) > kSwipeThreshold && std::abs(dx) > std::abs(dy)) {
    goToPage(pages->currentIndex() + (dx < 0 ? 1 : -1));
  }
}
