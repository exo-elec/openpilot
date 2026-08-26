# RK3576 / ExoPilot 02M support

**Status, 2026-08-26**: platform registration and NPU-topology plumbing
landed (Phase A below). Camera capture on 02M does **not** work yet — no
MIPI driver code exists for its 5-camera array (Phase B). Nothing here has
been run on real RK3576 hardware; all verification is host-side (unit
tests, env-var platform override), matching this repo's existing "dev PC
testing phase" status for RK3588 too (see `docs/eop/PHASE5_HARDWARE_READINESS.md`).

## Why this exists

Until 2026-08-26, this fork and the sibling `~/pilot/exopilot` repo both
stated RK3576 (ExoPilot 02M) was VisionPilot's (a separate ROS2 stack)
exclusive target and explicitly out of scope for openpilot. That boundary
has been overridden by product decision: EOP10 now targets both ExoPilot
01M (RK3588) and 02M (RK3576), and VisionPilot's ROS2 stack is additional
on 02M, not exclusive.

Scope note: the Hailo-8/DeepX (DX-M1) PCIe accelerator support this repo
already has is **camera-inference tier only** (side/rear BSD-style
detection) — see `system/inferenced/compute.py`'s `WorkloadClass` docstring.
The core driving model (`driving_vision`/`driving_policy`) stays RKNN-only
on both platforms; RKNN and Hailo/DeepX are different, incompatible NPU
toolchains, and nobody has attempted running the driving model on either
PCIe accelerator anywhere in this codebase, `~/pilot/exopilot`, or
`~/pilot/visionpilot`.

## What's implemented (Phase A)

- `system/hardware/registry.py`: `rk3576` registered (alias `exopilot02m`),
  `PlatformRegistry.detect()` checks the device-tree compatible string for
  `rk3576`.
- `system/hardware/rk3576/hardware.py`: `RK3576Hardware`, a thin subclass of
  `RK3588Hardware` — most Rockchip/Linux-generic methods (reboot, network,
  power) are inherited unchanged. Overridden: board identity
  (`get_device_type`/`get_platform`/`detect`), pin/camera-geometry data
  sources (`hal.platform.rk3576_pins`/`rk3576_camera_geometry`), the 5-camera
  MIPI array + 160mm stereo baseline (see refactor note below), mic
  capability (`has_voice_input` → True, unlike 01M), and cellular modem
  power control (02M wires EC25 via direct GPIO bit-bang, not 01M's
  Mini-PCIe USB-mode mux — genuinely different circuit, not just different
  pin numbers).
- **Refactor**: `RK3588Hardware.get_camera_array_config()`/
  `get_stereo_baseline_mm()` used to hardcode `RK3588Hardware._cam_geo`
  instead of `self._cam_geo`, which would have forced `RK3576Hardware` to
  duplicate both method bodies just to plug in different data. Changed to
  `self._cam_geo`/`self.MIPI_CAMERA_NAMES`/`self.PLATFORM_NAME`/
  `self.SOC_NAME`/`self.HAS_TELE_ROAD`/`self._usb_cameras`, all now class
  attributes a subclass can override without touching the method bodies.
  `test_rk3576.py::test_shares_camera_array_logic_with_rk3588` is a
  regression test for this staying true.
- `system/hardware/camera_types.py` (new): `CameraSensor`/`HDRMode`/
  `CameraConfig`/`find_camera()`, factored out of
  `system/hardware/rk3588/camera_config.py` so `rk3576/camera_config.py`
  doesn't duplicate the same dataclass/enum definitions.
- `system/hardware/rk3576/camera_config.py`: mirrors
  `rk3588/camera_config.py`'s `USB_CAMERAS` optional-`hal`-import pattern,
  sourcing from `hal.platform.rk3576_camera_geometry`.
- `selfdrive/modeld/runners/rknn_platform.py`: `PlatformType.RK3576` (2
  cores × 3 TOPS), `detect_platform()`/`get_core_count()` extended.
  `NPU_ALLOCATION_MAP[PlatformType.RK3576]` is an **empty dict** — no
  per-task core assignment exists yet for RK3576 in `hal.tuning.npu` (that
  module only has RK3588 data), so `get_core_mask()` falls back to core 1
  for every task on RK3576 until real tuning data is ported. Core *count*
  is correct regardless.
- `~/pilot/exopilot` (separate repo): `hal/hal/platform/rk3576_camera_geometry.py`
  was missing a `USB_CAMERAS` export that `rk3588_camera_geometry.py` has
  (added, same 3 UVC cameras/specs). `hal/hal/platform/rk3576_pins.py` was
  missing EC25 modem GPIO data that exists in `boards.py`'s
  `BOARD_DATA["exopilot02m"]["cellular"]` but wasn't surfaced in the shape
  `hardware.py` consumers expect (added, marked `"confirmed": False` since
  the power-on sequence timing/polarity hasn't been validated against a
  schematic).
- `CLAUDE.md` / `common/core_config.py`: "RK3576 not supported" language
  corrected.

Not touched, deliberately: `system/inferenced/hailo_hef.py` and
`system/inferenced/deepx_dxnn.py` (the camera-tier NPU backends) — both
detect their hardware generically via `lspci`/device-node probing, not
RK3588-specific mechanisms, and `BOARD_DATA["exopilot02m"]["features"]["pcie_2lane"]`
is `True`, so there's no known reason either backend needs platform-specific
changes to work on 02M. Not verified on real hardware either, same caveat
as everything else here.

## What's NOT implemented (Phase B — needs real RK3576 hardware)

- **Camera capture**: `system/v4l2d/_default_camera_configs()` hardcodes
  exactly 4 MIPI cameras (01M's road/wide_road/stereo_left/stereo_right).
  02M's 5-camera array (mono_narrow/mono_wide/mono_tele/stereo_left/
  stereo_right) needs a per-platform camera list plus actual sensor
  driver/capture logic. `~/pilot/visionpilot/src/system/camera/camera/drivers/
  {ox03c10_driver.py,gc4653_driver.py}` has real, working register-level
  driver code for the exact same two sensors (OX03C10 ×3, GC4653 stereo
  pair) behind a clean `BaseCameraDriver` interface — a direct porting
  reference, not something to re-derive from datasheets. VisionPilot uses
  ROS2 topics where EOP10 uses V4L2+VisionIPC, so this is adaptation, not a
  drop-in copy.
- **Stereo depth math**: anything computing depth from a hardcoded 80mm
  baseline constant needs to read `get_stereo_baseline_mm()` per-platform
  instead (160mm on 02M).
- **USB/UVC side+rear cameras**: `system/uvcd`/`selfdrive/sided` already
  handle N USB cameras generically — should work once platform detection
  recognizes `rk3576` and `RK3576Hardware.has_side_cameras()`/
  `has_rear_camera()` return real results on actual hardware, but this is
  unverified.
- **Modem power-on sequence**: `RK3576Hardware.modem_power_on()`/
  `modem_power_off()` use real GPIO numbers from `boards.py`, but the
  bit-bang sequence itself (order, pulse widths, polarity) is a first guess
  based on the 01M sequence's shape, not a confirmed 02M procedure — needs
  validation against a schematic or real hardware before relying on it.
- **NPU per-task core allocation**: needs real tuning data for RK3576's
  2-core topology in `hal.tuning.npu` (currently RK3588-only).
- **Thermal/fan management and PMIC power-rail monitoring**:
  `system/hardware/hardwared.py` (RK806S PMIC rail names/nominal voltages),
  `system/thermald/thermald.py`/`fan_control.py`/`thermal_zones.py`
  (devfreq governor paths like `/sys/class/devfreq/ffa30000.npu/governor`,
  `hal.platform.rk3588_thermal` import) are all RK3588-specific — device-tree
  addresses, PMIC wiring, and `hal.platform.rk3576_thermal` doesn't exist
  yet. All fail closed on RK3576 (checked: `_set_governor()` no-ops if the
  sysfs path is absent, same pattern throughout) rather than crash, so
  running on RK3576 today just means NPU/GPU/DMC performance-governor
  forcing and PMIC under-voltage detection are silent no-ops, not broken.
  Needs real RK3576 devfreq/PMIC data before these do anything there.

## Corrected while auditing for "clean dual support" (2026-08-26)

Found by grepping for actual conditional branching on `rk3588` (not just
comments/docstrings) across the whole tree — three real gaps beyond Phase A's
original scope:

- **`system/hardware/__init__.py`'s `ROCKCHIP`/`TICI` flags were RK3588-only**:
  `ROCKCHIP = RK3588` (not `RK3588 or RK3576`), so on real RK3576 hardware
  these would have silently evaluated `False`. Both flags gate real behavior
  in `selfdrive/ui/onroad/cameraview.py` (EGL zero-copy rendering, shader
  setup), `selfdrive/recordd/recordd.py` (encoding path), `system/updated.py`,
  and `conftest.py`'s test-skipping — all of which would have silently
  treated RK3576 as a PC/dev build. Fixed via
  `ROCKCHIP = isinstance(HARDWARE, RK3588Hardware)`, which covers
  `RK3576Hardware` through its subclass relationship and any future
  Rockchip platform automatically, rather than an enumerated list that
  would need updating again for a third platform. Also added the missing
  `RK3576`/`RK3576_DETECTED`/`RK3576Hardware` exports (only `RK3588`'s
  existed).
- **`selfdrive/controls/lib/eop_utils.py::detect_exopilot_platform()`**:
  docstring said RK3588 was "the only platform openpilot supports" even
  though the function's own code already correctly returned `'exopilot02m'`
  for an RK3576 device tree — the code was ahead of the docs even before
  this session's changes. Corrected the docstring, and added the same
  `HARDWARE` env-var override `PlatformRegistry.detect()` already supports,
  since this function previously had no way to exercise the RK3576 branch
  without a real device tree.
- **`common/realtime.py`'s `BIG_CORES`/`LITTLE_CORES` comment** said "RK3588
  CPU cores" — the actual index arrays (`[0,1,2,3]`/`[4,5,6,7]`) are correct
  for RK3576 too (same 4-big+4-little topology, A72 instead of A76), just
  the comment was misleadingly platform-specific. Comment corrected, no
  logic change.

## Verification performed

Host-side only, no hardware:
- `system/hardware/rk3576/tests/test_rk3576.py`,
  `selfdrive/modeld/runners/tests/test_rknn_platform.py` — both pass with
  and without the `hal` package on `PYTHONPATH` (skipping hal-dependent
  assertions gracefully when absent, matching this repo's existing
  convention for `rk3588`'s test suite).
- Fixed a pre-existing bug in `system/hardware/rk3588/tests/test_rk3588.py`
  found while running it for comparison: `test_hardware_creation()`
  asserted `has_side_cameras() is True`/`has_rear_camera() is True`
  whenever `hal` was importable, but those methods probe real device
  files/USB enumeration — the assertion only held on real RK3588 hardware,
  and fails on any dev PC that happens to have `hal` on the path. Changed
  to assert the methods are callable and return a `bool`, not a specific
  value, matching this file's own "host-side, no hardware required" scope.
