#include "selfdrive/ui/qt/qt_window.h"

#include <cmath>

namespace {
// ExoPilot panel geometry, as given 2026-08-30: both panels are ~600px tall;
// only width differs -- ~1080 is the 7" (01) baseline, ~1600 the 9" (02)
// panel this telemetry feature adds width for. This is a distinct hardware
// family from comma three's 2160x1080 (DEVICE_SCREEN_SIZE), not a scaled
// variant of it, and this tree has zero SCALE= usage in any launch script,
// so production always requests a fixed 2160x1080 window today regardless
// of the real panel -- meaning something outside this Qt code (a display
// bridge/scaler, or the compositor) may already be remapping that fixed
// canvas onto the true ExoPilot panel. Whether QGuiApplication actually
// reports ExoPilot's true physical pixels here, or that same remapped
// 2160x1080 canvas, has NOT been verified against real hardware. So rather
// than compare against an assumed baseline, this only acts when the
// detected size matches the confirmed 9" (02) signature within a small
// tolerance; anything else -- including comma three, PC, or a 2160x1080
// report from ExoPilot's own scaler -- safely falls back to zero extra
// width (device-01 behavior, unchanged).
const QSize kExoPilot01Size(1080, 600);
const QSize kExoPilot02Size(1600, 600);
constexpr int kToleranceHalfPx = 20;

bool matchesSize(const QSize &detected, const QSize &known) {
  return std::abs(detected.width() - known.width()) <= kToleranceHalfPx &&
         std::abs(detected.height() - known.height()) <= kToleranceHalfPx;
}
}  // namespace

int getTelemetryPanelWidth() {
  static const int width = [] {
    if (Hardware::PC()) return 0;
    const QSize detected = QGuiApplication::primaryScreen()->size();
    if (matchesSize(detected, kExoPilot02Size)) {
      return kExoPilot02Size.width() - kExoPilot01Size.width();
    }
    return 0;
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
