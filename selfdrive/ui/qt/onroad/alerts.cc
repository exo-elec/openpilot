#include "selfdrive/ui/qt/onroad/alerts.h"

#include <QPainter>
#include <map>

#include "selfdrive/ui/qt/util.h"

void OnroadAlerts::updateState(const UIState &s) {
  Alert a = getAlert(*(s.sm), s.scene.started_frame);
  if (!alert.equal(a)) {
    alert = a;
    update();
  }
}

void OnroadAlerts::clear() {
  alert = {};
  update();
}

OnroadAlerts::Alert OnroadAlerts::getAlert(const SubMaster &sm, uint64_t started_frame) {
  const cereal::SelfdriveState::Reader &ss = sm["selfdriveState"].getSelfdriveState();
  const uint64_t selfdrive_frame = sm.rcv_frame("selfdriveState");

  Alert a = {};
  if (selfdrive_frame >= started_frame) {  // Don't get old alert.
    a = {ss.getAlertText1().cStr(), ss.getAlertText2().cStr(),
         ss.getAlertType().cStr(), ss.getAlertSize(), ss.getAlertStatus()};
  }

  if (!sm.updated("selfdriveState") && (sm.frame - started_frame) > 5 * UI_FREQ) {
    const int SELFDRIVE_STATE_TIMEOUT = 5;
    const int ss_missing = (nanos_since_boot() - sm.rcv_time("selfdriveState")) / 1e9;

    // Handle selfdrive timeout
    if (selfdrive_frame < started_frame) {
      // car is started, but selfdriveState hasn't been seen at all
      a = {tr("openpilot Unavailable"), tr("Waiting to start"),
           "selfdriveWaiting", cereal::SelfdriveState::AlertSize::MID,
           cereal::SelfdriveState::AlertStatus::NORMAL};
    } else if (ss_missing > SELFDRIVE_STATE_TIMEOUT && !Hardware::PC()) {
      // car is started, but selfdrive is lagging or died
      if (ss.getEnabled() && (ss_missing - SELFDRIVE_STATE_TIMEOUT) < 10) {
        a = {tr("TAKE CONTROL IMMEDIATELY"), tr("System Unresponsive"),
             "selfdriveUnresponsive", cereal::SelfdriveState::AlertSize::FULL,
             cereal::SelfdriveState::AlertStatus::CRITICAL};
      } else {
        a = {tr("System Unresponsive"), tr("Reboot Device"),
             "selfdriveUnresponsivePermanent", cereal::SelfdriveState::AlertSize::MID,
             cereal::SelfdriveState::AlertStatus::NORMAL};
      }
    }
  }
  return a;
}

void OnroadAlerts::paintEvent(QPaintEvent *event) {
  if (alert.size == cereal::SelfdriveState::AlertSize::NONE) {
    return;
  }
  static std::map<cereal::SelfdriveState::AlertSize, const int> alert_heights = {
    {cereal::SelfdriveState::AlertSize::SMALL, 150},
    {cereal::SelfdriveState::AlertSize::MID, 235},
    {cereal::SelfdriveState::AlertSize::FULL, height()},
  };
  int h = alert_heights[alert.size];

  int margin = 25;
  int radius = 18;
  if (alert.size == cereal::SelfdriveState::AlertSize::FULL) {
    margin = 0;
    radius = 0;
  }
  QRect r = QRect(0 + margin, height() - h + margin, width() - margin*2, h - margin*2);

  QPainter p(this);

  // draw background + gradient
  p.setPen(Qt::NoPen);
  p.setCompositionMode(QPainter::CompositionMode_SourceOver);
  p.setBrush(QBrush(alert_colors[alert.status]));
  p.drawRoundedRect(r, radius, radius);

  QLinearGradient g(0, r.y(), 0, r.bottom());
  g.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.05));
  g.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0.35));

  p.setCompositionMode(QPainter::CompositionMode_DestinationOver);
  p.setBrush(QBrush(g));
  p.drawRoundedRect(r, radius, radius);
  p.setCompositionMode(QPainter::CompositionMode_SourceOver);

  // text
  const QPoint c = r.center();
  p.setPen(QColor(0xff, 0xff, 0xff));
  p.setRenderHint(QPainter::TextAntialiasing);
  if (alert.size == cereal::SelfdriveState::AlertSize::SMALL) {
    p.setFont(InterFont(42, QFont::DemiBold));
    p.drawText(r, Qt::AlignCenter, alert.text1);
  } else if (alert.size == cereal::SelfdriveState::AlertSize::MID) {
    p.setFont(InterFont(50, QFont::Bold));
    p.drawText(QRect(0, c.y() - 70, width(), 85), Qt::AlignHCenter | Qt::AlignTop, alert.text1);
    p.setFont(InterFont(38));
    p.drawText(QRect(0, c.y() + 12, width(), 50), Qt::AlignHCenter, alert.text2);
  } else if (alert.size == cereal::SelfdriveState::AlertSize::FULL) {
    bool l = alert.text1.length() > 15;
    p.setFont(InterFont(l ? 75 : 100, QFont::Bold));
    p.drawText(QRect(0, r.y() + (l ? 135 : 150), width(), 340), Qt::AlignHCenter | Qt::TextWordWrap, alert.text1);
    p.setFont(InterFont(50));
    p.drawText(QRect(0, r.height() - (l ? 200 : 235), width(), 170), Qt::AlignHCenter | Qt::TextWordWrap, alert.text2);
  }
}
