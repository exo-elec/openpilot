#include "selfdrive/ui/qt/onroad/buttons.h"

#include <algorithm>
#include <string>

#include <QPainter>

#include "selfdrive/ui/qt/util.h"

void drawIcon(QPainter &p, const QPoint &center, const QPixmap &img, const QBrush &bg, float opacity) {
  p.setRenderHint(QPainter::Antialiasing);
  p.setOpacity(1.0);  // bg dictates opacity of ellipse
  p.setPen(Qt::NoPen);
  p.setBrush(bg);
  p.drawEllipse(center, btn_size / 2, btn_size / 2);
  p.setOpacity(opacity);
  p.drawPixmap(center - QPoint(img.width() / 2, img.height() / 2), img);
  p.setOpacity(1.0);
}

// PersonalityButton
PersonalityButton::PersonalityButton(QWidget *parent) : QPushButton(parent) {
  setFixedSize(btn_size, btn_size);
  QObject::connect(this, &QPushButton::clicked, this, &PersonalityButton::cyclePersonality);
}

void PersonalityButton::cyclePersonality() {
  // selfdrived re-reads the param every 0.1 s and publishes it back in
  // selfdriveState.personality, which updateState() shows.
  personality = (personality + 1) % 3;
  params.put("LongitudinalPersonality", std::to_string(personality));
  update();
}

void PersonalityButton::updateState(const UIState &s) {
  const auto cs = (*s.sm)["selfdriveState"].getSelfdriveState();
  const bool eng = cs.getEngageable() || cs.getEnabled();
  const int pers = static_cast<int>(cs.getPersonality());
  if (pers != personality || eng != engageable) {
    personality = pers;
    engageable = eng;
    update();
  }
}

void PersonalityButton::paintEvent(QPaintEvent *event) {
  static const char *labels[] = {QT_TR_NOOP("AGGR"), QT_TR_NOOP("STD"), QT_TR_NOOP("RELAX")};
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);
  p.setPen(Qt::NoPen);
  p.setBrush(QColor(0, 0, 0, isDown() ? 210 : 166));
  p.drawEllipse(QPoint(btn_size / 2, btn_size / 2), btn_size / 2, btn_size / 2);

  // three distance bars: more bars = larger following gap
  const int bars = personality + 1;
  p.setBrush(QColor(255, 255, 255, engageable ? 255 : 150));
  for (int i = 0; i < 3; i++) {
    p.setOpacity(i < bars ? 1.0 : 0.25);
    p.drawRoundedRect(QRect(28 + i * 20, 30, 14, 26), 3, 3);
  }
  p.setOpacity(1.0);
  p.setPen(QColor(255, 255, 255, engageable ? 255 : 150));
  QFont f = p.font();
  f.setPixelSize(20);
  f.setBold(true);
  p.setFont(f);
  p.drawText(QRect(0, 62, btn_size, 26), Qt::AlignCenter, tr(labels[std::clamp(personality, 0, 2)]));
}
