#include "selfdrive/ui/qt/onroad/telemetry_panel.h"

#include <algorithm>
#include <cmath>

#include <QPainter>
#include <QVBoxLayout>

#include "common/util.h"

namespace {
const QColor kPanelBg(0, 0, 0, 200);
constexpr int kDotsBarHeight = 28;
}  // namespace

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

// ----- TelemetryPanel -----

TelemetryPanel::TelemetryPanel(QWidget *parent) : QWidget(parent) {
  QVBoxLayout *main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(0, 0, 0, 0);
  main_layout->setSpacing(0);

  pages = new QStackedWidget(this);
  bevPage = new BEVWidget(this);
  // BEVWidget's constructor fixes it at 130x180 for its existing small
  // corner-overlay use in AnnotatedCameraWidget; undo that here so it fills
  // this panel's page area instead. bev_widget.cc/h are untouched -- the
  // existing corner overlay instance is unaffected.
  bevPage->setMinimumSize(0, 0);
  bevPage->setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX);
  statsPage = new TelemetryStatsPage(this);
  pages->addWidget(bevPage);  // index 0: default BEV top-down view
  pages->addWidget(statsPage);
  main_layout->addWidget(pages, 1);

  // Transparent strip left for the page-dot indicator, painted by this
  // widget's own paintEvent -- kept as a plain (non-opaque) child so it
  // doesn't paint over that indicator.
  QWidget *dotsSpacer = new QWidget(this);
  dotsSpacer->setFixedHeight(kDotsBarHeight);
  dotsSpacer->setAttribute(Qt::WA_TransparentForMouseEvents);
  main_layout->addWidget(dotsSpacer);
}

void TelemetryPanel::updateState(const UIState &s) {
  if (!s.scene.started) return;
  bevPage->updateState(s);
  // BEVWidget::updateState() calls setVisible(enabled && data_valid) on
  // itself for its other (small corner-overlay) use in AnnotatedCameraWidget.
  // That fights this panel's QStackedWidget: reassert visibility so page
  // switching here stays the sole authority over what's shown, instead of
  // the page silently going blank whenever EOPBEVWidgetEnabled is off or
  // modelV2/radarState haven't arrived yet.
  bevPage->setVisible(true);
  statsPage->updateState(s);
  update();
}

void TelemetryPanel::goToPage(int index) {
  const int count = pages->count();
  pages->setCurrentIndex(((index % count) + count) % count);
  update();
}

void TelemetryPanel::paintEvent(QPaintEvent *event) {
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

void TelemetryPanel::mousePressEvent(QMouseEvent *event) {
  dragStart = event->pos();
  dragging = true;
}

void TelemetryPanel::mouseReleaseEvent(QMouseEvent *event) {
  if (!dragging) return;
  dragging = false;
  const int dx = event->pos().x() - dragStart.x();
  const int dy = event->pos().y() - dragStart.y();
  constexpr int kSwipeThreshold = 60;
  if (std::abs(dx) > kSwipeThreshold && std::abs(dx) > std::abs(dy)) {
    goToPage(pages->currentIndex() + (dx < 0 ? 1 : -1));
  }
}
