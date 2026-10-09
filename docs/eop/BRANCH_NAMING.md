# Branch naming and the 01M / 02M platform split

**Decision, 2026-09-10.** Branches are named for the platform they target,
replacing the `EOP10` product-generation name:

| Repo | Branch | Platform | Stack |
|---|---|---|---|
| `openpilot` (this repo) | `dev/01M` (was `dev/EOP10`) | ExoPilot 01M — RK3588 | openpilot, C++/Qt UI |
| `openpilot` (this repo) | `dev/02M` | ExoPilot 02M — RK3576, 1600×600 | openpilot backend, new Qt Widgets UI; ESP32 corner-radar WiFi add-on (only 02M has the antenna for it) |

**Update, 2026-09-23.** The earlier plan to move 02M to a separate stack is
dropped: 02M stays on openpilot `dev/02M`. The sections below describe
`dev/01M` dropping its 02M code.

## Status

**The UI half is done** (2026-09-10). Removed from this branch:

- `selfdrive/ui/qt/onroad/telemetry_panel.{cc,h}` and its entry in
  `selfdrive/ui/SConscript`
- `getTelemetryPanelWidth()` and the `EOP_TELEMETRY_PANEL_*` constants
  (`selfdrive/ui/qt/qt_window.{h,cc}`) — `deviceScreenSize()` is now a constant
  `{1024, 600}`
- the `EOPTelemetryPanelWidth` param (`common/params_keys.h`) and its
  `ParamSpinBoxControl` (`selfdrive/ui/qt/offroad/eop_panel.cc`)
- `MainWindow`'s `stack_wrapper` / `QHBoxLayout` split and its `updateState`
  slot (`selfdrive/ui/qt/window.{cc,h}`) — `stack_layout` sits directly on
  `MainWindow` again
- `nagaspilot/docs/TELEMETRY_PANEL.md`

`AnnotatedCameraWidget`'s BEV corner overlay is now unconditional. **`BEVWidget`
itself was kept** — the panel reused it, but 01M has always shown its 130x180
corner instance, and `bev_widget.{cc,h}`'s caller-agnostic refactor (`isShowing()`,
no self-hiding) was an improvement worth keeping on its own terms.

`Hardware::RK3576()` was **also kept**: it is platform detection, not UI, and
`ROCKCHIP()`, `get_name()` and `get_device_type()` all depend on it.

The platform half below is still pending, and waits on the sequencing above.

## What gets removed, when it does

Roughly 40 files carry `rk3576` / `02M` references. The split is:

**Lives only on `dev/02M`** — `system/hardware/rk3576/`, `PlatformType.RK3576` and
its NPU allocation map in `selfdrive/modeld/runners/rknn_platform.py`, the
5-camera v4l2d config, `detect_exopilot_platform()`'s RK3576 branch, and the
02M entries in `common/core_config.py`, `common/realtime.py`,
`common/transformations/camera.py`, `selfdrive/locationd/calibration_storage.py`,
`selfdrive/controls/lib/surface_quality_db.py`,
`selfdrive/steamd/stereo_correction.py`, `selfdrive/surfaced/surface_detector.py`,
`system/inferenced/rockchip_npu.py`.

**Deleted here** — done, see Status above.

Two changes from the 2026-08-26 RK3576 work should also **stay**, since both
were improvements independent of that platform:

- `RK3588Hardware`'s `self._cam_geo` / class-attribute refactor — reverting it
  re-hardcodes exactly what
  `system/hardware/rk3576/tests/test_rk3576.py::test_shares_camera_array_logic_with_rk3588`
  exists to prevent.
- `ROCKCHIP = isinstance(HARDWARE, RK3588Hardware)` in
  `system/hardware/__init__.py` — still correct with one platform, and it won't
  need re-touching if a third ever appears.

## Rename ordering

`dev/01M` now exists alongside `dev/EOP10`. To complete the rename:

1. Land the 02M removal above.
2. Point the repo's default branch at `dev/01M`.
3. Update any CI branch filters, systemd units and submodule pins that name
   `dev/EOP10`.
4. Only then delete `dev/EOP10`.

## Full plan

The porting plan and backend linkage analysis are in
`docs/eop10/EOP10_PORT_PLAN.md` on `dev/02M`.
