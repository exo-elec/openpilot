# Shared NGP10 / EOP10 / 01M / 02M pipeline

NGP10 is the portable policy parent. EOP10 inherits its control, map and
runtime helpers unchanged, and adapts hardware and parameter names. See
`docs/integration/SHARED_NGP_EOP_PIPELINE.md` for the parent parity gate.

The shared steering activity producer is `driveractivityd` in NGP10. All
descendants consume the same `driverMonitoringState` for warning events,
controlsd forced deceleration and UI availability status. No separate
steeringd, driverd or camera dmonitoring daemon is registered. The NGP-origin
`ngp_dm_policy` selector offers deployed relaxed timing and a tighter decay
policy without resetting accumulated awareness; see
`nagaspilot/docs/STEERING_ACTIVITY_MONITORING.md`.

NGP retains comma card/OpenDBC. Starting at EOP, OpenDBC is removed as a
dependency; vehicled/socketd owns a minimal MIT-derived Tesla-party protocol,
and cereal owns its wire-compatible car schema. The protocol is checked
against recorded reference state and byte-level controller outputs. Both
controlsd paths use the NGP vehicle-model equations; EOP only adapts its existing
fallback parameters. Board HAL selection never changes those equations.

EOP10, 01M and 02M use the same runtime source tree. `common/build_profile.py`
selects the UI backend, default SoC and logical display size:

| Build | UI backend | Default SoC | Display |
|---|---|---|---|
| EOP10 | cpp | rk3588 | 1024 × 600 |
| 01M | pyqt5 | rk3588 | 1024 × 600 |
| 02M | pyqt5 | rk3576 | 1600 × 600 |

Native Qt source is retained in the common tree; SCons builds it only for the
cpp profile. PyQt5 source is shared; ordinary narrow layouts create no floating
side panels. Wide layouts add them lazily. Backend cloud voice and CPU wake
code are shared, disabled by their existing defaults; EOP10's native UI does
not gain the animated PyQt5 recording popup.

Both SoC adapters are independent subclasses of RockchipHardware. Kernel
paths, pin maps and verified camera assignments come from each board's HAL.
RK3588 keeps its existing camera discovery. RK3576 requires confirmed unique
MIPI roles. Neither profile invents missing camera, GPS, IMU or thermal data.
Measured NPU allocation applies only to the matching SoC; the same allocator
packs task groups for either core count.

The shared manager starts canonical surround radar4d on all Exopilot boards with ignition on; optional WiFi reception remains RK3576-specific. Its IPC and
costmap consumer are present everywhere and inactive without data. Existing
controllers, feature switches, recording, BLE, navigation, localization,
planning, audio and inference implementations are inherited from this common
baseline rather than recopied per board.

Validation of source sharing does not establish hardware readiness or feature
behavior on a moving vehicle. Refer to FEATURE_PARITY_AND_SWITCHES.md and
VISIONPILOT_PORT_AUDIT.md for known runtime, provisioning and hardware gaps.

## Validation in this workspace

- Shared PyQt5 UI, NPU packing/benchmark and media: 222 passed, 11 hardware skips.
- Both board adapters, camera selection and power rails: 30 passed, 7 hardware skips.
- Platform flags, geometry and allocation: 23 passed.
- Cloud codec/client/capture and CPU wake: 16 passed.
- Renderer selection, RK3576-only corner WiFi and UI build actions: 5 passed.
- Radar point conversion, publication and costmap stamping: 11 passed, 1 skip,
  using real Cap'n Proto messages and a mocked IPC transport.
- Existing voiced tests: 11 passed with real messages and mocked IPC.
- Python syntax audit found and removed the malformed 01M pathd wrapper.

The shared local gate passed ruff, shebangs and all 212 offscreen UI tests.
The preserved legacy native/raylib process-integration test needs compiled
msgq and is excluded from that host-only UI gate. No full native C++/SCons
build, live Google call or vehicle/hardware integration run was performed.
Tests used the existing Python 3.13 test venv; production project metadata
still declares Python >=3.11,<3.13. Real deployment uses its pinned toolchain.

## NGP parent refresh and prefixes

The parent chain is now NGP10 → EOP10 → 01M → 02M. The NGP parent includes
the newer portable map/planning ports. Canonical policy prefixes describe
ownership: EOP-origin map and path-nudge cores retain eop_* names even in
NGP; NGP-origin policy keeps ngp_* names in descendants. Existing imports
and saved settings have explicit compatibility aliases. The parity gate
compares all inherited controls, runtime and map Python files.

The EOP-only planner preserves its existing controller adapters and sensor
inputs; importing a portable core does not enable a second controller.
EOPDDSCEnabled, EOPPathdNudgesEnabled and EOPSharedSLCOffsets are canonical
keys for NGP's portable adapter and are deliberately excluded from the EOP
UI. EOP retains its existing distraction path, EOPNudgeEnabled and speed
limit offset controls.

The upstream footprint budget records the smaller turn hook (173 added lines,
previously 177; one more upstream line replaced) and the inherited NGP replay
README. Other upstream budgets remain unchanged. The direct parent parity
check covers portable source independently of those documentation edits.

The EOP-only distance-scale fix uses EOPPathdFixScaleEnabled (PDSF in the
PyQt5 settings). Old ngp_pathd_fix_scale values remain a read fallback when
the canonical key is absent. It stays off by default; this does not change
existing speed-reduction behavior automatically.

The NGP refresh passed 72 focused parent policy/naming tests, 132 integrated
EOP policy/adapter tests, four daemon scale-switch refresh tests and the
212-test offscreen UI gate. Both upstream footprint gates and the renderer
selection tests pass. The NGP schema was also loaded from real source in a
host fixture: personality values and six pipeline messages were checked.
All 448 original parameter definitions are preserved; EOP log/custom schema
blobs and native C++ UI source remain unchanged. Host tests do not replace
compiled IPC, a native UI build, driving replay or hardware validation.
