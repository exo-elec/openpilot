#pragma once

#include <string>

#include <QApplication>
#include <QScreen>
#include <QWidget>

// QCOM2-specific Wayland includes removed - Rockchip uses standard display
// TODO: Add Rockchip-specific display handling if needed

#include "system/hardware/hw.h"

const QString ASSET_PATH = ":/";

// Width in px of the extra screen area beyond the ExoPilot 01M baseline
// (1024x600) for the ExoPilot 02M (RK3576) wide-screen telemetry panel. This
// is NOT a guessed/autodetected pixel value -- ExoPilot 02M's true panel
// resolution isn't recorded anywhere in this tree yet (system/hardware/rk3576/
// has real RK3576 SoC/camera support, but no display-size constant). Gating
// is the real, verified Hardware::RK3576() device-tree check; the exact
// extra width is an installer-set param (EOPTelemetryPanelWidth) since only
// whoever has the physical unit can measure it. See qt_window.cc for the
// implementation and nagaspilot/docs/TELEMETRY_PANEL.md for the full design
// note, including why guessing this from screen-size detection was
// abandoned on the sibling dev/EDP10 branch.
int getTelemetryPanelWidth();

inline QSize deviceScreenSize() {
  if (Hardware::RK3588()) return {1024, 600};  // ExoPilot 01M / 01L
  if (Hardware::RK3576()) return {1024 + getTelemetryPanelWidth(), 600};  // ExoPilot 02M
  return {1024, 600};  // default (PC dev)
}

void setMainWindow(QWidget *w);
