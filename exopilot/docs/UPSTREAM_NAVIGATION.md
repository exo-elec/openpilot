# Navigation: upstream openpilot's nav alpha

comma brought navigation back on 2026-10-11 (commaai/openpilot `4c362ca32`, "nav alpha" #39066, MIT). NGP10 runs it as
upstream wrote it; ExoPilot follows the same code (owner 2026-11-16: refer to comma's implementation as much as possible).

## What came from upstream (synced at upstream master `f14f4ca60`, 2026-11-15)

| Here | Upstream | Change |
|------|----------|--------|
| `selfdrive/navigation/{helpers,pmtiles,renderer,route,tiles,vector_tile}.py` | `openpilot/selfdrive/navigation/` | import paths only (`cereal.messaging`, `msgq.visionipc`, `openpilot.system.hardware`) |
| `selfdrive/navigation/mapsd.py` | same | import paths, and the tile source comes from `open_tiles()` (a local archive first) |
| `selfdrive/navigation/local_tiles.py`, `tests/test_local_tiles.py` | (new) | a local PMTiles file as the tile source |
| `selfdrive/navigation/navd.py`, `tests/test_navigation.py` | same | import paths, and **routing by Valhalla instead of Mapbox** (owner 2026-11-16: no Mapbox): `calculate_route` posts to Valhalla's `/route`; a step without `bannerInstructions` counts as none |
| `system/athena/athenad.py` `setNavDestination` / `clearNavDestination` (+ test) | same | validation written out (upstream's `number(-90, 90)` decorator is newer than this athenad) |
| `system/manager/process_config.py` `navigation()`, `navd`, `mapsd` | same | module paths without `openpilot.` |
| `common/params_keys.h` `NavigationEnabled`, `NavDestination` | same | none |
| `common/constants.py` `CV.METER_TO_FOOT`, `CV.METER_TO_MILE` | same | none |
| `selfdrive/ui/qt/onroad/navigation.{h,cc}` | `selfdrive/ui/mici/onroad/navigation.py` | Qt port of the raylib panel (comma 3 runs the Qt UI): same states, colours, arrow, distance format and layout, scaled 2.2x |
| Developer toggle "Navigation (Alpha)" | mici `developer.py` | Qt `ParamControl` |

To re-sync: `git fetch upstream master` and diff `openpilot/selfdrive/navigation/` against `selfdrive/navigation/` after
the same three import replacements.

## How it works

- **Destination:** athena `setNavDestination(latitude, longitude)` writes `NavDestination` (cleared at manager start
  and on going offroad). comma connect or any athena client (e.g. Nav2Pilot) can call it.
- **Route:** `navd` (1 Hz) asks **Valhalla** (`VALHALLA_URL`, default `http://127.0.0.1:8002`, the local server EOP10
  already runs) with `costing=auto`, `format=osrm`, `shape_format=geojson`, `banner_instructions=true`, the GPS heading
  (tolerance 90 degrees, as upstream's bearing) and the UI language. Valhalla's OSRM output is the Mapbox Directions JSON
  upstream parses, so everything after the request is upstream's: rerouting after 25 m off the route for more than 3
  updates, `navInstruction` and `navRoute`. Upstream asked Mapbox through comma's API (`/v1/nav`); that path is gone.
  It does not fill `navInstruction.speedLimit`.
- **Map:** `mapsd` fetches byte ranges of comma's hosted Protomaps PMTiles basemap (signed URL from
  `v1/<dongle>/nav/tiles`, 64 MB cache under `/data/navigation/tiles`), draws a 256x256 road + route frame at 1 m per
  pixel (the training map style) and publishes it on `VISION_STREAM_MAP`. No driving model reads it yet.
- **UI:** the panel beside the camera shows the next maneuver, the map crop and the ETA while `NavigationEnabled` is on
  and a destination is set; without a map it lists what it waits for (GPS fix, route, map frames).

## Limits

- Routing needs a Valhalla server with tiles for the area: on the device (EOP boards) or on a reachable machine. Not yet run
  against a live Valhalla here; the response shape follows Valhalla's documented OSRM output.
- **Offline map:** `mapsd` reads a local Protomaps basemap when there is one (`selfdrive/navigation/local_tiles.py`:
  `NAV_PMTILES`, else `/data/media/0/navigation/map.pmtiles`), and only otherwise comma's hosted archive (network and
  the device's comma account). Make the file once per region with the protomaps CLI, e.g.
  `pmtiles extract https://build.protomaps.com/<date>.pmtiles map.pmtiles --bbox=97.3,5.6,105.7,20.5 --maxzoom=15`
  (Thailand), and copy it to the device. The only change in upstream's files is the one line in `mapsd` that opens
  the source.

## ExoPilot boards (EOP10 and up)

- The same `selfdrive/navigation`; EOP10's own Valhalla navd (`selfdrive/navd`) is gone. Its Valhalla routing-tile
  downloaders moved to `exopilot/system/valhalla/`; the local Valhalla server on :8002 is what `navd` asks.
- NavPilot sets the destination over BLE (`ncp_session` CMD_NAVIGATE / CMD_CONVOY_LEAD) into the same `NavDestination`
  ({latitude, longitude, place_name}; navd reads the first two), as athena does. `NavDestinationWaypoints` is gone
  (nothing read it).
- `NavigationEnabled` defaults on (EOP10's navd always ran).
- `navvoiced` (`exopilot/selfdrive/navigation/nav_voice.py`) keeps the former navd's spoken turns on top of
  `navInstruction`: approach (500-200 m), imminent (200-80 m), execute (80-20 m), arrival; `NavVoiceEnabled`; sends
  `ttsRequest`, which only ExoPilot boards have.
- The onroad panel is the same Qt `NavigationPanel` as NGP10, right of the camera.
