#include "selfdrive/ui/qt/qt_window.h"

#include <algorithm>

void setMainWindow(QWidget *w) {
  const QSize panel = deviceScreenSize();

  // One supported size, fixed. The SCALE env knob and the old PC branch
  // (minimum 640x480, resize to whatever the host screen was) let the
  // layout be exercised at arbitrary sizes and aspect ratios it is not
  // written for -- every coordinate here is absolute against 1024x600.
  QSize size = panel;

  // The single exception, and it is not a second supported size: a dev PC
  // whose screen cannot physically fit the panel. Scale the whole thing
  // down uniformly so the aspect ratio -- and therefore the layout -- is
  // preserved. On device this never triggers.
  if (Hardware::PC()) {
    const QSize host = QGuiApplication::primaryScreen()->size();
    if (!(host - panel).isValid()) {
      const qreal fit = std::min(qreal(host.width()) / panel.width(),
                                 qreal(host.height()) / panel.height());
      size = panel * fit;
    }
  }

  w->setFixedSize(size);
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
