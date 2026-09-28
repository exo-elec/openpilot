# CLAUDE.md

Guidance for Claude Code when working on this openpilot fork.

## Project Overview

**ExoPilot (EOP)** — Advanced ADAS for Rockchip RK3588.

- **Codebase**: OpenPilot fork + EOP-specific daemons, controllers, UI
- **Platform**: **RK3588 only** (ExoPilot 01L / 01M). 02M/RK3576 lives on
  `dev/02M` — see **Branch model** below. As of 2026-09-13 this branch carries
  no RK3576 support at all: `system/hardware/rk3576/`, `PlatformType.RK3576`,
  `Hardware::RK3576()`, the RK3576 camera/NPU entries and the 02M UI
  (wide-screen telemetry panel, `EOPTelemetryPanelWidth`, the split
  `MainWindow`) are gone, and `deviceScreenSize()` is a constant 1024x600.
  Asking for `rk3576` now fails loudly rather than falling back. ExoRobot 01H
  (RK3588 16GB, HumRobot) is in `~/robot/exorobot`
- **Suffix = RAM**: L=4GB / M=8GB / H=16GB
- **Camera-tier card is standard on 01M and 02M** (2026-09-27): a Hailo-8 or a
  DX-M1M, detected at runtime, chosen on price. It runs semantic segmentation
  for every camera; object detection (YOLOv8) and the driving model stay on
  RKNN — see New Features below
- **Status**: In development — dev PC testing phase (not hardware-deployed)

## Prerequisites (on real hardware)

ExoPilot BSP must be installed first before openpilot:
```bash
sudo ~/pilot/exopilot/scripts/install/setup_rk3588.sh && sudo reboot   # ExoPilot 01L/01M
```
The 02M equivalent (`setup_rk3576.sh`) belongs with `dev/02M`.

---

## Code Quality Standards

### When Reviewing/Fixing Code

1. **Feature/quality impact only** — Fix runtime bugs, not cosmetic issues
   - ✅ Missing imports, AttributeErrors, capnp API misuse, SQL constraint violations
   - ❌ Typos in comments, style reformatting, unused imports

2. **Capnp field assignment patterns**
   - `List(Struct)`: Use `init('field', count)` + `[i].attr = val` — never `.add()` or direct assignment
   - `List(primitive)`: Direct Python list assignment OK
   - See `selfdrive/controls/lib/*.py` for examples

3. **Daemon initialization** — All attributes used in `get_state()` must be initialized in `__init__`, not just in `update()` code paths

4. **Testing** — Code is at dev PC stage; hardware bugs lower priority than Python runtime crashes
   - Run `./test.sh` before pushing; it checks lint, shebangs, and the RK3588/Rockchip host-side pytest suites.
   - For the full upstream lint gate, run `./test.sh --full`.
   - GitHub check workflows (`eop10_lint`, `selfdrive`, `docs`) are manual-only since
     2026-09-29 (every push/PR reported by email); `./test.sh` is the gate.
   - Install pre-commit hooks: `uv pip install pre-commit && pre-commit install`

### Frame conventions (R5, 2026-09-28) -- match sunnypilot / comma upstream

Checked against current `commaai/openpilot` and `sunnypilot/sunnypilot`
`master` (both `openpilot/...` monorepo layout); they agree:

- **modelV2** (leads, laneLines, roadEdges, position) and anything built on
  it (planned paths, lateral offsets added to a path): calibrated frame,
  [Forward, **Right**, Down] (`common/transformations/README.md`).
- **Every `yRel`** (`RadarData` points, `radarState` leads, `stereoObjects`,
  side/rear detections, radar_zones objects, gridd grids): car frame,
  **left positive** (`log.capnp` `LeadData.yRel`).
- **Flip once, where one feeds the other**, as `radard.py` does:
  `yRel = -lead.y[0]`. Never flip a value twice, never compare across frames.

EOP code that broke this, fixed 2026-09-28: `desire_helper._evaluate_gap`
and `lc_lead_handoff._pick_adjacent_lead` (model lead y read as left),
`aeb._collect_objects` (negated an already-left `yRel`), gridd
`_fuse_modeld_detections` stereo check (`X + y`), pathd `lat_nudge` (y-left
offset added to a y-right path), SOC (read `yRel` as right),
`path_corridor_fusion` (y-left fallback/validation on y-right model edges).

### Build & Platform

- **SConstruct**: RK3588 maps to `aarch64`
- **launch_openpilot.sh**: RK3588 hardware only
- **Architecture decisions**: Prefer minimal overlays—revert to upstream, then re-apply only EOP additions

## Key Files

| File | Purpose |
|------|---------|
| `ARCHITECTURE.md` | System design + daemon overview |
| `SYSTEM_CONFIG.md` | Hardware specs (RK3588) |
| `docs/eop/` | Feature documentation |
| `docs/eop/04_Integration/BLE_DESIGN.md` | BLE/NCP architecture (dual transport) |
| `docs/upstream-audit/DELTA_AUDIT.md` | Audit trail + revert plan |
| `AGENTS.md` | Agent edit boundaries (local-only, read this first) |
| `test.sh` | Local dev gate before pushing |
| `.pre-commit-config.yaml` | Pre-commit hooks |
| `cereal/{log,custom}.capnp` | Message definitions |
| `common/core_config.py` | CPU affinity mapping |
| `system/bluetoothd/ble_gatt.py` | BLE GATT server (Nordic UART, iOS + Android) |
| `system/bluetoothd/spp.py` | Classic SPP server (RFCOMM, OBD scanners) |
| `selfdrive/adaptd/adaptd.py` | Adaptive driving daemon (renamed from elm327d) |

## Branch model

This is the **foundation** branch. It keeps the old C++/Qt UI and supports
**ExoPilot 01M (RK3588) hardware only**. The two UI branches sit on top of it
and take foundation improvements by **rebasing**, not by cherry-picking:

```
dev/EOP10 ──┬── dev/01M   PyQt5 UI, classic openpilot layout, RK3588 / 1024x600
            └── dev/02M   PyQt5 UI, nagasware layout,          RK3576 / 1600x600
```

- **Each branch carries one board.** EOP10 and 01M are RK3588-only; 02M is
  RK3576-only. `system/hardware/rockchip_base.py` holds what the two boards
  share, and each board class is a sibling on top of it —
  `RK3576Hardware` used to subclass `RK3588Hardware`, which made 01M's class
  load-bearing for 02M and the two inseparable. Do not reintroduce that.
- **The supported hardware is ExoPilot's, not "Rockchip".** The authoritative
  list is `exo-elec/exopilot`'s `hal/hal/platform/boards.py` (`BOARD_DATA`)
  and `bsp.py`:

  | board | SoC | device-tree compatible | consumer |
  |-------|-----|------------------------|----------|
  | ExoPilot 01M | RK3588 | `exopilot,exp01`, `rockchip,rk3588` | this branch + `dev/01M` |
  | ExoPilot 02M | RK3576 | `rpdzkj,rp-rk3576`, `rockchip,rk3576` | `dev/02M` |
  | ExoPilot 03M | RK3688 | — | **not supported yet**; DoraPilot's, not this tree's |

  Only those two boards. Rockchip's other parts (RK356X and the rest) are not
  ExoPilot hardware and must not appear in search paths or board maps, and
  RK3688 stays out until it is actually supported. Nothing here should be
  written to pick up "any future Rockchip board for free" — adding a board is
  a deliberate act.

- **Four lists name the board a branch builds for.** A rebase from EOP10
  brings EOP10's values with it, so re-point them on every rebase:

  | file | list | EOP10 / 01M | 02M |
  |------|------|-------------|-----|
  | `SConstruct` | `ROCKCHIP_SOCS` | `rk3588` | `rk3576` |
  | `system/hardware/rk_device_id.py` | `SUPPORTED_SOCS` | `rk3588` | `rk3576` |
  | `tools/convert_models_to_rknn.py` | `RKNN_TARGETS` | `rk3588` | `rk3576` |
  | `Jenkinsfile` | `EOP_BOARD` | `rk3588` | `rk3576` |

  Everything else derives from the board that is running, and **no daemon may
  spell a board name**. Board data comes from
  `HARDWARE.hal_module("<kind>")`, which resolves
  `hal.platform.<board>_<kind>` via the board's `HAL_PREFIX`; RKNN artifact
  paths come from `rknn_soc_tag()`, because an RKNN binary is valid for
  exactly one SoC. Hardcoding `rk3588` is what made `pigeond` fail to import
  on 02M and silently dropped the MPP hardware encoder from its build.

- **A fix that is not about the UI belongs here**, so both branches inherit
  it. Daemons, cereal, params_keys.h, systemd units, SConstruct outside the
  Qt block. Fixing it on 01M or 02M instead leaves the other branch broken.
- **UI fixes belong on the branch they apply to.** The C++ UI is this
  branch's; `selfdrive/ui/` is theirs.

### Rebasing the UI branches onto an improved EOP10

```bash
git fetch origin dev/EOP10
git checkout dev/01M && git rebase origin/dev/EOP10
git checkout dev/02M && git rebase origin/dev/EOP10
```

Both then need a force-with-lease push, since a rebase rewrites commits.

**Expect modify/delete conflicts.** The UI branches delete the whole C++ UI
tree, and 02M swaps the hardware layer. Any EOP10 commit touching a file a UI
branch deleted conflicts on every rebase. The resolution is usually "the UI
branch's deletion wins" — `git rm` and continue — but read the incoming
change first: a *backend* fix that happens to live in a deleted file needs
porting to the Python equivalent rather than dropping.

After rebasing, re-run `./test.sh` on each branch.

## Daemon Naming

| Old name | New name | Note |
|----------|----------|------|
| `elm327d` | `adaptd` | Renamed 2026-05-30 — never implemented ELM327; is a driving policy daemon |
| `radar3d.py` (camera+radar fusion) | `radard.py` | Renamed 2026-08-16 — matches upstream openpilot's name. `radar3d.py` (now `system/radar3d/`, the sensor-producer layer like `ubloxd`) is the long-range UART radar *producer* daemon, not the fusion daemon; see New Features below |

## UI

The C++/Qt UI is gone. `selfdrive/ui/` is a Qt Widgets UI written in
Python and run as a `PythonProcess`, and `dev/02M` uses the same module.

- **Design is unchanged on 01M.** Sidebar, offroad home, the left-nav settings
  window and the onroad HUD all reproduce the C++ layout they replace.
  `dev/02M` is where the new design lives (top-tab settings, swipeable side
  panels, 1600x600 chrome).
- **`views/` and `main.py` are the intended divergence** between 01M and 02M.
  Within `components/` each branch also owns the files that *are* its layout:
  `alerts.py`, `hud.py` and `theme.py` exist only here; `chrome.py`,
  `panels.py`, `panel_widgets.py` and `factory.py` only on 02M. Every file
  that exists on **both** branches — `qt.py`, `state.py`, the common
  `components/`, `settings/`, `styles/`, `views/panels/` — is kept
  **byte-identical**, so a fix cherry-picks between them unchanged. Check
  that before editing one of those files, and check it both ways: a
  one-directional diff misses a file that exists only on the other branch.
- **Binding**: **PyQt5 only**. There is no PySide fallback — carrying one
  meant checking every spelling against two bindings, and it leaked anyway
  (scoped vs unscoped QDBus enums, QSpinBox float coercion). Import Qt names
  from `selfdrive/ui/qt.py` rather than from `PyQt5` directly: it is the
  one place a future Qt move gets edited. Write `Signal`, not `pyqtSignal`.
  Note PyQt5 is GPLv3 or a paid Riverbank licence while openpilot is MIT, so
  the licence question has to be settled before anything is distributed —
  a recorded choice for a research project, not an oversight.
- **Run it**: `PYTHONPATH=. python3 -m openpilot.selfdrive.ui.main --demo`
- **Test it**: `./test.sh` now includes the UI suite, or directly with
  `QT_QPA_PLATFORM=offscreen python3 -m pytest selfdrive/ui/tests
  -c selfdrive/ui/tests/pytest.ini --noconftest`
- **Not yet verified on hardware**: the VisionIPC/EGL camera path and which Qt
  platform plugin the device runs. See `docs/eop10/EOP10_PORT_PLAN.md` P1.

## New Features

**RKNN detects, the card segments (2026-09-27):**
- **RKNN runs YOLOv8 object detection for every camera** — road (monod, 20 Hz),
  02M telephoto (monod), side left and right (sided), rear (reard), all at
  the 20 Hz foundation rate — nothing is rate-reduced — through `selfdrive/sided/yolo_detector.py`.
  Side/rear/tele use NPU core 1, freed when segmentation left RKNN; road uses
  the last core. RKNN is on the die, so what BSD, RCTA and the front object
  list are built from cannot disappear with a card. RKNNLite's `core_mask` is
  a bit mask: pass `1 << core`, never the index (01M monod passed 2 = core 1).
- **The camera-tier card (Hailo-8 or DX-M1M, standard on 01M and 02M, chosen
  on price) runs drivable-area segmentation for every camera**:
  TwinLiteNet+ Large 384x640 (BDD100K drivable area + lane lines, MIT,
  1.94 M params — one context on a Hailo-8), the same network compiled from
  one ONNX for both cards by `tools/card_drivable_model.py` (export → calib
  → `compile-hailo` with Hailo's DFC / `compile-dx` with DeepX's DX-COM;
  both compilers need vendor logins, x86-64). Neither vendor zoo ships it.
  The export folds the two heads into one `[drivable, lane]` score output
  and scales 0-255 input itself, so no compiler can reorder outputs.
  `inferenced` letterboxes each frame and returns a 540x960 OTHER/DRIVABLE/
  LANE map (`system/inferenced/drivable.py`); the DX backend adapts to a
  `.dxnn` taking NHWC uint8 or NCHW float. Chosen 2026-09-27 over the
  Cityscapes networks (consumers only asked "where is the road") and over
  YOLOPv2 (24.8 M params, multi-context on Hailo-8, too big to fine-tune).
- **Building the card artifacts is one command** on an x86-64 machine with
  the vendor compilers: `tools/card_drivable_model.py all --images <frames>`
  (fetches `large.pth`, or `--weights` a fine-tune; skips a compile whose
  compiler is missing, and says so; installs into `models/`).
- **Side/rear camera positions (2026-09-27)**: `SideCameraGeometry.R_cv`
  was not a vehicle→camera rotation (identity at yaw 0: optical axis on
  vehicle +z), so every ray pointed up and sided's BEV positions — which
  `radar_zones` pairs with BLE radar tracks — were never right. Fixed, and
  a box whose bottom is at/above the horizon is ranged by class width
  instead of a negative ground intersection. `reard` now uses the same
  ground-plane reprojection instead of `400 / box height`, and publishes
  lateral offset in metres — it used to publish a −1..1 image position as
  metres. Tests: `sided/tests/test_ground_plane.py`.
- **Side/rear calibration lives in exopilot (2026-09-27)**. The old 6-DOF
  epipolar `side_camera_calibrator.py` reported "converged, 0.0 px" on
  wrong mountings (pitch −58.5° for a real −10°) and is deleted, along with
  the `SideCameraCalib_*` params it wrote.
- **`camera_calibrationd.py` wraps `calibrationd.Calibrator`, one per camera,
  instead of reinventing block averaging/moving-average decay/validity
  checks (its old `CameraCalibrationState`).** Only `road` is ever fed
  cameraOdometry (openpilot's egomotion is single-camera), so it is the
  only one that actually moves; `liveCalibration` is exactly that
  Calibrator's own `get_msg()` -- upstream's format, unmodified, including
  fields the old code silently dropped (`wideFromDeviceEuler`,
  `roadTransformTransStd`). `calibrationState` (EOP's own multi-camera +
  side/rear summary) is published on top, unchanged in shape. Tests:
  `selfdrive/locationd/test/test_camera_calibrationd.py`.
  exopilot's `hal.calibration` owns side/rear calibration; openpilot only
  feeds it. `camera_calibrationd` (`side_rear_calibration.py`) hands it the
  front camera's `cameraOdometry` (scaled by wheel speed, moved to base_link),
  the road camera's own calibration, and the uvcd frames. It stores each
  camera's link in `sensors_tf.yaml` once it reads calibrated, and reports
  progress in `calibrationState.cameraCalibrations` (ROS roll/pitch/yaw with
  pitch positive down, and height above the road). sided/reard place their
  cameras from that tf tree (`bev_reprojector.hal_geometry`). Without hal (a
  dev PC) they fall back to the nominal mounting in `bev_reprojector.py`.
  - **Cameras**: 1 MP AHD, 1280x720, behind AHD-to-UVC bridges. M12
    fisheyes (equidistant): side 2.8 mm (98° H; the vendor's "120°" is
    diagonal), rear 1.8 mm (153° H; the vendor's "170°" is diagonal). The
    lens is a spec, as for the front camera, and is never solved.
  - **tf**: base_footprint → base_link (**the ExoPilot unit's centre**) →
    `camera_<name>_link` → optical frame (ROS REP-103/105).
  - **Where parts go**:
    - the unit is on the **front** windshield behind the rear-view mirror;
    - the sides are at the old-style side turn-lamp / side-badge spot on each
      front fender, looking back and turned a little outward;
    - the rear camera is inside, behind the **back glass** (rear windscreen),
      top centre, under the high-mounted stop lamp.

    Calibration starts from these and is clamped to the HAL's `MOUNT_LIMITS`.
  - x/y cannot be seen from motion and stay nominal. The Device panel's
    "Side/Rear Camera Calibration" row (dev/01M, dev/02M) shows per-camera
    status and resets via `EOPSideRearCalibReset`.
  - **Camera ↔ BLE radar association has one owner: `controlsd`'s
    `radar_zones._associate_camera_objects`** (reads `sideDetections`/
    `rearDetections` directly, same-side and rear gating, noisy-OR
    confidence). Side-camera handover (left↔right) is `sided`'s
    `HandoverManager`. gridd does not ingest side/rear detections: a second
    association there (tried 2026-09-28, reverted) duplicated every object
    and, via a `-y` sign slip, mirrored side cameras onto the wrong side.
  - Tests: `selfdrive/locationd/test/test_side_rear_calibration.py`
    (uses `../exopilot/hal` when present, skips otherwise).
- **Off-road / Thai roads**: BDD100K is paved US roads.
  `tools/finetune_drivable.py` fine-tunes on BDD100K + IDD (unmarked rural
  roads) + ORFD (off-road) + ExoPilot's own labelled drives; sets without
  lane labels train the drivable head only. IDD is non-commercial research —
  check licences before shipping weights. See `DRIVABLE_FINETUNE.md`.
- New daemon `segd` is the card's only segmentation client and feeds it by
  what the drive needs, not round-robin (`segd/schedule.py`: road always,
  wide/tele by speed, side cameras with a blinker / blind-spot flag / side
  detection, rear in reverse or with a rear detection; an idle tick runs no
  job). It publishes `monoSegments` (one `MonoSegment` per camera, for `rcd`)
  and `drivableBev` (each camera's map on gridd's BEV grid, one camera per
  message). `gridd` fuses the maps (`gridd/drivable_fusion.py`: log-odds by
  camera reliability, decays to unknown, moves with the car) into its cost
  layer and no longer touches the card or the road frame. SceneSeg and
  PP-LiteSeg no longer run on RKNN. With no card, segd idles.
- **Card failure degrades, nothing takes over** (user decision, 2026-09-27):
  no RKNN substitute, no stepping down to another model, no moving to the
  other card. A card that cannot load its model is marked degraded in
  `inferenced` and its ids answer "not available"; gridd runs without a road
  mask and sets `gridStatus.segmentationDegraded` (custom.capnp @9) -- never
  `fault`, which would disable openpilot. surfaced's drivable-area costmap
  needs no card and carries on. The artifact lists per card are install
  preference (first file on disk), not a failover chain. The eGPU is
  different: Chestnut keeps its one-way switch to the RKNN driving model.
- **DX-M1M, not DX-M1**: M.2 2242, 25 TOPS INT8, 2 GB LPDDR4x, PCIe Gen3 x2,
  3 W typical, -40..85 °C. Needs a 42 mm standoff in the 2280 socket.
- Bugs fixed on the way: monod crashed in `__init__` (`messaging.SubManager`
  does not exist) and its road YOLO never returned anything (read a
  `detections` key RKNN does not produce, fed an unresized frame); monod's
  `monoSegments` set a `timestamp` field the schema lacks; `rcd` read
  `hasRoad` off the segments list instead of an entry; `inferenced` ran one
  job per 10 Hz tick (SubMaster returns one message per update).
- **Traffic lights (TLSC) come from monod's road YOLOv8**, COCO class 9 on
  the road camera only (`ROAD_COCO_CLASSES`): HSV lamp colour
  (`monod/traffic_light_classifier.py`), ranged by the box's long side
  (~1 m head), sent in `monoDetections` (`trafficLightState`/`Confidence`,
  new custom.capnp fields @20/@21), passed on by gridd in `stereoObjects`
  as `obstacleType = trafficLight`. Before this TLSC only ever acted in
  simulation: its detector, `gridd/yolo_objdet.py`, was never imported
  (now deleted). Lights skip monod's fusion, never de-duplicate a lead in
  gridd, and `radar_zones` ignores them.
- **gridd crashed on its first detection**: it wrote class names (`"car"`,
  `"lead"`) into the `ObstacleType` enum, which capnp rejects. `obstacle_type()`
  maps them. The planner now holds TLSC's target between `stereoObjects`
  updates (dropped after 0.5 s) instead of applying it only on frames where
  one arrived.
- **Only inferenced opens the card or the eGPU**: `HALConfig(own_exclusive_devices=True)`
  is set there alone. Any other process's local HAL (the RKNN detectors in
  monod/sided/reard, gridd) skips Hailo/DX/eGPU — before this, every
  `get_hal()` also grabbed the card.
- Plan, budget and bench list: `docs/eop/05_Features/CAMERA_ACCEL_TIER.md`.

**USB eGPU = openpilot's Chestnut driving model only (2026-09-27):**
- The eGPU runs exactly one model: upstream openpilot's Chestnut big driving
  model (`big_driving_supercombo`, modeld's eGPU runner). `egpu.py`'s
  `EGPU_ALLOWED_MODELS` makes `load_model()` refuse anything else, and
  `inferenced` rejects EGPU jobs for any other model id.
- Camera and rule-based Autoware-style models (detection, segmentation) are
  **not** eGPU work: detection is on RKNN, segmentation on the Hailo-8 /
  DX-M1M card (`BackendType.ACCEL`). The 2026-08-23 eGPU camera-shadow path
  (`side_yolo_egpu`, `rear_yolo_egpu`, `*_seg_egpu`, `egpu_camera_detector.py`,
  the six `EOP*EGPUMode` params, and the shadows in sided/reard/gridd/monod)
  was removed — do not bring it back. `EGPU_CAMERA_SHADOW.md` is history.
- `tinygrad_repo` is pinned to the official stable `v0.13.0` tag; do not follow
  `master`. The deployed runtime must use Python 3.11+ (EOP `.venv` is 3.12).
- `inferenced` is the sole eGPU owner; `modeld` owns driving state and parsing.
  Production driving inference preserves openpilot `modeld`'s runners,
  temporal buffers, output parsers and `modelV2` semantics.
- Chestnut's one-way failover, as upstream: keep the RKNN small model
  loaded/warm, reject activation unless the external model loads and warms,
  switch to RKNN on exception/timeout/non-finite output/unplug, soft-disable
  if engaged, and never auto-retry eGPU onroad. See
  `docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md`.
- Driving artifacts are asymmetric by design: the eGPU runs official upstream
  Chestnut big supercombo; the local fallback on RKNN is Bukapilot's KA2
  split pair, `driving_vision.rknn` + `driving_policy.rknn`
  (`rknn_driving_runner.py`, hashes in `models/MODEL_MANIFEST.md`). The
  2026-08-23 audit's monolithic Bukapilot `supercombo.rknn` is not what runs.
  Converted and validated separately for RK3588 and RK3576; never reuse an
  RKNN binary across target SoCs.
- The EOP10 corner-radar baseline is BLE-only: ESP32_RADAR `dev/TR13`
  publishes `radar2d`; WiFi point-cloud radar support belongs to the 02M layer.

**radar3d — long-range UART radar replaces the never-wired OEM CAN radar (2026-08-16):**
- The vehicle has no real forward OEM radar — only a 2D blind-spot corner
  radar (`radar2d`, unchanged). `card.py` used to source the `radar3d`
  socket from `opendbc.car.tesla.radar_interface.RadarInterface`, decoding
  a Continental ARS4-B/TC375-BrownPanda CAN stream that was never actually
  wired to real hardware — a second, more complete decoder
  (`continental_interface.py`) existed alongside it and was never imported
  either. Both removed; `card.py` no longer touches radar at all.
- New standalone producer `system/radar3d/radar3d.py` (`Radar3DD`,
  Class-D pattern) reads a long-range UART radar sensor directly (77GHz,
  up to 120m, onboard CFAR/AoA — no local DSP needed) and publishes
  `car.RadarData` on the `radar3d` socket at 20Hz. No capnp changes —
  `RadarPoint`'s existing fields covered everything the sensor provides.
- `radar3d` feeds two independent, complementary consumers: `radard.py`
  (renamed from this repo's old `radar3d.py` — see Daemon Naming above)
  for ACC ego-lane lead tracking, and `gridd.py`'s pre-existing
  `_fuse_radar3d()` for forward adjacent-lane (merge/cut-in) awareness —
  the latter already existed and needed zero changes.
- Driver lives in `../exopilot/hal/hal/drivers/radar/radar3d.py` (`Radar3D`,
  `Radar3DConfig`) — low-level sensor porting can't live in this public
  repo, same ownership split as BGT60TR13C. See
  `docs/eop/04_Integration/TC375_RADAR.md` for the full wire contract,
  sign-convention bench-verify items, and file map.
- EOP10 has no `radar4d` daemon. Its ESP32 corner-radar input is BLE-only
  `radar2d` from `dev/TR13`; WiFi point-cloud radar support is a 02M board-layer
  addition.
- 01M WiFi (2026-09-24): the RTL8822CE PCIe card serves only the vehicle LAN
  (no corner-node hotspot), so `wlan0` uses 5GHz. exopilot's
  `setup_wifi_lan.sh` (run by `setup_rk3588.sh`) writes
  `/etc/exopilot/wifi-band.conf` (`LAN_BAND=a`); the WiFi screens pin new
  networks seen on 5GHz to band `a` through `common/wifi_band.py` (with a
  5GHz BSSID when one is pinned); 2.4GHz-only networks stay joinable.

**BRSC — Bumpy Road Speed Controller (2026-08-03):**
- Reduces cruise speed / positive accel on rough pavement, detected from vertical
  IMU acceleration (`accelerometer` service, not vision — complements VTSC/MTSC
  which only see path curvature). Real-world tuned: isolated single-spike events
  (railroad crossing, one pothole) don't trigger a slowdown; sustained roughness
  (washboard/broken pavement) does; recovery ramps back over a few seconds instead
  of stepping, and a retriggerable hold (capped) rides out closely-spaced bumps.
- Pure policy lives in `nagaspilot/controls/ngp_brsc.py` (`NGPBRSC` class, zero
  cereal/Params deps) so the identical file ports to `dev/NGP10` and `dev/EDP10`.
  `NGP`-prefixed (not `EOP`-prefixed) param `ngp_lon_brsc` is a deliberate
  exception to the `EOP<Feature><Param>` rule, made for features shared verbatim
  across branches — see `docs/eop/03_Software/Controllers/BRSC.md`.
- EOP10 wiring: `selfdrive/controls/plannerd.py` (subscribes `accelerometer`) +
  `selfdrive/controls/lib/longitudinal_planner.py` (same `_apply_speed_limit`
  pattern as SQSC/RCD/TLSC — applied after VTSC/MTSC's curve-speed blend).
- capnp: `LongitudinalPlan.ngpBrscActive/ngpBrscSpeed/ngpBrscRoughness` (`log.capnp` @66-68).
- Tests: `nagaspilot/tests/test_ngp_brsc.py` (pure-Python, no capnp/build needed).

## Recent Bug Fixes

**`opendbc_repo` pin predated the BYD port — submodule (2026-09-21):**
- The `opendbc_repo` gitlink sat at `49d4849` (2026-08-23), 56 commits behind
  `exo-elec/opendbc` master and *before* `2236aeb`, the commit that adds BYD. At that
  pin the submodule has no `opendbc/car/byd/`, no `byd_*.dbc` and no
  `safety/modes/byd.h`, so none of the BYD protocol work was reachable from an
  openpilot build — the car simply did not exist as far as this tree was concerned.
  Bumped to `6c0fbcd`. `49d4849` is an ancestor, so it was a stale pin, not a divergence.
- Found by a cross-repo protocol audit covering `BYD_Atto3`, `opendbc` and this tree;
  see `opendbc/docs/BYD_ATTO3_QZWF_REFERENCE_PORT.md` and
  `BYD_Atto3/DOC/protocol_consistency_audit.md`. **When changing anything that depends
  on an opendbc car port, check the gitlink is recent enough to contain it.**

**Duplicated vehicle-identification tables drifted — obd2d/bluetoothd (2026-09-21):**
- `VEHICLE_WMI_MAP`, the VIN→type lookup and `is_chinese_ev()` were defined twice, in
  `selfdrive/obd2d/vehicle_db.py` and `system/bluetoothd/protocol.py`. The maps stayed
  identical but the *fallbacks* diverged: `protocol.py` returned `generic_ev` for an
  unlisted `L` WMI where `vehicle_db.py` returned `generic_ice`, so the same VIN
  selected a different Mode 22 PID table over BLE than over OBD.
- `vehicle_db.py` owns them now (`detect_vehicle_type_from_wmi()`), `protocol.py`
  imports, and the EV fallback applies on both paths.

**`_FuseHost` test double out of sync with `GridD` — gridd (2026-08-10):**
- `fix(gridd): wire up load_corner_poses()` (c9b5df77f) moved
  `_fuse_radar2d_objects`'s corner-pose lookup from the `_R2D_CORNER_POSE`
  class constant to `self._r2d_corner_pose`, an instance attribute set in
  `GridD.__init__()`. `test_fuse_radar2d.py`'s `_FuseHost` — a deliberate
  lightweight stand-in that mirrors `GridD`'s class-level constants without
  running its (heavy) `__init__` — was never updated, so every test using it
  hit `AttributeError: '_FuseHost' object has no attribute
  '_r2d_corner_pose'`. Fixed by adding `_r2d_corner_pose = GridD._R2D_CORNER_POSE`
  to `_FuseHost`, matching the same placeholder-table fallback
  `GridD.__init__` itself falls back to when the shared registry is absent.

**Hailo-8 multi-process VDevice race — sided/reard (2026-08-10):**
- `sided` (side_left/side_right) and `reard` (rear) run as separate concurrent
  processes but share one physical Hailo-8 over the same USB hub. Both used to
  call `InferenceClient.hailo()` directly, each creating its own `VDevice()`;
  HailoRT only grants one process exclusive ownership (no multi-process
  scheduler service running), so whichever daemon started second failed
  `initialize()` silently and its CPU fallback returned `[]` — only one of
  side/rear ever actually got YOLO detections.
- Fixed by routing `HailoSideDetector` (`selfdrive/sided/hailo_side_detector.py`,
  shared by `sided.py`/`reard.py`) through `InferenceClient(daemon_name,
  use_ipc=True).submit_job(BackendType.HAILO_8, ...)` so only `inferenced`
  ever touches `Hailo8Backend`/`VDevice`; added `yolo_side` → `models/hef/
  yolov8n.hef` to `inferenced.py`'s `MODEL_REGISTRY` (its path resolver
  previously assumed every model was `{fmt}/{ext}` templated for rknn/onnx,
  which doesn't apply to fixed-format `.hef` files). Also made
  `InferenceClient._hal` lazy (`system/inferenced/client.py`) so constructing
  a client no longer eagerly initializes every local backend — the
  side-effect that caused the eager per-process `VDevice()` grab in the first
  place. See `docs/INFERENCED_ARCHITECTURE.md` "Hailo Backend" for the
  IPC-only rule.

**BLE / Bluetooth stack (2026-05-31):**
- `ncp_session.py`: multiple `PubMaster` instances for same service crashed msgq on boot — fixed with one shared session per process
- `ncp_session.py`: `NCPSession.start()` not idempotent (spawned duplicate telem threads); fixed with guard
- `ncp_session.py` / `spp.py`: missing `voiceCommandRequest` in `cereal/services.py` — PubMaster construction crash
- `spp.py`: socket write race — `Client._send_lock` and `_send_locked` were independent; fixed with shared lock per connection
- `ble_gatt.py`: GATT TX chunk interleave — `PropertiesChanged` emitted outside `_lock`; fixed
- `ble_gatt.py`: `StartNotify`/`StopNotify` not wired to session — route state never reset between connections
- `bluetoothd.py`: `int(bytes)` TypeError for `EOPBluetoothPairWindow` param
- `protocol.py`: command codes 0x22–0x2F did not match Dart `frame_protocol.dart` — all NCP commands silently misrouted

**Earlier (2026-05-30):**
- `spp.py`: missing `import struct`, missing `_handle_voice_intent` / `_handle_driving_profile` handlers
- `pairing_agent.py`: wrong BlueZ capability (`DisplayYesNo` → `DisplayOnly`), missing `DisplayPasskey`
- `bluetoothd.py`: GLib main loop never ran
- `adaptd`: not in `process_config.py`; `EOPAdaptdEnabled` undefined in `params_keys.h`
- `BluetoothPairingPin/Addr/Active`, `EOPDeviceName`, `EOPAdaptdEnabled` missing from `params_keys.h`

See `docs/upstream-audit/DELTA_AUDIT.md` Step 15 for earlier Python runtime bugs (thermald, sqsc, camera_calibrationd, surface_quality_db, eop_utils).

## Lint & Code Quality (2026-08-12)

A focused lint cleanup landed on `dev/EOP10`:

- `./test.sh` (focused gate) is green.
- `./test.sh --full` is green for ruff, shebang, large-file, merge-marker, and codespell checks; only mypy debt remains (553 errors in 149 files, down from ~740/203).
- Real runtime bugs fixed: `gridd/yolo_objdet.py` wrong result attributes, `surfaced/surfaced.py` missing `CellState` aliases, `system/hardware/rk_device_id.py` mixed bytes/str reads.
- ~200 shebang files marked executable; pre-existing large assets excluded from the 120 KB gate; codespell ignore-list expanded for EOP/Rockchip domain terms.

See `docs/eop/CODE_QUALITY_LINT_CLEANUP.md` for the full report and recommended next steps.

---

**Last updated**: 2026-09-21  
**Branch**: dev/01M (renamed from dev/EOP10, 2026-09-10)

---

## Local Inference Stack

This workstation runs a two-tier local inference stack. All code changes use local GPU+CPU automatically — no flag needed. Use `// --claude` to skip local and go cloud-only.

```
:8000  GPU  Qwen3-Coder-30B AWQ    (RTX 3090) — code generation, 20-30 tok/s
:8001  CPU  DeepSeek-R1-70B Q8_0   (i9-13900K) — deep review, 2-4 tok/s
:8002  LiteLLM proxy               — unified endpoint, auto-failover
```

### Usage

```
# Local (free — GPU codes, CPU reviews in parallel)
You: "fix the capnp list assignment in adaptd.py"

# Deep review (safety-critical, arch, auth)
You: "review the BLE session state machine @deep"

# Cloud only
You: "design the new daemon lifecycle architecture // --claude"
```

### Task Tiers

| Tier | Type | Review timeout |
|------|------|---------------|
| 1 Quick fix | rename, typo, comment | 300s |
| 2 Routine | fix bug, add function, test | 900s |
| 3 Moderate | refactor, new module | 1800s |
| 4 Deep | arch, safety-critical, auth | 2400s |

Add `// @deep` to force Tier 4 on any task.

### Check Status

```bash
curl -s http://localhost:8002/v1/models \
  -H "Authorization: Bearer sk-local-dev-not-secret" | jq '.data[].id'
```

See `~/RTX3090/` for full setup and service management.
