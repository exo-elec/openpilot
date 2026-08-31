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
// by MainWindow's settings/onboarding width constraint (window.cc), so both
// stay in sync instead of duplicating the literal 1024x600.
constexpr int EOP_01M_WIDTH = 1024;
constexpr int EOP_01M_HEIGHT = 600;

// Width in px of the extra screen area beyond the ExoPilot 01M baseline
// (1024x600) for the ExoPilot 02M (RK3576) wide-screen telemetry panel.
// Defaults to 576 (1600 - 1024, a 1600x600 02M panel), overridable per-unit
// via the EOPTelemetryPanelWidth param without a rebuild if a given unit's
// real panel differs -- system/hardware/rk3576/ has real RK3576 SoC/camera
// support but no display-size constant, so this default is not yet
// cross-checked against a physical panel. Gating is the real, verified
// Hardware::RK3576() device-tree check. See qt_window.cc for the
// implementation and nagaspilot/docs/TELEMETRY_PANEL.md for the full design
// note, including why this was originally zero-defaulted.
int getTelemetryPanelWidth();

inline QSize deviceScreenSize() {
  if (Hardware::RK3588()) return {EOP_01M_WIDTH, EOP_01M_HEIGHT};  // ExoPilot 01M / 01L
  if (Hardware::RK3576()) return {EOP_01M_WIDTH + getTelemetryPanelWidth(), EOP_01M_HEIGHT};  // ExoPilot 02M
  return {EOP_01M_WIDTH, EOP_01M_HEIGHT};  // default (PC dev)
}

void setMainWindow(QWidget *w);
