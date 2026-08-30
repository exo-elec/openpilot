#pragma once

#include <string>

#include <QApplication>
#include <QScreen>
#include <QWidget>

#ifdef QCOM2
#include <qpa/qplatformnativeinterface.h>
#include <wayland-client-protocol.h>
#include <QPlatformSurfaceEvent>
#endif

#include "system/hardware/hw.h"

const QString ASSET_PATH = ":/";
const QSize DEVICE_SCREEN_SIZE = {2160, 1080};

// Width in px of the extra screen area beyond the baseline comma-three panel
// (DEVICE_SCREEN_SIZE), for the ExoPilot 02 wide-screen telemetry panel. This
// is NOT autodetected from the real panel -- it reads the installer-set
// dp_ui_exopilot_wide_screen / dp_ui_telemetry_panel_width params (see the
// comment above this function's definition in qt_window.cc for why). Zero
// unless an installer has explicitly opted a unit in, so the driving view
// stays byte-for-byte the same as device 01 by default.
int getTelemetryPanelWidth();

void setMainWindow(QWidget *w);
