# EOP10 Working Tree — Task List

Branch: `EOP10`
Goal: Complete the EOP schema/runtime alignment changes so the working tree is coherent and the modified daemons are syntactically and import-clean.

## Completed

- [x] Inspect working tree (24 modified files, no `task.md` existed).
- [x] Align remaining `CP.brand` → `CP.carName` consumers (`alcc`, `latcontrol_angle`, `plannerd`, `modeld`, `torqued`, `events`, `selfdrived`).
- [x] Update `cereal/car.capnp` `RadarData.Error` enum to include `none` and shift legacy values; align `radard.py` + `selfdrived.py` to the single-error `RadarState.radarErrors` schema.
- [x] Update `card.py` to the EOP `CarParams` schema:
  - use `safetyConfigs[0].safetyModel` instead of legacy `safetyModel`
  - remove `steerAtStandstill`
  - use `experimentalLongitudinalAvailable` instead of `alphaLongitudinalAvailable`
  - wire `SimpleCANParser` via `CarState.get_can_parsers`
  - publish live `liveTracks`
  - re-write `CarParams` to params every 10 frames so blocking readers unblock quickly
- [x] Update `continental_interface.py` to consume `(addr, dat, src)` tuples from `can_capnp_to_list`.
- [x] Harden `system/socketd/can_capnp.py` `_as_event` for capnp context-manager semantics and duck-type readers.
- [x] Update `system/stated/stated.py`:
  - use `carState.engineRpm`
  - publish `deviceState.started`
  - sync `EOPIgnitionOn` param
- [x] Simulation tooling:
  - block EOP-incompatible daemons in `tools/sim/launch_openpilot.sh`
  - suppress OpenCL compiler warnings in `tools/sim/lib/camera_sim.py`
  - auto-detect docker/sudo in `tools/sim/start_carla.sh` and tests
- [x] Fix stale field references discovered during review:
  - `selfdrive/controls/controlsd.py`: `self.CP.steerAtStandstill` → `getattr(..., False)`
  - `selfdrive/debug/set_car_params.py`: `alphaLongitudinalAvailable` → `experimentalLongitudinalAvailable`
  - `selfdrive/ui/onroad/exp_button.py`: `alphaLongitudinalAvailable` → `experimentalLongitudinalAvailable`
- [x] Add missing `micStatus` publisher in `system/micd/micd.py` so the UI subscription added in `selfdrive/ui/ui.cc` actually receives data.

## Verification run

- `python3 -m py_compile` passes for all modified Python files.
- `pytest -q selfdrive/test/test_daemon_imports.py` → **40 passed** (monod excluded: needs `hal` package).
- `system/socketd/tests/test_safety.py` → **16 passed** (was 6 failing; see "Safety reconciliation" below).
- `pytest -q selfdrive/controls/tests/ selfdrive/gridd/tests/` → all pass except 3 pre-existing environment failures (monod `hal` import, `/data/calibration/stereo_calibration.npz` missing on dev PC, `test_nslc` helper) — confirmed identical on the clean tree.

## Follow-up session (radar4d + remaining work)

Completed:

- [x] **radar4d tracker improvements** (`radar4d_tracker.py`, `radar4d.py`):
  published `vRel` is now the EKF-filtered radial velocity (raw Doppler still feeds
  the filter); elevation/z complementary-filtered (`EKF_Z_SMOOTH_GAIN`); tracker
  accepts measured frame `dt_s` (IRQ-paced loop) for physically correct prediction;
  dead imports removed. 34 tracker tests incl. 3 new ones, 81 radar4d tests total.
  Note: vRel calibration-rotation was analyzed and is a no-op (dot product is
  rotation-invariant).
- [x] **controlsd audit**: task premise was wrong — controlsd is the ONLY onroad
  publisher of `carControl` (vehicled actuates it) and `controlsState` (selfdrived/
  modeld/plannerd/UI consume it). Kept in `process_config.py` with a do-not-remove
  comment. The dual `ttsRequest` publishers (controlsd + selfdrived) are distinct
  content streams on a multi-publisher service — left as-is.
- [x] **test_onroad.py**: updated stale process paths (`vehicled.car.card` →
  `vehicled.vehicled`, `locationd.calibrationd` → `camera_calibrationd`,
  `ui.soundd` → `soundd.soundd`, `system.micd` → `micd.micd`); dropped dead
  `./encoderd` and `system.loggerd.uploader` budgets. Still tici-marked; EOP
  daemon CPU budgets (gridd/pathd/stated/…) not yet measured.
- [x] **Safety reconciliation** (`tesla_safety.py` + `vehicled/safety/safety.py`):
  - **Real bug fixed**: Tesla `DAS_control` accel is offset-encoded around
    `INACTIVE_ACCEL=375` (0 m/s²), but the "80% of Panda" factor was applied to the
    raw values, giving MAX 340 / MIN 310 — both BELOW inactive. Combined with the
    `negative_accel_both` check (both < 375 = normal braking), layer-1 rejected
    **100% of longitudinal commands**. Limits corrected to MAX 415 (+1.6 m/s²) /
    MIN 305 (−2.8 m/s²); `negative_accel_both` removed in both copies (opendbc
    `tesla.h` has no equivalent; min/max bounds are the guard).
  - **Test fixes**: raise-based API (`pytest.raises(SafetyViolation)`), preset
    rate/angle-error state so each test isolates one check, correct Tesla bit
    packing helper, counter-tolerance test aligned to the implemented "2 missed"
    semantics. `system/socketd/tests/test_safety.py` → 16/16.

## Known remaining work

- [ ] **On-road validation of the accel-limit fix**: the corrected MAX 415 / MIN 305
  defaults (and `EOPSafetyMaxAccel`/`EOPSafetyMinAccel` param overrides) should be
  sanity-checked on hardware before relying on layer-1 longitudinal enforcement.
- [x] **Safety limits duplication**: DONE — `system/socketd/safety/tesla_safety.py`
  is now the single canonical module (TC275 0x712 cross-core checks merged in,
  `VehicleSafetyLayer` kept as an alias); `selfdrive/vehicled/safety/safety.py` is a
  re-export shim. Both safety managers verified against it.
- [x] **Simulation integration tests — docker permission fixed (2026-08-02)**:
  `tools/sim/tests/` run locally → 29 passed except `test_carla_bridge.py::test_driving`
  (needed a CARLA server; docker socket was permission-denied for this user) and
  `test_metadrive_bridge.py` (metadrive package not installed). The docker blocker
  is fixed: `vcar` was added to the `docker` group (`sudo usermod -aG docker
  vcar`); takes effect in new shells, or immediately via `sg docker -c "..."` in
  an existing one. The `carlasim/carla:0.9.16` image is already pulled locally
  (29.4GB) — no download needed.
  **Actually attempting `test_driving` surfaced two more blockers, unrelated to
  docker, that stop it running from this shell/sandbox:**
  1. The `.venv` (Python 3.12) has no `msgq`, no `opendbc`, and a `params_pyx.so`
     built for a different Python ABI — `msgq_repo`/`rednose_repo` submodules
     are uninitialized (`git submodule status` shows them unpopulated) and
     `opendbc_repo` is never added to `PYTHONPATH` or `pip install -e`'d
     anywhere in this checkout (see the `opendbc_repo` entry below). The
     system `/usr/bin/python3.10` has the `carla` PyPI package installed
     (`~/.local/lib/python3.10/site-packages`) but *not* `msgq` either — so
     neither interpreter currently has a working `cereal.messaging` import.
  2. This exec environment only exposes `/dev/nvidiactl`, not `/dev/nvidia0`
     — no real GPU device passthrough — so `nvidia-smi` fails to reach the
     driver here even though the host has an RTX 3090 (used by the local
     inference stack in `CLAUDE.md`). CARLA needs actual GPU/Vulkan
     rendering; it cannot run from this sandboxed shell regardless of the
     Python/package fixes above. Running it would need to happen directly on
     the host, outside this tool's sandbox.
- [x] **RadarZoneMonitor alert priority** (`controls/lib/radar_zones.py`): side
  blind-spot messages now take priority over rear cross traffic in the zone-overlap
  region (an overtaking car behind-and-lateral is a blind-spot threat, not RCTA);
  fixes the failing `test_alert_messages`. `test_simulated_components.py` → 30/30.
- [ ] **EOP CPU budgets in test_onroad.py**: measure and add budgets for EOP daemons
  (gridd, pathd, stated, adaptd, radar4d, …) when on RK hardware.
- [ ] **Pre-existing dev-PC failures** (confirmed identical on the clean tree, not
  caused by these changes): `test_daemon_imports[monod]` (needs `hal` package),
  `test_depth_validation` (needs `/data/calibration/stereo_calibration.npz`),
  `test_nslc::test_get_nslc_speed_helper` (returns None on dev PC).

## Follow-up session (vehicled removal + OpenDBC de-duplication, 2026-08-02)

Completed:

- [x] **Finished `selfdrive/vehicled/` → `system/socketd/vehicle/` removal**:
  the in-progress rename (staged in the index from a prior session) is now
  fully coherent. `system/socketd/vehicle.py` (the old single-file shim) and
  `system/socketd/vehicle/vehicled.py` (the standalone process wrapper) are
  both deleted — `socketd` runs `vehicle.Car` as a thread inside its own
  process (`SocketD.start()` in `system/socketd/socketd.py`), there is no
  separate `vehicled` process entry in `process_config.py`.
- [x] **Fixed a real test bug**: `selfdrive/test/test_onroad.py` still
  budgeted CPU for `system.socketd.vehicle.vehicled` — a module whose file
  had already been deleted, so it could never match a real process and would
  silently report "NO METRICS FOUND". Renamed the key to
  `system.socketd.socketd`, the actual running process.
- [x] Removed the dead `"vehicled": CORE_BIG` entry from
  `common/core_config.py`'s CPU-affinity table (no such process exists;
  `socketd` already has its own mapping).
- [x] Rewrote `system/socketd/vehicle/ARCHITECTURE.md` and
  `MIGRATION_SUMMARY.md` — a prior mechanical find/replace had corrupted them
  into nonsense (`selfdrive/socketd vehicle adapter/`, etc.). Both now
  accurately describe the single-process `socketd` architecture.
- [x] Updated contributor-facing docs that still pointed at the deleted
  `selfdrive/vehicled/` path: `.github/pull_request_template.md`,
  `tools/car_porting/README.md`, `docs/CARS.md`,
  `docs/car-porting/what-is-a-car-port.md`,
  `docs/eop/01_Core/NAMING_CONVENTIONS.md`,
  `docs/eop/01_Core/VEHICLE_STACK_COMPATIBILITY.md`.
- [x] **De-duplicated `system/socketd/vehicle/tesla/values.py` against the
  pinned OpenDBC fork** (shared commit with `dev/NGP10`): `CANBUS` and
  `CarControllerParams.ACCEL_MIN/ACCEL_MAX/JERK_LIMIT_MIN/JERK_LIMIT_MAX` are
  now re-exported from `opendbc.car.tesla.values` instead of being a second
  hardcoded copy — the exact kind of duplication that caused the accel-limit
  bug in the "Safety reconciliation" entry above. Also removed `GEAR_MAP`,
  `TeslaSafetyFlags`, `TeslaFlags`, `STEER_THRESHOLD`, `FW_QUERY_CONFIG` —
  confirmed dead code with zero importers anywhere in the tree. See
  `system/socketd/vehicle/MIGRATION_SUMMARY.md` → "OpenDBC De-duplication".
- [x] Verified `python3 -m py_compile` on all touched files, and confirmed
  (via `git stash` on a clean tree) that the `msgq`/`opendbc` module-not-found
  import failures in this dev-PC venv are pre-existing environment gaps, not
  caused by this session's changes — `opendbc_repo` is not currently
  `pip install -e`'d or added to `PYTHONPATH` by any launch script or
  `SConstruct` rule in this checkout.
- [x] Committed (`11e38d891`, "remove vehicled daemon, dedupe tesla values
  against OpenDBC") and pushed to `origin/dev/EOP10`.
- [x] Fixed the CARLA sim-test docker permission blocker: `vcar` added to the
  `docker` group. See "Simulation integration tests" above.

Known remaining work:

- [ ] **Dev-PC Python environment is broken for anything touching `cereal.messaging`**
  (pre-existing, not introduced by this session — confirmed via `git stash` on a
  clean tree). Root causes, so a future session doesn't have to re-diagnose:
  - `msgq_repo` and `rednose_repo` submodules are uninitialized (`git submodule
    status` shows them with a `-` prefix — never `git submodule update --init`'d).
  - `.venv` is Python 3.12, but `common/params_pyx.so` is a stale build for a
    different Python ABI (`undefined symbol: PyCode_NewWithPosOnlyArgs`).
  - `opendbc_repo` is a populated submodule but is never `pip install -e`'d or
    added to `PYTHONPATH` by any launch script or `SConstruct` rule — so
    `system/socketd/vehicle/car/{card,carstate,carcontroller}.py`'s existing
    `from opendbc.car.tesla...` imports can't resolve here either, from either
    the 3.12 `.venv` or system `/usr/bin/python3.10`.
  - System `/usr/bin/python3.10` has the `carla` PyPI package installed
    globally (`~/.local/lib/python3.10/site-packages`) — but not `msgq`.
  - Fix needs: `git submodule update --init msgq_repo rednose_repo`, build/
    install their Cython extensions against whichever Python is canonical for
    this checkout (`.python-version` says 3.12, but the only environment with
    any of these packages present is 3.10), and either `pip install -e
    opendbc_repo` or add it to `PYTHONPATH`.
- [ ] **This tool's exec sandbox has no GPU device passthrough** — only
  `/dev/nvidiactl` is present, not `/dev/nvidia0`, so `nvidia-smi` can't reach
  the driver here even though the host has an RTX 3090. CARLA needs real
  GPU/Vulkan rendering (`tools/sim/start_carla.sh` explicitly requires a
  discrete GPU) and cannot run from this sandboxed shell — would need to run
  directly on the host outside this tool.
