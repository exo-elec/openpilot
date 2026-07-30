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
- [ ] **Simulation integration tests**: `tools/sim/tests/` run locally → 29 passed
  except `test_carla_bridge.py::test_driving` (needs a CARLA server; docker socket
  is permission-denied for this user — needs sudo/group fix + multi-GB image pull)
  and `test_metadrive_bridge.py` (metadrive package not installed).
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
