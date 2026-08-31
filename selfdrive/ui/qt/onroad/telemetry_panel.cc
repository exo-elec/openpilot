#include "selfdrive/ui/qt/onroad/telemetry_panel.h"

#include <algorithm>
#include <cmath>

#include <QPainter>
#include <QVBoxLayout>

#include "common/util.h"
#include "selfdrive/ui/qt/util.h"

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
  } else {
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
  }

  // Skip the repaint (font switches, several drawText calls) when nothing
  // displayed here actually changed -- e.g. steady cruise with no lead,
  // ticking at UI_FREQ with identical numbers every frame. is_metric has to
  // be part of this comparison too: toggling units while stopped (vEgo == 0
  // either way) with no lead would otherwise leave the wrong km/h/mph label
  // on screen indefinitely, since nothing else displayed would have changed.
  const bool changed = !has_prev || is_metric != prevIsMetric || vEgo != prevVEgo ||
                        steeringAngleDeg != prevSteeringAngleDeg || leadOneValid != prevLeadOneValid ||
                        (leadOneValid && (leadOneDRel != prevLeadOneDRel || leadOneVRel != prevLeadOneVRel));
  if (changed) {
    prevIsMetric = is_metric;
    prevVEgo = vEgo;
    prevSteeringAngleDeg = steeringAngleDeg;
    prevLeadOneValid = leadOneValid;
    prevLeadOneDRel = leadOneDRel;
    prevLeadOneVRel = leadOneVRel;
    has_prev = true;
    update();
  }
}

void TelemetryStatsPage::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);
  p.fillRect(rect(), kPanelBg);

  p.setPen(Qt::white);
  p.setFont(InterFont(18, QFont::DemiBold));
  p.drawText(QRect(0, 12, width(), 30), Qt::AlignHCenter, tr("STATS"));

  auto drawRow = [&](int y, const QString &label, const QString &value) {
    p.setFont(InterFont(14));
    p.setPen(QColor(255, 255, 255, 150));
    p.drawText(QRect(20, y, width() - 40, 24), Qt::AlignLeft, label);
    p.setFont(InterFont(26, QFont::DemiBold));
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
  // BEVWidget takes no opinion on its own size (see bev_widget.h) -- as a
  // QStackedWidget page it's simply resized to fill the page area, same as
  // any other page here.
  bevPage = new BEVWidget(this);
  statsPage = new TelemetryStatsPage(this);
  // Mouse-transparent, same technique (and reason) as dotsSpacer below and
  // OnroadWindow's own `alerts` overlay (onroad_home.cc): Qt delivers mouse
  // press/release to the topmost widget under the cursor and does not
  // bubble an unaccepted one up to the parent on its own, so without this,
  // TelemetryPanel::mousePressEvent/mouseReleaseEvent below would never
  // fire for a swipe anywhere over these pages -- only over the thin dots
  // strip. Neither page has its own interactive content that needs real
  // mouse events. `pages` itself needs the same attribute, not just its
  // two children -- it's a plain QStackedWidget with no mouse handling of
  // its own, so once bevPage/statsPage stop absorbing the event, `pages`
  // (the direct child of this panel's layout) would just absorb it
  // instead, one container short of actually reaching TelemetryPanel.
  bevPage->setAttribute(Qt::WA_TransparentForMouseEvents);
  statsPage->setAttribute(Qt::WA_TransparentForMouseEvents);
  pages->setAttribute(Qt::WA_TransparentForMouseEvents);
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
  // Only the current page: the other one isn't drawn (QStackedWidget), so
  // walking modelV2/radarState to populate it every ~UI_FREQ tick regardless
  // of which page is showing would be pure waste. Whichever page a swipe
  // lands on picks up fresh data on the very next tick -- an imperceptible
  // delay at UI_FREQ, not a staleness problem.
  //
  // BEVWidget no longer manages its own visibility (see bev_widget.h) --
  // this panel's QStackedWidget page-switching is already the sole
  // authority over what's shown, so no extra visibility handling is needed
  // here; bevPage draws an empty grid on its own when disabled/invalid.
  QWidget *current = pages->currentWidget();
  if (current == bevPage) {
    bevPage->updateState(s);
  } else if (current == statsPage) {
    statsPage->updateState(s);
  }
  // No unconditional update() here: bevPage/statsPage each call their own
  // update() only when something they draw actually changed, and this
  // panel's own paintEvent (the page-dot indicator) only depends on the
  // current page index, which goToPage() below already repaints on change.
}

void TelemetryPanel::goToPage(int index) {
  const int count = pages->count();
  const int wrapped = ((index % count) + count) % count;
  if (wrapped == pages->currentIndex()) return;
  pages->setCurrentIndex(wrapped);
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
