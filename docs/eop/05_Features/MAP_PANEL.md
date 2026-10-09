# 02M floating navigation map

`dev/02M` now includes a native PyQt5 OSM map PiP in the existing floating
side-panel framework. This replaces the obsolete C++/QMapLibre design previously
documented here. EOP10 and 01M are not changed by this implementation.

The default right panel is navigation; speed remains on the left. Swipe a panel
to cycle its widgets, hold for one second to swap sides, and triple-tap to reset.
The map is bounded to 384 × 360 pixels and sits above the bottom chrome. Camera
overlays, warnings and driver alerts retain their higher drawing priority.

## Live data

The shared UI SubMaster supplies `fusedPosition`, `gpsLocationExternal`,
`navInstruction` and `navRoute`. A fresh fused position with confidence above 0.3
wins over GPS. GPS fixes and fused position expire after two seconds; instructions
expire after five seconds. Route geometry is event-driven and retained while
instructions remain valid. Invalid coordinates are rejected and geometry is
bounded to 4096 points.

The north-up map follows the vehicle and displays its heading when fresh GPS
bearing is available, a blue route, the next maneuver and distance, ETA, remaining
time and distance. No fix shows “Waiting for live location.” Without a route it
still shows live position. NavPilot chooses destinations; host navd owns routing.
Map rendering runs on-device and does not depend on a continuous phone connection.

## Tiles and connectivity

`selfdrive/ui/components/osm_tiles.py` requests only tiles intersecting the
visible viewport at zoom 16. Requests are asynchronous, HTTPS-only, carry an
identifiable User-Agent, and use Qt's HTTP-aware disk cache. Limits are four
in-flight requests, a 64 MiB disk cache, 64 decoded tiles and ten-second request
timeouts. Hiding the panel cancels outstanding downloads. Failures leave local
route and position drawing available and retry after 30 seconds.

Default provider: `https://tile.openstreetmap.org/{z}/{x}/{y}.png`.
Attribution is always visible. Public OSM tiles are a best-effort online service;
this is not an offline map downloader. For fleet deployment choose an appropriate
provider or self-host tiles and configure these environment variables:

| Variable | Purpose |
| --- | --- |
| `EOP_OSM_TILE_URL` | HTTPS PNG URL template containing `{z}`, `{x}`, `{y}`; 256-pixel tiles |
| `EOP_OSM_ATTRIBUTION` | Provider attribution shown inside the map |
| `EOP_OSM_CACHE_DIR` | Persistent cache directory; default Qt application cache directory |

Follow the [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/)
and the selected provider's terms. Qt honors HTTP cache freshness through
[QNetworkDiskCache](https://doc.qt.io/archives/qt-5.15/qnetworkdiskcache.html).
No prefetch, bulk downloading or WebEngine dependency is introduced.

## Validation and limits

`selfdrive/ui/tests/test_map_panel.py` covers coordinate projection, dateline and
polar bounds, fresh/stale sources, route lifetime, panel gestures/data retention,
onroad integration, offline painting, request headers, concurrency and cancellation.
The map/state/panel focused suite passes all 101 tests on desktop Qt with
offscreen rendering. The full UI suite has 194 passes, one skip and three
failures also reproduced against unchanged `dev/02M`: desktop hardware registry
selection and two existing parameter-coverage checks.
RK3576 hardware, live GNSS, target TLS/cache permissions, actual road-camera
composition and on-road readability still require device validation.
