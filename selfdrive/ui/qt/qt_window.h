#pragma once

#include <string>

#include <QApplication>
#include <QScreen>
#include <QWidget>

// QCOM2-specific Wayland includes removed - Rockchip uses standard display
// TODO: Add Rockchip-specific display handling if needed

#include "system/hardware/hw.h"

const QString ASSET_PATH = ":/";

// The one panel this UI supports: ExoPilot 01M, 1024x600.
//
// Worth knowing: this size is the long-standing assumption, not a measured
// spec. exopilot's hal/hal/platform/boards.py records a confirmed panel for
// 02M (confirmed_ui_panel_px = 1600x600) and carries no display data at all
// for 01M, so nothing outside this repo corroborates 1024x600. It is what
// the layout has always been built against, and it stays until someone
// measures the real panel.
//
// This is not a default or a preferred size -- it is the only size. 02M's
// wide screen moved to dev/02M with its telemetry panel and per-unit width
// param (docs/eop/BRANCH_NAMING.md), so no platform here differs from this,
// and every widget below is laid out in absolute coordinates against it.
//
// Nothing may render the UI at another size or aspect ratio. On a dev PC
// whose screen cannot physically fit 1024x600, setMainWindow() applies a
// uniform scale -- the same layout, smaller -- rather than letting the
// window take an arbitrary shape the layout was never designed for.
constexpr int EOP_PANEL_WIDTH = 1024;
constexpr int EOP_PANEL_HEIGHT = 600;

inline QSize deviceScreenSize() {
  return {EOP_PANEL_WIDTH, EOP_PANEL_HEIGHT};
}

void setMainWindow(QWidget *w);
