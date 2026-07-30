# Design Document: Map Panel UI

> **⚠️ SUPERSEDED (2026-07-06):** The Map Panel / Cards UI described below was removed
> (commit `bd17cf80e`) — it only ever activated on RK3576's widescreen aspect ratio,
> which RK3588 (openpilot's only supported platform) never matches. The map/route now
> live entirely on the NavPilot phone app; the device shows only a turn-by-turn
> maneuver overlay (`selfdrive/ui/qt/onroad/hud.cc`), not a map. Kept below for historical
> reference only.

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Build** | ✅ Conditional on `QMapLibre` |
| **Cereal Services** | ✅ Fixed (`navInstruction`, `navRoute`, `mapData`, `liveLocationKalman`) |

---

> **Layer:** Qt UI (C++) — separate from NAVD daemon
> **Platform:** Both RK3588 and RK3576 (when QMapLibre is installed)
> **Status:** ✅ Implemented — `selfdrive/ui/qt/maps/map_panel.cc` + `onroad_home.cc`
> **Map engine:** QMapLibre (MapLibre GL Native)
> **Style:** OSM Liberty (open source, no API key) — `https://osm-liberty.lukasstrabe.de/style.json`

---

## 1. Objective

Show an on-demand OSM map overlay in the Qt onroad UI. When navigation is active, the map displays:
- GPS position marker with bearing arrow
- Route polyline from NAVD
- Turn instructions + distance (MapInstructions widget)
- ETA and remaining distance (MapETA widget)

The map panel is **toggleable on demand** (FrogPilot-style) — hidden by default, shown when:
- A `navDestination` is set AND `navRoute` is published
- User taps the non-sidebar area of the screen

---

## 2. Display Layout

### 2.1 On-Road Split (`OnroadWindow`)

```
┌────────────────────────┬────────────────────────────┐
│                        │                            │
│   MapPanel (576 px)    │   AnnotatedCameraWidget    │
│   QMapLibre + route    │   (road camera + HUD)      │
│   + instructions       │                            │
│                        │                            │
└────────────────────────┴────────────────────────────┘
```

- Map is inserted at position 0 of the `QHBoxLayout` `split` (left side)
- Width: **fixed 576 px** (matches RK3576 cards panel width)
- Hidden by default; toggled via `mousePressEvent` on `OnroadWindow`
- Camera fills remaining width

### 2.2 Full-Screen Context (RK3576 EXO2, 1600×600)

The on-road window lives inside `HomeWindow` alongside the cards panel:

```
┌─────────────────┬─────────────┬─────────────────┐
│   MapPanel      │   Camera    │    Cards        │
│    576 px       │   448 px    │    576 px       │
│   (onroad)      │  (onroad)   │  (home window)  │
└─────────────────┴─────────────┴─────────────────┘
        ↑              ↑                ↑
       x=0          x=576           x=1024
```

Camera center = 576 + 224 = **800 px** = screen center.  
The 576 px map on the left mirrors the 576 px cards panel on the right, producing a **symmetric** layout.

| Platform | Screen | Map | Camera | Cards | Notes |
|----------|--------|-----|--------|-------|-------|
| EXO2 (RK3576) | 1600×600 | 576 px | 448 px | 576 px | Symmetric, camera centered |
| EXO1 (RK3588) | 1024×600 | 576 px | 448 px | — | No cards panel |

### 2.3 Hidden State

When map is manually toggled off or no route is active:

```
┌───────────────────────────────────────┐
│                                       │
│         AnnotatedCamera (100%)        │
│                                       │
└───────────────────────────────────────┘
```

---

## 3. Build System: Conditional QMapLibre

The map code is **conditionally compiled** via `ENABLE_MAPS` macro.

### Detection (`selfdrive/ui/SConscript`)

```python
import subprocess

qmaplibre_available = False
try:
  subprocess.run(['pkg-config', '--exists', 'QMapLibre'], check=True)
  qmaplibre_available = True
except Exception:
  pass

if qmaplibre_available:
  qt_env['CXXFLAGS'] += ["-DENABLE_MAPS"]
  base_libs += ['QMapLibre']
  qt_src += [
    "qt/maps/map.cc", "qt/maps/map_eta.cc", "qt/maps/map_helpers.cc",
    "qt/maps/map_instructions.cc", "qt/maps/map_panel.cc",
  ]
```

### Runtime Behaviour

| QMapLibre Installed | `ENABLE_MAPS` | Map Widget | UI Binary |
|---------------------|---------------|------------|-----------|
| Yes | Defined | ✅ Built + wired | Links `-lQMapLibre` |
| No | Undefined | ❌ Skipped | Builds without map code |

### Installing QMapLibre

```bash
# Ubuntu / Debian
sudo apt install libqmaplibre-dev

# Or build from source
# https://github.com/maplibre/maplibre-native-qt
```

---

## 4. Map Panel Behavior States

| State | Condition | Map content |
|-------|-----------|-------------|
| **Hidden** | Default / no navigation | Not visible |
| **GPS Only** | Map visible, no route | Current position marker |
| **Routing** | `NavDestination` set, route incoming | Position + "Waiting for route" |
| **Navigating** | `navRoute` + `navInstruction` active | Position + route polyline + turn arrow + ETA |
| **Arrived** | `maneuverType == "arrive"` | Position marker, route clears |
| **No GPS** | No `liveLocationKalman` fix | Map visible, "Waiting for GPS" |

---

## 5. Code Changes

### 5.1 `selfdrive/ui/qt/maps/map_panel.cc`

Wrapper around `MapWindow`. Connects:
- `UIState::offroadTransition` → `MapWindow::offroadTransition`
- `Device::interactiveTimeout` → `content_stack->setCurrentIndex(0)`
- `MapWindow::requestVisible` → `MapPanel::mapPanelRequested()` + `setVisible()`

```cpp
MapPanel::MapPanel(const QMapLibre::Settings &settings, QWidget *parent) : QFrame(parent) {
  content_stack = new QStackedLayout(this);

  auto map = new MapWindow(settings);
  QObject::connect(uiState(), &UIState::offroadTransition, map, &MapWindow::offroadTransition);
  QObject::connect(device(), &Device::interactiveTimeout, this, [=]() {
    content_stack->setCurrentIndex(0);
  });
  QObject::connect(map, &MapWindow::requestVisible, this, `visible` {
    if (visible) { emit mapPanelRequested(); }
    setVisible(visible);
  });
  content_stack->addWidget(map);
}
```

### 5.2 `selfdrive/ui/qt/onroad/onroad_home.h`

```cpp
class OnroadWindow : public QWidget {
  Q_OBJECT
public:
  OnroadWindow(QWidget* parent = 0);
  bool isMapVisible() const { return map && map->isVisible(); }
  void showMapPanel(bool show) { if (map) map->setVisible(show); }
signals:
  void mapPanelRequested();
private:
  void createMapWidget();
  QWidget *map = nullptr;
  QHBoxLayout* split;
};
```

### 5.3 `selfdrive/ui/qt/onroad/onroad_home.cc`

```cpp
#ifdef ENABLE_MAPS
#include "selfdrive/ui/qt/maps/map_helpers.h"
#include "selfdrive/ui/qt/maps/map_panel.h"
#endif

void OnroadWindow::createMapWidget() {
#ifdef ENABLE_MAPS
  auto m = new MapPanel(get_osm_settings());
  map = m;
  QObject::connect(m, &MapPanel::mapPanelRequested, this, &OnroadWindow::mapPanelRequested);
  m->setFixedWidth(576);  // fixed width, matches RK3576 cards panel
  split->insertWidget(0, m);  // map left, camera right
  m->setVisible(false);
#endif
}

void OnroadWindow::mousePressEvent(QMouseEvent* e) {
#ifdef ENABLE_MAPS
  if (map != nullptr) {
    bool sidebarVisible = geometry().x() > 0;
    bool show_map = !sidebarVisible;
    if (show_map) {
      map->setVisible(show_map && !map->isVisible());
    }
  }
#endif
  QWidget::mousePressEvent(e);
}

void OnroadWindow::offroadTransition(bool offroad) {
#ifdef ENABLE_MAPS
  if (!offroad) {
    if (map == nullptr) {
      createMapWidget();
    }
  }
#endif
  alerts->clear();
}
```

### 5.4 `selfdrive/ui/qt/home.cc`

```cpp
#ifdef ENABLE_MAPS
  QObject::connect(onroad, &OnroadWindow::mapPanelRequested, this, [=] { sidebar->hide(); });
#endif
```

### 5.5 `selfdrive/ui/ui.cc`

Subscriptions include nav/map services:
```cpp
sm = std::make_unique<SubMaster>(std::vector<const char*>{
  // ...
  "navInstruction", "navRoute", "liveLocationKalman", "mapData",
  // ...
});
```

---

## 6. Map Style & Tiles

| Aspect | Value |
|--------|-------|
| Style URL | `https://osm-liberty.lukasstrabe.de/style.json` |
| Tile source | OpenStreetMap (`https://tile.openstreetmap.org`) |
| API key | None required |
| Cache | `/data/osm-cache-navd.db` (200MB max) |

**Note:** Routing is offline (Valhalla). Map tiles/styles require internet at runtime.

---

## 7. NavCard Integration

The `NavCard` (RK3576 Cards panel) consumes the same `navInstruction` message:

| Field | Source | Usage |
|-------|--------|-------|
| `maneuverType` + `maneuverModifier` | `navInstruction` | Turn icon |
| `maneuverDistance` | `navInstruction` | Distance to turn |
| `maneuverPrimaryText` | `navInstruction` | Street name |
| `distanceRemaining` | `navInstruction` | Total remaining distance |
| `timeRemaining` | `navInstruction` | ETA calculation |
| `lanes` | `navInstruction` | Lane guidance |

**Fix applied:** `NavCard` previously used non-existent `route.getTotalDistance()` — now reads `inst.getDistanceRemaining()` and `inst.getTimeRemaining()` from `navInstruction`.

---

## 8. Cereal Service Registration

**Critical fix:** `navInstruction`, `navRoute`, `mapData`, and `liveLocationKalman` were missing from `cereal/services.py`, causing SubMaster assert failures (C++) or silent message drops (Python).

Added to `cereal/services.py`:
```python
"liveLocationKalman": (True, 20., 4),
"navInstruction": (True, 1., 10),
"navRoute": (True, 0., -1),
"mapData": (True, 1., 1),
```

Regenerated headers:
```bash
python3 cereal/services.py > cereal/services.h
python3 cereal/services.py > cereal/gen/cpp/services.h
```

---

## 9. Related Documents

- NAVD.md — Navigation daemon (routing, BLE, speed limits)
- [UI.md](./UI.md) — EOP UI design document
- [OVERVIEW.md](../00_Index/OVERVIEW.md) — architecture overview
- [IMPLEMENTATION_STATUS.md](../00_Index/IMPLEMENTATION_STATUS.md) — MAP-PANEL status row
