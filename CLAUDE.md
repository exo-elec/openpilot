# CLAUDE.md

Guidance for Claude Code when working on this openpilot fork.

## Project Overview

**ExoPilot (EOP)** — Advanced ADAS for Rockchip RK3588.

- **Codebase**: OpenPilot fork + EOP-specific daemons, controllers, UI
- **Platform**: **RK3576 only** (ExoPilot 02M). 01M/RK3588 lives on `dev/01M`
  and `dev/EOP10` — see **Branch model** below. As of 2026-09-13 this branch
  carries no RK3588 support at all: `system/hardware/rk3588/`,
  `PlatformType.RK3588`, `Hardware::RK3588()` and the RK3588 camera/NPU
  entries are gone. Asking for `rk3588` fails loudly rather than falling
  back. ExoRobot 01H (RK3588 16GB, HumRobot) is in `~/robot/exorobot`
- **Suffix = RAM**: L=4GB / M=8GB / H=16GB; PCIe accel (camera-tier Hailo-8/DX-M1 only) is a runtime-detected plug-in, works unchanged on either SoC
- **Status**: In development — dev PC testing phase (not hardware-deployed)

## Prerequisites (on real hardware)

ExoPilot BSP must be installed first before openpilot:
```bash
sudo ~/pilot/exopilot/scripts/install/setup_rk3576.sh && sudo reboot   # ExoPilot 02M
```
The 01M equivalent (`setup_rk3588.sh`) belongs with `dev/01M`.

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
   - Install pre-commit hooks: `uv pip install pre-commit && pre-commit install`

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

`dev/EOP10` is the **foundation**: it keeps the old C++/Qt UI and supports
**ExoPilot 01M (RK3588) hardware only**. This branch and `dev/01M` sit on top
of it and take foundation improvements by **rebasing**, not cherry-picking:

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
- **UI fixes belong on the branch they apply to.** `selfdrive/ui/` is
  this branch's and 01M's; the C++ UI is EOP10's.
- **`views/` and `main.py` are the intended divergence** between 01M and 02M.
  Within `components/` each branch also owns the files that *are* its layout:
  `chrome.py`, `panels.py`, `panel_widgets.py` and `factory.py` exist only
  here; `alerts.py`, `hud.py` and `theme.py` only on 01M. Every file that
  exists on **both** branches — `qt.py`, `state.py`, the common
  `components/`, `settings/`, `styles/`, `views/panels/` — is kept
  **byte-identical**, so a fix cherry-picks between them unchanged. Check
  that before editing one of those files, and check it both ways: a
  one-directional diff misses a file that exists only on the other branch.

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
| `radar3d.py` (camera+radar fusion) | `radard.py` | Renamed 2026-08-16 — matches upstream openpilot's name. `radar3d.py` is now the long-range UART radar *producer* daemon, not the fusion daemon; see New Features below |

## UI

The C++/Qt UI is gone. `selfdrive/ui/` is a Qt Widgets UI written in
Python and run as a `PythonProcess`, and `dev/01M` uses the same module.

- **This branch carries the new design**: top-tab settings, swipeable side
  panels, 1600x600 chrome. `dev/01M` keeps the classic openpilot layout.
- **`views/` and `main.py` are the intended divergence** between 01M and 02M.
  Within `components/` each branch also owns the files that *are* its layout:
  `chrome.py`, `panels.py`, `panel_widgets.py` and `factory.py` exist only
  here; `alerts.py`, `hud.py` and `theme.py` only on 01M. Every file that
  exists on **both** branches — `qt.py`, `state.py`, the common
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
- **Translations**: `main.load_translation()` installs a `QTranslator` for
  `LanguageSetting` before the first widget is built — Qt resolves `tr()` when
  a string is used, so a widget constructed earlier keeps its English text. It
  reads `selfdrive/ui/translations/<stem>.qm`, compiled from the `.ts` sources
  by the `lrelease` step in `selfdrive/ui/SConscript` (`scons translations`).
  The C++ UI embedded those in a Qt resource; there is no resource system
  here, so they are read from disk.
- **Not yet verified on hardware**: the VisionIPC/EGL camera path and which Qt
  platform plugin the device runs. See `docs/eop10/EOP10_PORT_PLAN.md` P1.

## New Features

**Optional USB eGPU camera shadow path (2026-08-23):**
- `tinygrad_repo` is pinned to the official stable `v0.13.0` tag; do not follow
  `master`. The deployed runtime must use Python 3.11+ (EOP `.venv` is 3.12).
- `inferenced` is the sole eGPU owner. `sided` and `reard` submit IPC jobs with
  direct fallback disabled and keep independent model IDs, Params, sessions,
  health and queues. Side and rear are not one combined model.
- Only `off` and `shadow` modes exist. Existing Hailo/local detections remain
  authoritative; no eGPU output is connected to planning or control.
- Corner radars are not part of this camera pipeline: BLE `radar2d` on every
  board, plus the ESP32_RADAR `dev/v2` WiFi point-cloud add-on on 02M only.
- Future AutoSpeed, AutoSteer and AutoDrive ONNX experiments must be independent,
  lower-priority compatibility references. Semantic segmentation is the main eGPU
  expansion: front plus independent side and rear sessions. Production driving
  inference must preserve openpilot `modeld`'s vision/policy runners, temporal
  buffers, output parsers and `modelV2` semantics. See
  `docs/eop/05_Features/EGPU_CAMERA_SHADOW.md`.
- For an authoritative external driving model, follow upstream Chestnut's one-way
  failover: keep the RKNN small model loaded/warm, reject activation unless the
  external model loads and warms, switch to RKNN on exception/timeout/non-finite
  output/unplug, soft-disable if engaged, and never auto-retry eGPU onroad. EOP's
  single device owner remains `inferenced`; `modeld` owns driving state and parsing.
  See `docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md`.
- Driving artifacts are asymmetric by design: the eGPU target is official upstream
  Chestnut big supercombo; local fallback is the Bukapilot monolithic supercombo,
  converted and validated separately for RK3588 and RK3576. Never reuse an RKNN
  binary across target SoCs. First validate the exact Bukapilot ONNX on eGPU versus
  Bukapilot RKNN before introducing the different-generation upstream big model.

**radar3d — long-range UART radar replaces the never-wired OEM CAN radar (2026-08-16):**
- The vehicle has no real forward OEM radar — only a 2D blind-spot corner
  radar (`radar2d`, unchanged). `card.py` used to source the `radar3d`
  socket from `opendbc.car.tesla.radar_interface.RadarInterface`, decoding
  a Continental ARS4-B/TC375-BrownPanda CAN stream that was never actually
  wired to real hardware — a second, more complete decoder
  (`continental_interface.py`) existed alongside it and was never imported
  either. Both removed; `card.py` no longer touches radar at all.
- New standalone producer `selfdrive/controls/radar3d.py` (`Radar3DD`,
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
- No `radar4d` daemon runs today. The ESP32 corner-radar WiFi point cloud
  (ESP32_RADAR `dev/v2`, UDP 47000, decoder `hal.drivers.radar.radar4d`) is a
  02M-only add-on on top of the BLE `radar2d` baseline; only 02M hardware has
  the antenna for the corner-node WiFi AP.

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

**Last updated**: 2026-09-13  
**Branch**: dev/02M (RK3576 / ExoPilot 02M only)

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
