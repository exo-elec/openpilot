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
// (DEVICE_SCREEN_SIZE), detected once at startup from the real panel size Qt's
// display backend (EGLFS/DRM) already probed during boot -- no assumed board
// ID or sysfs path. Zero on comma three (or any panel at or under baseline
// width), so the driving view stays byte-for-byte the same as device 01.
int getTelemetryPanelWidth();

void setMainWindow(QWidget *w);
