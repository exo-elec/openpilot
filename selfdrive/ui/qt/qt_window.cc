#include "selfdrive/ui/qt/qt_window.h"

#include <algorithm>

#include "common/params.h"

// Real ExoPilot 01/02 panel pixel dimensions were asked for three times in
// this feature's development and came back different (and reportedly wrong)
// each time, with no spec sheet, part number, or on-device readout ever
// provided to check against. Rather than keep guessing a screen-size
// signature to autodetect against -- especially since production sets no
// SCALE= anywhere, so it's unverified whether QGuiApplication even reports
// ExoPilot's true physical pixels or a display-bridge/scaler's virtual
// 2160x1080 canvas (see nagaspilot/docs/TELEMETRY_PANEL.md) -- this is
// instead an explicit opt-in the installer sets once per physical unit,
// based on hardware only they can see and measure:
//   dp_ui_exopilot_wide_screen (bool): set on a 9" (02) unit, left off (the
//     default) on a 7" (01) unit or anything else -- so an unconfigured
//     device renders exactly as before this feature existed.
//   dp_ui_telemetry_panel_width (int, px): how much extra width that unit's
//     panel actually has beyond its 01 counterpart. No default is assumed
//     safe here either -- an installer who enables the toggle without
//     setting a real width gets a 0px (invisible, harmless) panel rather
//     than a guessed size.
int getTelemetryPanelWidth() {
  static const int width = [] {
    if (Hardware::PC()) return 0;
    Params params;
    if (!params.getBool("dp_ui_exopilot_wide_screen")) return 0;
    int px = 0;
    try {
      px = std::stoi(params.get("dp_ui_telemetry_panel_width"));
    } catch (const std::exception &) {
      px = 0;
    }
    return std::max(0, px);
  }();
  return width;
}

void setMainWindow(QWidget *w) {
  const float scale = util::getenv("SCALE", 1.0f);
  const QSize sz = QGuiApplication::primaryScreen()->size();
  const QSize fixedSize = DEVICE_SCREEN_SIZE + QSize(getTelemetryPanelWidth(), 0);

  if (Hardware::PC() && scale == 1.0 && !(sz - DEVICE_SCREEN_SIZE).isValid()) {
    w->setMinimumSize(QSize(640, 480)); // allow resize smaller than fullscreen
    w->setMaximumSize(DEVICE_SCREEN_SIZE);
    w->resize(sz);
  } else {
    w->setFixedSize(fixedSize * scale);
  }
  w->show();

#ifdef QCOM2
  QPlatformNativeInterface *native = QGuiApplication::platformNativeInterface();
  wl_surface *s = reinterpret_cast<wl_surface*>(native->nativeResourceForWindow("surface", w->windowHandle()));
  wl_surface_set_buffer_transform(s, WL_OUTPUT_TRANSFORM_270);
  wl_surface_commit(s);

  w->setWindowState(Qt::WindowFullScreen);
  w->setVisible(true);

  // ensure we have a valid eglDisplay, otherwise the ui will silently fail
  void *egl = native->nativeResourceForWindow("egldisplay", w->windowHandle());
  assert(egl != nullptr);
#endif
}


extern "C" {
  void set_main_window(void *w) {
    setMainWindow((QWidget*)w);
  }
}
