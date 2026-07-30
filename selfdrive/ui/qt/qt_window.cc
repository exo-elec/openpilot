#include "selfdrive/ui/qt/qt_window.h"

#include "common/util.h"

void setMainWindow(QWidget *w) {
  const float scale = util::getenv("SCALE", 1.0f);
  const QSize sz = QGuiApplication::primaryScreen()->size();

  const QSize screen = deviceScreenSize();
  if (Hardware::PC() && scale == 1.0 && !(sz - screen).isValid()) {
    w->setMinimumSize(QSize(640, 480)); // allow resize smaller than fullscreen
    w->setMaximumSize(screen);
    w->resize(sz);
  } else {
    w->setFixedSize(screen * scale);
  }
  w->show();

// QCOM2-specific Wayland display rotation removed
// Rockchip/ExoPilot uses standard display orientation
// TODO: Add EOP-specific display handling if screen rotation needed
}


extern "C" {
  void set_main_window(void *w) {
    setMainWindow((QWidget*)w);
  }
}
