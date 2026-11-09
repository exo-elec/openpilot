#pragma once

// One place for the UI's look: geometry, painter colours and the QSS layers.
//
// Mirrors the PyQt5 design (selfdrive/ui/components/theme.py and
// selfdrive/assets/qss/*.qss) so the two can be compared line by line until
// the PyQt5 path is removed (docs/eop/REFACTOR_CLEANUP_TASKS.md, step 4.1).
// Painter values live here because most of the UI is drawn with QPainter,
// which QSS does not reach; widget styling lives in the QSS sheets.

#include <initializer_list>

#include <QColor>
#include <QFont>
#include <QString>

namespace theme {

// ---- geometry (ui.h, sidebar.cc, settings.cc, alerts.cc) -------------------
constexpr int BORDER_SIZE = 30;        // status-coloured frame around the camera
constexpr int HEADER_HEIGHT = 250;     // gradient the HUD text sits in
constexpr int SIDEBAR_W = 260;
constexpr int SETTINGS_NAV_W = 210;
constexpr int ALERT_SMALL_H = 150;
constexpr int ALERT_MID_H = 235;
constexpr int TOUCH_MIN_H = 56;        // touch target on a panel used while driving

// ---- status colours, indexed by UIStatus (ui.h) ----------------------------
inline const QColor STATUS_DISENGAGED{0x17, 0x33, 0x49, 0xc8};
inline const QColor STATUS_OVERRIDE{0x91, 0x9b, 0x95, 0xf1};
inline const QColor STATUS_ENGAGED{0x17, 0x86, 0x44, 0xf1};
inline const QColor STATUS_ALCC{0x22, 0xa0, 0xdc, 0xf1};

// ---- alert colours, by AlertStatus (alerts.h) ------------------------------
inline const QColor ALERT_NORMAL{0x15, 0x15, 0x15, 0xf1};
inline const QColor ALERT_WARNING{0xda, 0x6f, 0x25, 0xf1};
inline const QColor ALERT_CRITICAL{0xc9, 0x22, 0x31, 0xf1};

// ---- shared palette --------------------------------------------------------
inline const QColor GOOD{0x1b, 0x9f, 0xfa};
inline const QColor WARNING{0xff, 0xc9, 0x00};
inline const QColor DANGER{0xc9, 0x22, 0x31};
inline const QColor INACTIVE{0x54, 0x54, 0x54};
inline const QColor WHITE{0xff, 0xff, 0xff};
inline const QColor BLACK{0x00, 0x00, 0x00};
inline const QColor SIDEBAR_BG{57, 57, 57};

// Dark automotive chrome (assets/qss/dark_automotive.qss), for painted widgets
// that must match the styled ones next to them.
inline const QColor BG{0x0d, 0x11, 0x13};
inline const QColor SURFACE{0x13, 0x1a, 0x1c};
inline const QColor SURFACE_RAISED{0x1d, 0x26, 0x29};
inline const QColor OUTLINE{0x2a, 0x36, 0x39};
inline const QColor TEXT{0xe6, 0xeb, 0xea};
inline const QColor TEXT_SECONDARY{0xb3, 0xc0, 0xc0};
inline const QColor TEXT_MUTED{0x7c, 0x8b, 0x8d};
inline const QColor TEXT_DISABLED{0x5c, 0x6b, 0x6d};
inline const QColor PRIMARY{0x2f, 0x75, 0x66};

// Side/rear camera overlay border; replaced by the blind-spot colour when that
// side is occupied.
inline const QColor OVERLAY_BORDER{0, 200, 255, 220};
constexpr int OVERLAY_BORDER_PX = 3;
constexpr int OVERLAY_BLIND_SPOT_PX = 12;

// ---- fonts -----------------------------------------------------------------
inline QFont inter(int pixel_size, QFont::Weight weight = QFont::Normal) {
  QFont f("Inter");
  f.setPixelSize(pixel_size);
  f.setWeight(weight);
  return f;
}

// ---- QSS layers ------------------------------------------------------------
// base, then the theme, then the per-view sheets, read from the :/qss/ resource.
// Applied per screen as it is ported (Phase 4), not app-wide: the theme's
// QWidget background rule paints every QLabel, and the not-yet-ported C++
// screens draw labels over painted panels expecting them transparent.
enum class Component { ONROAD, OFFROAD };

QString styleSheet(std::initializer_list<Component> components = {});

}  // namespace theme
