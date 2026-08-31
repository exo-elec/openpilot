#include "selfdrive/ui/qt/qt_window.h"

#include <algorithm>

#include "common/params.h"
#include "common/util.h"

int getTelemetryPanelWidth() {
  static const int width = [] {
    if (!Hardware::RK3576()) return 0;
    // Falls back to the same default the Settings UI's spin box uses (see
    // EOP_TELEMETRY_PANEL_DEFAULT_WIDTH) rather than 0, so a device that
    // hits this path (param never written, or unparseable) still gets a
    // panel sized to match what Settings displays, instead of silently
    // showing no panel at all while Settings claims 576.
    int px = EOP_TELEMETRY_PANEL_DEFAULT_WIDTH;
    try {
      const std::string stored = Params().get("EOPTelemetryPanelWidth");
      if (!stored.empty()) px = std::stoi(stored);
    } catch (const std::exception &) {
      px = EOP_TELEMETRY_PANEL_DEFAULT_WIDTH;
    }
    // The Settings UI's spin box already caps entry at
    // EOP_TELEMETRY_PANEL_MAX_WIDTH, but a value reaching this param by any
    // other path (adb param_set, a params.db migration, a manual edit) has
    // no such limit -- clamp here too, since this result feeds straight
    // into setMainWindow()'s QWidget::setFixedSize() with no other check.
    return std::clamp(px, 0, EOP_TELEMETRY_PANEL_MAX_WIDTH);
  }();
  return width;
}

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
