# ESP32 corner radars on openpilot `dev/02M`

Four ESP32_RADAR corner nodes (front-left, front-right, rear-left,
rear-right). On 02M they run ESP32_RADAR **`dev/v2`**: ESP32-S3 + Calterah
**CAL77S244** (77 GHz, 4 TX / 4 RX). The radar does FFT/CFAR/DoA on-chip and
sends a 4D point cloud to its ESP32 over UART in the **Radar4D** protocol
(the same frame format as the robot stack's head radar,
`hal.drivers.radar.radar4d_head`).

Every node has two outputs at once:

| Link | What | openpilot side | Role |
|---|---|---|---|
| **BLE** (critical link, every board) | Tracked objects (voxel grid → on-node Kalman tracker with occlusion coasting) | `system/bluetoothd/ble_central.py` → `radar2d` → gridd `_fuse_radar2d()` | Advisory BSD/RCW/FCTA/RCTA; the object source |
| **WiFi add-on** (02M only) | The radar's raw Radar4D frames, in ≤1400-byte UDP chunks, port 47000 | `selfdrive/controls/radar4d.py` → `radar4d` → gridd `_fuse_radar4d()` | Costmap occupancy only (raw points, not tracks) |

Host → node over BLE: ego speed/yaw rate (vehicle-state characteristic,
written by `ble_central.py`) and params/calibration commands. If a node
loses BLE it stops all output, WiFi included, and reports it; it does not
reboot. Speed never goes over WiFi.

## WiFi on 02M

02M is our own board with an **AP6275S** (WiFi 6, RSDB): `ap0` is a hidden
**2.4GHz** hotspot for the ESP32s (the ESP32-S3 has no 5GHz radio) and
`wlan0` joins the vehicle's own LAN on **5GHz** at the same time. Set up by
exopilot's `scripts/install/setup_rk3576.sh` (module check + firmware),
`setup_wifi_dualwan.sh` (hotspot, MAC-ACL, `/etc/exopilot/wifi-band.conf`),
`pair_corner_nodes.sh` (per-corner MAC and fixed IP) and
`wifi_lan_connect.sh`. openpilot's WiFi screens pin new 5GHz-capable
networks to band `a` through `common/wifi_band.py`. Details: exopilot
`docs/02-HARDWARE/wifi_corner_nodes.md`.

## `radar4d` service (`selfdrive/controls/radar4d.py`)

- Receives with `hal.drivers.radar.radar4d.RadarCornerReceiver`
  (`Radar4DChunkAssembler` reassembles each frame per source, corner and
  `seq`; `radar4d_head.parse_buffer()` decodes it). Idles, logged once, if
  the `hal` package is missing.
- Keeps the latest cloud per corner; a corner silent for 0.25 s is dropped.
- Places points in the vehicle frame with the **confirmed** corner-pose
  registry (`radar_corner_geometry.load_corner_poses()`, the same one the
  BLE path uses). A corner without a confirmed pose, or with an unresolved
  strap (0xFF), is not published.
- Tags static points with a Doppler check against `carState.vEgo` along
  each point's line of sight (`isStatic`, `dynProp`).
- Publishes `radar4d` (`Custom.Radar4D.points`) at 20 Hz, strongest 512
  points; `trackId`/`existenceProb` are 0 (raw points), `aRel` NaN.
- Pure helpers: `selfdrive/controls/lib/radar4d_points.py`.

gridd `_fuse_radar4d()` stamps the strongest 256 points within 30 m into
the costmap (radius 0.3 m, cost 0.9 moving / 0.7 static); they never become
`stereoObjects` entries.

## Tests

- `selfdrive/controls/tests/test_radar4d_points.py` (incl. end-to-end
  through the hal receiver when `hal` is installed)
- `selfdrive/controls/tests/test_radar4d_daemon.py`
- `selfdrive/gridd/tests/test_fuse_radar4d.py`, `test_fuse_radar2d.py`
- `common/tests/test_wifi_band.py`

## Status

Host-tested; not run on hardware. No CAL77S244 board exists yet. Open
items (radar board, bench signs and point densities, confirmed corner
poses, the AP6275S board): ESP32_RADAR `docs/cal77s244/reserved-todo.md`.

## Sources of truth

- ESP32_RADAR `dev/v2`: `docs/cal77s244/v2-data-path.md`,
  `docs/wire-protocol.md`, `docs/ble-link.md`,
  `docs/cal77s244/radar4d-protocol.md`, radar side `cal77s244_fw/`.
- exopilot hal: `hal/drivers/radar/radar4d.py`, `radar4d_head.py`,
  `radar2d.py`.
