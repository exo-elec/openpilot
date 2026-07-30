#pragma once

#include <string>

#include <QApplication>
#include <QScreen>
#include <QWidget>

// QCOM2-specific Wayland includes removed - Rockchip uses standard display
// TODO: Add Rockchip-specific display handling if needed

#include "system/hardware/hw.h"

const QString ASSET_PATH = ":/";
inline QSize deviceScreenSize() {
  if (Hardware::RK3588()) return {1024, 600};  // ExoPilot 01M / 01L
  return {1024, 600};  // default (PC dev)
}

void setMainWindow(QWidget *w);
