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
alongside this request has no real adjacent-lane object data to preview from
yet (see the gridd study doc's activation gates) -- a full camera-based
preview is still out of scope. As an interim, presence-only step,
`OnroadWindow::drawAdjacentVehicleIcon()` (`onroad_home.h`/`.cc`) now draws a
fixed car glyph near the mirror position, on both device 01 and 02, gated on
the same real `leftBlindspot`/`rightBlindspot` booleans already driving the
existing full-screen BSM flash -- no distance, closing speed, or fabricated
camera feed, since none of that data exists. Revisit for anything richer
once a shadow detector has recorded-route accuracy evidence.

## Hardware detection

There is no board-ID or panel-name probing in this codebase (verified: no
`exopilot`/`EOP10` string appears anywhere except planning docs, and
`get_device_type()` only distinguishes stock comma tici/tizi/mici via
`/sys/firmware/devicetree/base/model`).

**Confirmed panel geometry (given 2026-08-30, not yet cross-checked against
a running device)**: ExoPilot 01 (7") is 1080x600; ExoPilot 02 (9") is
1600x600 -- both 600px tall, only width differs. This is a *different*
hardware family from comma three's 2160x1080 (`DEVICE_SCREEN_SIZE`), not a
scaled variant of it.

`getTelemetryPanelWidth()` (`qt_window.h`/`.cc`) reads
`QGuiApplication::primaryScreen()->size()` and only acts when it matches the
confirmed 1600x600 (02) signature within a small tolerance, returning
`1600 - 1080 = 520`. Any other detected size -- comma three, PC, an
unrecognized ExoPilot variant -- safely returns `0`, i.e. exactly today's
behavior. This is deliberately a signature match, not a "wider than X"
threshold, because of the open question below.

**Open question, not resolved by this change**: this tree has no `SCALE=`
set in any launch script (checked: `launch_env.sh` and all other `*.sh`),
so production has always requested a fixed 2160x1080 window on real hardware
regardless of what the physical panel actually is. That means one of two
things must already be true on ExoPilot, and this hasn't been confirmed
either way:

1. A display bridge/scaler chip presents a virtual 2160x1080 mode to the
   Linux/EGLFS side and internally scales to the real 1080x600 / 1600x600
   panel -- in which case `QGuiApplication::primaryScreen()->size()` would
   report 2160x1080 on *both* ExoPilot variants, and this detection
   mechanism cannot distinguish them at all; or
2. The GPU/DRM side genuinely reports the true panel resolution (1080x600 /
   1600x600), and something else (compositor, or the existing UI simply not
   fitting the smaller panel today) accounts for the mismatch with
   `DEVICE_SCREEN_SIZE`.

This code assumes (2). **Before trusting that the panel actually appears on
ExoPilot 02 hardware, confirm what `QGuiApplication::primaryScreen()->size()`
(or `xrandr` / `/sys/class/graphics/fb0/virtual_size`) actually reports on a
real device.** If it turns out to be (1), screen-size detection cannot work
at all and a different signal is needed (a device-tree property, a
provisioning-time param, or a build-time flag) -- this file's approach does
not attempt that.

`setMainWindow()` sizes the app's fixed window to `DEVICE_SCREEN_SIZE +
(extra, 0)` when the extra width is nonzero -- i.e. it grows the *existing*
2160x1080 logical canvas rather than resizing it to ExoPilot's real pixel
count. Whether that larger logical canvas maps correctly onto a 1600x600
physical panel depends entirely on whatever external scaling explains point
(1) or (2) above, and has not been verified.

`HomeWindow` (`home.cc`) only constructs `TelemetryWidget` when
`getTelemetryPanelWidth()` is nonzero, and gives it exactly that width.
`Sidebar` and `OnroadWindow` are untouched -- neither their code nor their
geometry changes -- so device 01 (and anything not matching the confirmed
02 signature) renders identically to before this change, and PC dev builds
are unaffected (`getTelemetryPanelWidth()` returns 0 on `Hardware::PC()`).

**Known limitation**: because `MainWindow` is sized as a whole, the
offroad/settings/onboarding screens (which share the same `QStackedLayout`)
also become wider whenever the panel is active, gaining unused right-hand
space. Only the onroad screen was in scope for this change.
