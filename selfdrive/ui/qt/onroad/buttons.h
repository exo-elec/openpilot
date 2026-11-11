#pragma once

#include <QPushButton>

#include "selfdrive/ui/ui.h"

const int btn_size = 110;
const int img_size = (btn_size / 4) * 3;

// Onroad quick access (owner 2026-11-11): tap to cycle Driver Personality
// Aggressive -> Standard -> Relaxed. Replaces openpilot's Experimental mode
// button; the ACC/E2E choice is Settings -> Driving -> Drive Mode.
class PersonalityButton : public QPushButton {
  Q_OBJECT

public:
  explicit PersonalityButton(QWidget *parent = 0);
  void updateState(const UIState &s);

private:
  void paintEvent(QPaintEvent *event) override;
  void cyclePersonality();

  Params params;
  int personality = 1;  // LongitudinalPersonality: 0 aggressive, 1 standard, 2 relaxed
  bool engageable = false;
};

void drawIcon(QPainter &p, const QPoint &center, const QPixmap &img, const QBrush &bg, float opacity);
