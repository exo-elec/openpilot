# Telemetry side panel (ExoPilot 02M extra screen width)

## Status

Implemented on `dev/EOP10`, **not yet built or run on real hardware** in
this session (no scons/capnp toolchain available in this environment --
reviewed by hand against the existing Qt UI conventions here). Treat this
as a first pass that needs a real build + on-device check before being
trusted.

## Why this is on dev/EOP10, not dev/EDP10

This feature was first built on `dev/EDP10` (a branch based on DragonPilot,
targeting original comma three hardware). Per
`nagaspilot/docs/BRANCH_CONCEPT.md` on that branch, EDP10 explicitly does
**not** adopt "EOP's RK3588 HAL, stereo/radar assumptions, or broad daemon
replacements" -- ExoPilot hardware work belongs on `dev/EOP10`, which
already had its own `eop_panel.cc`/`eop_panel.h` (this branch's real
settings-panel file, not `dp_panel.cc`) and active RK3576/RK3588
camera-platform work (see the "Add RK3576/ExoPilot 02M as a second
supported platform" commit history). This doc supersedes the EDP10 version
of the same feature; that branch's copy should be treated as an
intentionally-abandoned false start once this one is validated, not a
parallel implementation to keep in sync.

## What already existed here before this change

Unlike EDP10 (original comma three, front camera only), EOP10 already has
real hardware and software this feature could build on instead of
reinventing:

- **`system/hardware/registry.py`**: real device-tree-based platform
  detection aliasing `'rk3588'` to `'exopilot01m'` and `'rk3576'` to
  `'exopilot02m'` -- i.e. ExoPilot 01M and 02M are genuinely different SoCs,
  not just different screens on the same board.
- **`selfdrive/ui/qt/onroad/bev_widget.{h,cc}` (`BEVWidget`)**: an existing
  top-down "gridd"-style widget using real `modelV2` lane lines, road edges,
  and `radarState` leads, plus a richer blind-spot signal (severity 0/1/2,
  fused from `controlsState.leftBlindSpot`/`rightBlindSpot` and
  `carState.leftBlindspot`/`rightBlindspot`) than EDP10's boolean-only BSM.
  Already wired into `AnnotatedCameraWidget` as a small 130x180 corner
  overlay, gated by `EOPBEVWidgetEnabled` (default on).
- **`selfdrive/ui/qt/widgets/overlay_camera.{h,cc}` (`OverlayCameraWidget`)
  + `OnroadWindow::createOverlays()`**: real live camera PIP overlays for
  rear (reverse gear), left, and right (turn signal) -- genuine side cameras
  via `uvcd`, not a presence-only icon. This already covers the
  "lane-change adjacent-lane preview" idea that prompted this feature on
  EDP10 (where no side camera exists at all, so a boolean BSM icon was the
  only honest option) -- **nothing new was built for that here because a
  better version already existed.**

So this change is narrower than its EDP10 counterpart: only the wide-screen
side panel itself was missing; the "gridd" default view and the adjacent-
lane preview already existed and are reused, not reimplemented.

## What this change adds

- **`system/hardware/hw.h`**: added `Hardware::RK3576()`, mirroring the
  existing `Hardware::RK3588()` device-tree check (`/proc/device-tree/compatible`
  containing `"rk3576"`, with an `EOP_PLATFORM=rk3576` env var override for
  testing, matching `RK3588()`'s own override convention). The C++ side
  was RK3588-only before this; `registry.py` (Python) already had RK3576.
  `ROCKCHIP()` and `get_device_type()` now recognize both.
- **`getTelemetryPanelWidth()`** (`qt_window.h`/`.cc`): returns `0` unless
  `Hardware::RK3576()` is true (real chip detection, not guessed screen
  size), in which case it returns the installer-set `EOPTelemetryPanelWidth`
  param. This mirrors EDP10's installer-toggle design, but the *gate* is now
  genuine hardware identity instead of an extra manual on/off toggle -- one
  fewer thing an installer can get wrong, since RK3576 vs RK3588 isn't a
  judgment call.
- **`deviceScreenSize()`** (`qt_window.h`): unchanged for RK3588/PC/default
  (still exactly `{1024, 600}`); RK3576 now returns
  `{1024 + getTelemetryPanelWidth(), 600}`.
- **`EOPTelemetryPanelWidth`** (`common/params_keys.h`, `INT`, default
  `576` = `1600 - 1024`): assumes a 1600x600 02M panel. This default was
  set after the fact (see "Default width" below) -- 01M's 1024x600 is
  confirmed by existing shipped code (`deviceScreenSize()` itself, unrelated
  to this feature); the `1600` half is not independently verified against
  a physical 02M panel or spec sheet in this tree. Overridable per-unit via
  `eop_panel.cc`'s `add_safety_toggles()` (next to the existing BEV toggle,
  since the panel's default page reuses `BEVWidget`) via
  `ParamSpinBoxControl`, so correcting it doesn't require SSH/`param_set`.
- **`selfdrive/ui/qt/onroad/telemetry_panel.{h,cc}` (`TelemetryPanel`)**: a
  swipeable right-side panel, default page reuses the *existing* `BEVWidget`
  class (a second instance, resized to fill the panel instead of the small
  130x180 corner box -- `bev_widget.{h,cc}` are untouched), second page is a
  simple stats readout (speed/steering/lead). `HomeWindow` (`home.cc`) only
  constructs it when `getTelemetryPanelWidth() > 0`, as a sibling of the
  untouched `Sidebar`/`OnroadWindow` -- so ExoPilot 01M, PC, and any
  unconfigured 02M unit are byte-for-byte unaffected.

## Default width (576 = 1600 - 1024)

`EOPTelemetryPanelWidth` originally defaulted to `0` (panel present but
invisible until an installer measured their unit and set a real number),
because every screen-size figure given for this feature earlier in its
development turned out wrong on a later check. `1024` for 01M isn't in that
category -- it's the literal value already in `deviceScreenSize()`, shipped
and unrelated to this feature. Once that number was the one in play (rather
than an EDP10-era 1080 baseline), `1600` was reasserted for 02M, so the
default was changed to `576` on that basis. This is still not independently
cross-checked against a spec sheet or a running 02M unit -- it's a stronger
starting point than before, not a verified one. Adjust
`EOPTelemetryPanelWidth` (no rebuild needed, just a param + UI restart) if
a real unit's panel doesn't match.

## Known limitations (carried over from the EDP10 design)

- Whether `deviceScreenSize()`'s resulting wider fixed window actually maps
  correctly onto ExoPilot 02M's real physical panel has not been verified --
  there is no scons/capnp build available in this environment. Confirm on
  real 02M hardware.
- ~~`MainWindow`'s settings/onboarding screens share the same top-level
  `QStackedLayout` as `HomeWindow` and will also render at the wider window
  size when the panel is active, gaining unused right-hand space.~~ Fixed:
  see "Settings/onboarding no longer stretch on 02M" below.
- `BEVWidget::updateState()` calls `setVisible(enabled && data_valid)` on
  itself, for its other (small corner-overlay) use in
  `AnnotatedCameraWidget`. Left alone, that would fight this panel's
  `QStackedWidget` page-switching -- disabling `EOPBEVWidgetEnabled`, or
  onroad before `modelV2`/`radarState` arrive, would hide the widget even
  while its page is the current one, leaving a blank black page instead of
  falling back visibly. `TelemetryPanel::updateState()` reasserts
  `bevPage->setVisible(true)` after calling `bevPage->updateState(s)` so
  page-switching stays the sole visibility authority; when BEV is disabled
  or data isn't ready yet, the page still renders (grid/vehicle icon, no
  dynamic content) rather than going blank.

## BEV corner overlay merged into the panel on 02M

Initially `AnnotatedCameraWidget` always constructed its own small
(130x180) `BEVWidget` corner overlay regardless of platform, so on ExoPilot
02M a driver would see the same top-down view twice -- once small in the
corner of the main camera view, once full-size as the telemetry panel's
default page. `AnnotatedCameraWidget::AnnotatedCameraWidget()`
(`annotated_camera.cc`) now only constructs the corner `bev_widget` when
`getTelemetryPanelWidth() == 0` (ExoPilot 01M, PC, or an unconfigured
02M unit) -- `bev_widget` stays `nullptr` on a configured 02M unit, with a
null check added at its one other use site (`updateState()`). 01M is
completely unaffected: it never had a telemetry panel to duplicate against,
so this is a no-op there.

## Settings/onboarding no longer stretch on 02M

`MainWindow`'s `main_layout` is a `QStackedLayout` holding `homeWindow`,
`settingsWindow`, and `onboardingWindow` as siblings -- `QStackedLayout`
forces its current widget to fill the *entire* `MainWindow` rect regardless
of size policy or `maximumSize`, so on 02M (a wider `MainWindow`) the
settings/onboarding screens used to stretch to the full extra width, since
neither is nested inside `HomeWindow`'s own sidebar/telemetry carve-out.

Fixed with `wrapAtBaselineWidth()` (`window.cc`, anonymous namespace):
`settingsWindow`/`onboardingWindow` are each `setFixedWidth(EOP_01M_WIDTH)`
(not just a maximum -- a plain `QWidget` defaults to a `Preferred` size
policy, so a `QHBoxLayout` would otherwise size it to its own `sizeHint()`
and hand the leftover space to the trailing stretch, shrinking it on *every*
platform, not just leaving unused space on 02M) and placed inside a small
`QHBoxLayout` wrapper (`settingsWrapper`/`onboardingWrapper`, new
`MainWindow` members) with a trailing stretch to absorb any extra width.
`main_layout->addWidget(...)` now adds the wrappers instead of the raw
windows, and every `setCurrentWidget`/`currentWidget() ==` call in
`window.cc` was updated to target them -- `settingsWindow`/
`onboardingWindow` themselves are still used directly for their own method
calls (`setCurrentPanel()`, `showTrainingGuide()`, `completed()`) and signal
connections, which don't care about reparenting.

`EOP_01M_WIDTH`/`EOP_01M_HEIGHT` (`qt_window.h`) were pulled out of
`deviceScreenSize()`'s three duplicated `{1024, 600}` literals into shared
constants, so this fix and `deviceScreenSize()` can't drift apart.

On ExoPilot 01M and PC (`MainWindow` already exactly `1024` wide), this is
a genuine no-op: the fixed-width content plus a zero-width stretch exactly
reproduces what `QStackedLayout` used to force directly. Verified by code
review (two passes: the first caught the `sizeHint()`-shrinking bug above
before it shipped), not by an actual build/run -- still needs on-device
confirmation like the rest of this feature.
