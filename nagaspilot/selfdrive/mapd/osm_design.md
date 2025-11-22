# NagaPilot OSM/MapD Design (Minimal, Proven Pattern)

This document describes the minimal, clean design for offline OSM data and road attribute support in NagaPilot, inspired by sunnypilot’s proven approach while keeping scope small and device‑friendly.

## Overview

- UI: A simple Navigation panel (dp_panel style) to select a country and trigger downloads, or delete offline maps.
- Manager: A lightweight background process that downloads Geofabrik region extracts, tracks progress, raises offroad alerts, and keeps an optional MapD binary running.
- Attributes: If a backend writes `np_map_attrs.json` into the offline folder, the manager publishes speed limit and road name Params for controls to consume.

## Components

- UI panel: `nagaspilot/ui/qt/offroad/np_osm_panel.{h,cc}`
  - Country selection via `MultiOptionDialog`.
  - “Database Update” button triggers a download with a metered‑aware confirmation message.
  - “Delete Maps” removes all offline files under the storage path.
  - Progress displayed as `n/m (p%) • elapsed • ETA` based on `OSMDownloadProgress` and `OsmDownloadedDate`.
  - US state button is shown only when country is `US` (currently stubbed as “All States”).
  - Manual URL downloads are NOT supported (keeps UX simple, predictable, and localized to the curated list).

- Manager process: `nagaspilot/mapd/np_mapd_manager.py` (launched as `np_mapdd`)
  - Watches Params to detect update requests and selection changes.
  - Resolves country → Geofabrik URL and performs a streamed download with progress updates.
  - Maintains `LastGPSPosition` and a `DeviceStateNetworkMetered` flag in shared params for the UI.
  - Emits an offroad alert when offline maps are required but missing.
  - Optional: runs a prebuilt MapD binary if configured.
  - Optional: publishes road attributes when `np_map_attrs.json` is present.

## Storage

- Offline data path: `~/nagaspilot/osm/offline`
  - All downloaded files (e.g., `*-latest.osm.pbf`) are saved here.
  - Deleting maps removes all contents of this folder.

## Params Contract

Written/read by the UI panel:
- `OsmLocationTitle` (str) – e.g., `Germany (DE)`
- `OsmLocationName` (str, CC) – two‑letter code (e.g., `DE`, `US`, `TH`)
- `OsmStateTitle` (str) – only if country is `US`
- `OsmStateName` (str) – only if country is `US`
- `OsmDbUpdatesCheck` (bool) – set to `true` to request a download/update

Written/read by the manager:
- `OSMDownloadLocations` (JSON) – summary of nations/states selected (used for compatibility)
- `OSMDownloadProgress` (JSON) – `{ "total_files": 100, "downloaded_files": N }` (0–100)
- `OsmDownloadedDate` (str epoch) – start time used for elapsed/ETA in the UI
- `LastGPSPosition` (JSON) – `{ latitude, longitude, altitude }`
- `DeviceStateNetworkMetered` (bool) – copied from `deviceState.networkMetered` for UI confirmations

Offroad alert:
- `Offroad_OSMUpdateRequired` – set when `OsmLocal` is true and the offline folder is empty; cleared otherwise

Road attributes (published if `np_map_attrs.json` exists):
- `MapSpeedLimit` (str/float) – current speed limit (unit as defined by backend)
- `RoadName` (str) – current road name
- `NextMapSpeedLimit` (JSON) – e.g., `{ "speedlimit": 60.0, "latitude": 14.973, "longitude": 102.087 }`

## Country → Geofabrik Mapping

Manager resolves a Geofabrik URL based on `OsmLocationName` (country code). Curated set:

- North America: US, CA, MX
- Europe: GB, DE, FR, ES, IT, NL, PL
- Asia: JP, KR, TH, SG, MY, ID, PH, IN, AE, SA
- Oceania: AU, NZ
- South America: BR, AR, CL

URL format: `https://download.geofabrik.de/{region/path}-latest.osm.pbf`

Explicit mapping used in manager:

- US → `north-america/us`
- CA → `north-america/canada`
- MX → `north-america/mexico`
- GB → `europe/great-britain`
- DE → `europe/germany`
- FR → `europe/france`
- ES → `europe/spain`
- IT → `europe/italy`
- NL → `europe/netherlands`
- PL → `europe/poland`
- JP → `asia/japan`
- KR → `asia/korea-south`
- TH → `asia/thailand`
- SG → `asia/singapore`
- MY → `asia/malaysia`
- ID → `asia/indonesia`
- PH → `asia/philippines`
- IN → `asia/india`
- AE → `asia/united-arab-emirates`
- SA → `asia/saudi-arabia`
- AU → `australia-oceania/australia`
- NZ → `australia-oceania/new-zealand`
- BR → `south-america/brazil`
- AR → `south-america/argentina`
- CL → `south-america/chile`

Examples:
- `DE` → `https://download.geofabrik.de/europe/germany-latest.osm.pbf`
- `TH` → `https://download.geofabrik.de/asia/thailand-latest.osm.pbf`
- `US` → `https://download.geofabrik.de/north-america/us-latest.osm.pbf` (state list is not yet expanded)

Notes:
- No simulated progress: progress is based on streamed bytes. If a mapping is missing/unsupported, the manager finalizes the request without faking progress.
- Future: US state selection could refine the path to state-level extracts under `north-america/us/`.

## MapD Binary Runner (Optional)

- Param: `NpMapdBinary` – full path to the prebuilt MapD binary.
- Behavior: if set and executable, manager keeps it running in the background (no arguments; config should be embedded or implicit).
- On param changes the runner restarts the binary.

## Road Attributes Drop‑in (Optional)

- File: `~/nagaspilot/osm/offline/np_map_attrs.json`
- If present, manager publishes values to shared Params for consumers:
  - JSON structure example:
    ```json
    {
      "speedlimit": 80.0,
      "roadname": "Mittraphap Rd",
      "next": { "speedlimit": 60.0, "latitude": 14.973, "longitude": 102.087 }
    }
    ```
  - Manager writes:
    - `MapSpeedLimit = 80.0`
    - `RoadName = "Mittraphap Rd"`
    - `NextMapSpeedLimit = { ... }`
- Any backend (MapD or otherwise) can generate this file without UI or manager changes.

## UI Behavior Notes

- Country selection sets `OsmLocal = 1`, `OsmLocationTitle`, `OsmLocationName` and clears any state.
- “Database Update” shows a confirmation dialog; on metered networks it warns and changes the confirm button to “Continue on Metered”.
- While `OsmDbUpdatesCheck` is `true`, selection buttons are disabled and progress updates render in the Update row.
- “Delete Maps” removes all files under `~/nagaspilot/osm/offline`.
- Manual URL is intentionally not supported to keep UX simple and predictable.

## Limitations / Future Work

- US state selection is stubbed; can be expanded to a full list if needed.
- The curated country list can be extended or made dynamic via a fetcher.
- A built‑in attribute extractor can be added to populate `np_map_attrs.json` directly from the PBF (if MapD is not used).
- More advanced MapD management (arguments, status, DB checks) can be added when needed.
