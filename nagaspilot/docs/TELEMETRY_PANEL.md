# Telemetry side panel (ExoPilot 02 extra screen width)

## Status

Implemented, **not yet built or run on real hardware** in this session (no
scons/capnp toolchain available in this environment -- reviewed by hand
against the existing Qt UI conventions only). Treat this as a first pass that
needs a real build + on-device check before being trusted, consistent with
this repo's evidence bar for UI changes touching the onroad screen.

## What it does

`selfdrive/ui/qt/onroad/telemetry.{h,cc}` adds `TelemetryWidget`, a right-side
panel that only exists on screens wider than the baseline comma-three panel
(`DEVICE_SCREEN_SIZE` = 2160x1080 in `qt_window.h`). It is a swipeable
`QStackedWidget` of pages:

- **Page 0 (default): "TOP VIEW"** -- a top-down schematic with the ego car
  anchored near the bottom, the front-radar lead (if any) plotted above it by
  distance, and the existing `leftBlindspot`/`rightBlindspot` booleans shown
  as two persistent edge chips (same color/semantics as the full-screen BSM
  flash already in `OnroadWindow::paintEvent`).
- **Page 1: "STATS"** -- speed, steering angle, lead distance and relative
  speed, using fields already read elsewhere in the Qt UI (`hud.cc`).

Swipe left/right (mouse-drag-based; real touch delivers the same
`QMouseEvent`s on this platform) pages between them; page dots at the bottom
show position.

## What it deliberately does NOT do

Per `COMMA3_MONOD_GRIDD_STUDY.md`, no corner-radar, side-camera, or
`AdjacentVehicleTracker` message exists in this tree today -- only a design
proposal does. This panel does not fabricate or mock that data; the top-view
page only ever draws the front-radar lead and the real BSM booleans. Adding a
live corner/side view is future work gated on that detector actually
existing and publishing validated data, per this project's evidence rules.

The lane-change (LCA/ALC) "sneak preview of the adjacent lane" idea raised
alongside this request is explicitly deferred, not part of this change --
there is no real adjacent-lane object data to preview yet (see the gridd
study doc's activation gates). Revisit once a shadow detector has recorded-
route accuracy evidence.

## Hardware detection

There is no board-ID or panel-name probing in this codebase (verified: no
`exopilot`/`EOP10` string appears anywhere except planning docs, and
`get_device_type()` only distinguishes stock comma tici/tizi/mici via
`/sys/firmware/devicetree/base/model`). Rather than invent a sysfs path or a
provisioning-time param for hardware neither spec nor device is available to
verify against, detection uses the real panel size Qt's own display backend
(EGLFS/DRM) already probes at boot, via `QGuiApplication::primaryScreen()`:

- `getTelemetryPanelWidth()` (`qt_window.h`/`.cc`) returns
  `detected_width - 2160` when the real screen is wider than baseline, else
  `0`.
- `setMainWindow()` now sizes the app window to `DEVICE_SCREEN_SIZE +
  (extra, 0)` instead of unconditionally forcing 2160x1080 on real hardware.
- `HomeWindow` (`home.cc`) only constructs `TelemetryWidget` when that extra
  width is nonzero, and gives it exactly that width. `Sidebar` and
  `OnroadWindow` are untouched -- neither their code nor their geometry
  changes -- so a 2160x1080 panel (device 01) renders identically to before,
  and PC dev builds are unaffected (`getTelemetryPanelWidth()` returns 0 on
  `Hardware::PC()`).
- **Known limitation**: because `MainWindow` is sized as a whole, the
  offroad/settings/onboarding screens (which share the same `QStackedLayout`)
  also become wider on device 02, gaining unused right-hand space. Only the
  onroad screen was in scope for this change; revisit if that looks wrong in
  practice.

If ExoPilot 02 turns out to need a *different* baseline width for the main
driving view rather than the same 2160px region plus new panel, that's a
different design (shrink/rescale `nvg` itself) and this file's approach does
not do that -- it strictly preserves the existing 2160-wide region and adds
new panel width beside it.
