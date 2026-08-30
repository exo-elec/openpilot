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

## Hardware detection: explicit installer opt-in, not autodetection

There is no board-ID or panel-name probing in this codebase (verified: no
`exopilot`/`EOP10` string appears anywhere except planning docs, and
`get_device_type()` only distinguishes stock comma tici/tizi/mici via
`/sys/firmware/devicetree/base/model`).

**Real ExoPilot 01/02 panel pixel dimensions were asked for three times
during this feature's development and came back different -- and reportedly
wrong -- each time**, with no spec sheet, part number, or on-device readout
ever provided to check against (1080x600/1600x600 was tried and rejected as
wrong; no replacement numbers followed). Autodetecting from screen size was
abandoned for this reason, and for a second, independent reason found along
the way: this tree has no `SCALE=` set in any launch script (checked:
`launch_env.sh` and all other `*.sh`), so production has always requested a
fixed 2160x1080 window on real hardware regardless of the physical panel.
That means either a display bridge/scaler chip already remaps a virtual
2160x1080 canvas onto the real panel (in which case `QGuiApplication`
would report 2160x1080 on *every* ExoPilot variant, making screen-size
detection blind to the difference no matter what numbers are used), or the
GPU/DRM side genuinely reports true panel pixels and something else
explains the `DEVICE_SCREEN_SIZE` mismatch. Neither has been confirmed
against real hardware.

Given that, `getTelemetryPanelWidth()` (`qt_window.h`/`.cc`) no longer
guesses a signature to match against. It reads two persistent params
instead, set once per physical unit by whoever installs it -- the one party
who can actually see and measure the hardware:

- **`dp_ui_exopilot_wide_screen`** (bool, default off) -- installer sets
  this on a 9" ExoPilot 02 unit only. Off (including on device 01, comma
  three, and PC) means `getTelemetryPanelWidth()` returns `0` and the app
  behaves exactly as it did before this feature existed.
- **`dp_ui_telemetry_panel_width`** (int, px, default `0`) -- how much
  extra width that specific unit's panel has beyond a 7" ExoPilot 01 panel.
  There is no assumed-safe default here either: leaving it at `0` while the
  toggle above is on yields an enabled-but-invisible (harmless) panel
  rather than a guessed size.

Both are exposed in the offroad Settings UI (`add_ui_toggles()` in
`dp_panel.cc`, under "UI") via a `ParamControl` toggle and a
`ParamSpinBoxControl`, so setting them doesn't require SSH/`param_set`
access -- just knowing (from actually looking at or measuring the unit)
whether it's the 9" model and how many extra pixels wide it is. Both are
read once at UI process startup (`getTelemetryPanelWidth()` caches the
result in a function-local `static`), so a change from Settings needs a UI
restart to take effect -- normal for anything that resizes the app window.

`setMainWindow()` sizes the app's fixed window to `DEVICE_SCREEN_SIZE +
(extra, 0)` when the extra width is nonzero -- i.e. it grows the *existing*
2160x1080 logical canvas by exactly the installer-specified amount, rather
than resizing to ExoPilot's real pixel count (which, per the open question
above, isn't even known to be what `DEVICE_SCREEN_SIZE` reflects). Whether
that larger logical canvas maps correctly onto the real panel depends on
whatever external scaling explains the pre-existing `DEVICE_SCREEN_SIZE`
mismatch, and has not been verified -- **this still needs to be checked on
real ExoPilot 02 hardware**, but the install-time toggle means that check no
longer blocks on getting an exact number right first: if the resulting
window doesn't fit, the installer adjusts `dp_ui_telemetry_panel_width` and
restarts, no code or numbers-in-chat round-trip required.

`HomeWindow` (`home.cc`) only constructs `TelemetryWidget` when
`getTelemetryPanelWidth()` is nonzero, and gives it exactly that width.
`Sidebar` and `OnroadWindow` are untouched -- neither their code nor their
geometry changes -- so any unit with the toggle off (device 01, comma
three, PC, or simply an unconfigured device) renders identically to before
this feature existed.

**Known limitation**: because `MainWindow` is sized as a whole, the
offroad/settings/onboarding screens (which share the same `QStackedLayout`)
also become wider whenever the panel is active, gaining unused right-hand
space. Only the onroad screen was in scope for this change.
