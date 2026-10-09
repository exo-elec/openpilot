# EOP10 `cereal/car.capnp`: fork -> opendbc symlink

Status: **done 2026-10-04**: committed and pushed to EOP10 (`82db02c0f`), 01M rebased, 02M merged. Written 2026-10-04 from a field-by-field comparison of
`cereal/car.capnp` (EOP10 `d62c978ef`) with `opendbc_repo/opendbc/car/car.capnp` (pin `6c0fbcd`).

## Why

`cereal/car.capnp` on EOP10 is a regular file; upstream and NGP10 have a symlink to opendbc's. Both
declare the schema id `@0x8e2af1e708af8b8d`, so a process that loads `cereal.car` and `opendbc.car`
fails with `Duplicate ID`. Today that stops a full `scons` build (MPC codegen imports
`system/socketd/vehicle/car/card.py`), so `test_lateral_mpc`, `test_longcontrol`, both planner tests and
`test_brownpanda_contract` cannot run, and any EOP10 process importing both is broken the same way.
opendbc's own BYD code already notes the fork "has no lkaDisabled field", i.e. the opendbc side is the
newer one.

## What differs (counts from the comparison)

- 90 fork-only members, 70 opendbc-only members, 5 members with the **same name but a different ordinal**.
- Fork is the older layout: `CarEvent` (opendbc: `OnroadEventDEPRECATED`; events live in `log.capnp`),
  `EventName` entries `lowSpeedLockout @31`, `soundsUnavailable @56`, `controlsInitializing @98`,
  camera errors `@100`-`@102`, `startupNoFw @104`, `highCpuUsage @105`, `lkasDisabled @107`,
  `controlsdLagging @112`, `belowLaneChangeSpeed @122`; `CarState.events @13`, `gas`, `brake`,
  `engineRpm`, `clutchPressed`; `LateralINDITuning`/`LQRTuning` (opendbc: `...DEPRECATED`); older
  `Error`/`SafetyConfig` numbering.
- Ordinal mismatches (wire-incompatible, the real risk): `EventName.startupNoSecOcKey` 121 vs 125,
  `RadarData.errors` 0 vs 3, `Error.canError` 1 vs 0, `Error.wrongConfig` 3 vs 2,
  `SafetyConfig.safetyParam` 3 vs 1.
- EOP-only members the fork adds: `CarState.speedLimit @61`, `lkaDisabled @62`, `stockAdas @63`
  (`StockADAS`), `CruiseState.setDistance @7` (`SetDistance`), `ButtonEvent.Type altButton1/3`.

## How much code uses the fork-only parts (grep, `selfdrive system nagaspilot common tools`)

- `CarState.lkaDisabled`: 2 readers (`selfdrive/controls/controlsd.py:285`,
  `system/socketd/vehicle/car/events.py:53`); no writer found outside the fork.
- `CarState.speedLimit`: no reader of the CarState field (the 21 `speedLimit` hits are `mapData`,
  `navInstruction`, Params and tests). `stockAdas`: no reader outside the schema.
- `setDistance` / `SetDistance`: 5 files. `CarEvent`: 5 files. `lowSpeedLockout`: 2.
  `startupNoSecOcKey`: `selfdrive/selfdrived/events.py`, `selfdrived.py`, opendbc.
  `canMonoTimes` / `clutchPressed` / `engineRpm`: opendbc is the owner (9 `engineRpm` files).

## Steps (each its own commit; build and run tests after every step)

1. On a scratch clone, replace `cereal/car.capnp` with the symlink
   `../opendbc_repo/opendbc/car/car.capnp` (as NGP10). Build `cereal common` and load `cereal.car` and
   `opendbc.car` in one process (the check that fails today).
2. Fix compile-time users: `CarEvent` -> the `log.OnroadEvent` path (5 files), `lowSpeedLockout`,
   `SetDistance` (decide: drop or move to `custom.capnp`).
3. `lkaDisabled`: either add it to the opendbc fork's `car.capnp` (shared with BYD and NGP10) or compute
   it in `controlsd`/socketd from existing signals. Do not keep a private field in a symlinked file.
4. `speedLimit @61`, `stockAdas @63`: unused; drop, or move to `custom.capnp` if a reader is planned.
5. Ordinals: a log written with the fork cannot be read after the swap for the 5 mismatched members.
   Accept that (dev-PC stage, no deployed logs) and say so in the commit.
6. Full `scons`; then run `selfdrive/controls/tests`, `selfdrive/selfdrived/tests`, `nagaspilot/tests`
   with the repo `conftest.py`.
7. Rebase 01M, merge 02M (same conflict scripts as before).

## Not decided / needs the owner

- Step 3 (where `lkaDisabled` lives) and step 2's `SetDistance` change opendbc, which is shared; the
  exo-elec/opendbc fork is the right place, so it needs a commit and a submodule bump on every branch.
- The one action this plan needs that was **denied in this session**: deleting the tracked
  `cereal/car.capnp` to replace it with the symlink (step 1), even in the throwaway `~/work/openpilot`
  clone. Run it yourself or allow it, then continue from step 1.

## Result of the scratch implementation (2026-10-04)

- **Done in the working tree:** `cereal/car.capnp` -> symlink to opendbc's; `cereal/__init__.py` reuses
  `opendbc.car.structs.car` and loads `log`/`custom` with `imports=[opendbc/car, cereal]`;
  `log/custom/legacy.capnp` import `c++.capnp` and (log) `car.capnp` by absolute path so both resolve to
  opendbc's single file (otherwise a second `Duplicate ID`, this time for `c++.capnp`); `cereal/SConscript`
  passes `-I opendbc_repo/opendbc/car` to `capnpc`; `SConstruct` adds `#cereal/gen/cpp` (log's header
  includes `<car.capnp.h>`).
- **Users updated:** `CP.deprecated.{vEgoStarting,startingState,startAccel,stoppingDecelRate,vEgoStopping}`
  (`longcontrol.py`, `longitudinal_planner.py`, `socketd/vehicle/car/card.py`);
  `CP.safetyConfigs[0].safetyModel` (card.py); `get_friction`/`FRICTION_THRESHOLD` from
  `opendbc.car.lateral` (`latcontrol_torque.py`); `Car.CarEvent` -> `Car.OnroadEventDEPRECATED` in three
  deprecated `log.capnp` fields (same struct id); `test_longcontrol.py` and the two planner tests.
- **Verified:** full `scons` passes the MPC codegen and builds `cereal`/`common`; per-file pytest passes for
  `test_lateral_mpc`, `test_longcontrol`, both planner tests, `test_lateral_sign_conventions`,
  `test_state_machine`, `test_alertmanager`, `test_health_monitor`, `test_low_visibility`, and
  `nagaspilot/tests` except the 2 NGP10-only `test_brownpanda_contract` tests. 181 of 187 modules import
  (the rest: `panda` not built, 3 unrelated). Still failing before and after: the Qt UI (`eop_panel`,
  `settings.cc`), the missing driving-model `.onnx`, `test_desire_helper_lane_confidence` (NGP10-style stubs).
- **lkaDisabled (decided, done):** nothing ever wrote `CarState.lkaDisabled` (always false), so the two readers
  (`controlsd.py`, `socketd/vehicle/car/events.py`) were removed; behaviour is unchanged. Old EOP10 logs cannot be read for the 5 members with different ordinals.
- **Next after the commit:** rebase 01M onto the new EOP10, merge it into 02M, mirror the same changes where
  01M/02M carry their own `cereal` files.

## Follow-up fixes found by building real messages (2026-10-04)

The module-import sweep cannot see shape changes. Building the actual producers' messages found five more
breakages, all fixed in the same lineage:
- `card.py` wrote `CP.safetyModel` (slot @9); it is now `CP.deprecated.safetyModel` (the same bytes).
  An earlier version of this migration populated `safetyConfigs` instead, which would have changed
  `selfdrived`'s panda safety-mismatch check; `safetyConfigs` stays empty, pinned by a test.
- `CP.carName` no longer exists (opendbc has `brand`); `card.py` raised on every start. Removed (no readers).
- `system/radar3d` set `rr.errors = []/['fault']`; `RadarData.errors` is now the `Error` struct
  (`rr.errors.radarFault`), so the radar producer would have raised on its first publish.
- `Actuators.steer` is `Actuators.torque` (same ordinal, @2): `steamd/publisher.py` and `card.py` updated.
- `longitudinal_planner.py` read `carState.speedLimit` every cycle; the field was fork-only and never written
  (always 0.0), so it is now a constant 0.0 (no dash-limit source).
New tests: `test_card_car_params_schema.py`, `test_radar_errors_schema.py`.
Found, not fixed (unrelated, latent): `long_mpc.get_jerk_factor` evaluates `_PERSONALITY_DEFAULTS[personality]`
eagerly, which raises `KeyError` for `aggressive` (the message default); production publishes other values.

## Second round: launching the daemons (2026-10-04)

Smoke-running `radard`, `selfdrived`, `controlsd`, `card` and then every `PythonProcess` in `process_config.py` found more
breakages unrelated to field names: `RadarPoint` extras in `deprecated`, `selfdrived` subscribing to a service that does not
exist here, `CP.accelMin/accelMax` (in no schema), `CANParser.update_strings`, the personality `_DynamicEnum` hashing,
pycapnp 2.x type strictness (IntEnum, numpy scalars, `list.append`), typed Params (`InstallDate`, `UpdateFailedCount`,
`LastUpdateTime`, `ReleaseNotes` -> `UpdaterNewReleaseNotes`), `SubMaster(poll=[...])`, and `SelfdriveState.isOnroad` /
`ControlsState.enabled`. All fixed and pinned by tests; see `CODE_BOUNDARY_TASKS.md` for the list and the two open items.

