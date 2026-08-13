# CLAUDE.md

Guidance for Claude Code when working on this openpilot fork.

## Project Overview

**ExoPilot (EOP)** — Advanced ADAS for Rockchip RK3588.

- **Codebase**: OpenPilot fork + EOP-specific daemons, controllers, UI
- **Platform**: RK3588 (ExoPilot 01L / 01M) — the only platform openpilot supports. ExoPilot 02M (RK3576) is VisionPilot's; ExoRobot 01H (RK3588 16GB, HumRobot) is in `~/robot/exorobot`
- **Suffix = RAM**: L=4GB / M=8GB / H=16GB; PCIe accel (camera-tier Hailo-8/DX-M1 only) is a runtime-detected plug-in
- **Status**: In development — dev PC testing phase (not hardware-deployed)

## Prerequisites (on real hardware)

ExoPilot BSP must be installed first before openpilot:
```bash
sudo ~/pilot/exopilot/scripts/install/setup_rk3588.sh && sudo reboot   # ExoPilot 01L/01M
```

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

## Daemon Naming

| Old name | New name | Note |
|----------|----------|------|
| `elm327d` | `adaptd` | Renamed 2026-05-30 — never implemented ELM327; is a driving policy daemon |

## New Features

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

**Last updated**: 2026-08-12  
**Branch**: EOP10

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
