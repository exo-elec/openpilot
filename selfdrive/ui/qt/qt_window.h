#pragma once

#include <string>

#include <QApplication>
#include <QScreen>
#include <QWidget>

// QCOM2-specific Wayland includes removed - Rockchip uses standard display
// TODO: Add Rockchip-specific display handling if needed

#include "system/hardware/hw.h"

const QString ASSET_PATH = ":/";

// ExoPilot 01M baseline panel size. Shared by deviceScreenSize() below and
// by MainWindow's stack_wrapper sizing (window.cc), so both stay in sync
// instead of duplicating the literal 1024x600.
constexpr int EOP_01M_WIDTH = 1024;
constexpr int EOP_01M_HEIGHT = 600;

// Upper bound on getTelemetryPanelWidth()'s result, shared with the
// Settings UI's spin box (eop_panel.cc) so a value set outside that UI
// (adb param_set, a params.db migration, manual edit) can't produce a
// wildly oversized fixed window on real hardware -- setMainWindow() has no
// other validation between EOPTelemetryPanelWidth and a real
// QWidget::setFixedSize() call.
constexpr int EOP_TELEMETRY_PANEL_MAX_WIDTH = 800;

// Fallback used both when EOPTelemetryPanelWidth (common/params_keys.h)
// hasn't been written yet -- shouldn't happen once manager_init() has
// seeded every declared key, but see eop_panel.cc's ParamSpinBoxControl
// comment for when it can -- and, here, when the stored value fails to
// parse as an int. 576 = 1600 - 1024, i.e. a 1600x600 02M panel; shared
// with eop_panel.cc's ParamSpinBoxControl default_value so a device that
// hits this fallback path shows the same number in Settings that it's
// actually using, instead of getTelemetryPanelWidth() silently landing on
// 0 (no panel at all) while Settings displays 576.
constexpr int EOP_TELEMETRY_PANEL_DEFAULT_WIDTH = 576;

// Width in px of the extra screen area beyond the ExoPilot 01M baseline
// (1024x600) for the ExoPilot 02M (RK3576) wide-screen telemetry panel.
// Defaults to EOP_TELEMETRY_PANEL_DEFAULT_WIDTH, overridable per-unit via
// the EOPTelemetryPanelWidth param without a rebuild if a given unit's
// real panel differs -- system/hardware/rk3576/ has real RK3576 SoC/camera
// support but no display-size constant, so this default is not yet
// cross-checked against a physical panel. Gating is the real, verified
// Hardware::RK3576() device-tree check. See qt_window.cc for the
// implementation and nagaspilot/docs/TELEMETRY_PANEL.md for the full design
// note, including why this was originally zero-defaulted.
int getTelemetryPanelWidth();

inline QSize deviceScreenSize() {
  // Only RK3576 (ExoPilot 02M) ever differs from the baseline -- RK3588
  // (01M) and the PC/default fallback both want exactly {EOP_01M_WIDTH,
  // EOP_01M_HEIGHT}, so there's no need to spell that out three times.
  const int extra = Hardware::RK3576() ? getTelemetryPanelWidth() : 0;
  return {EOP_01M_WIDTH + extra, EOP_01M_HEIGHT};
}

void setMainWindow(QWidget *w);
