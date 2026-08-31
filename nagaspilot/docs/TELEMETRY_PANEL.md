# Telemetry side panel (ExoPilot 02M extra screen width)

## Status

Implemented on `dev/EOP10`, **not yet built or run on real hardware** in
this session (no scons/capnp toolchain available in this environment --
reviewed by hand against the existing Qt UI conventions here, plus several
rounds of the `/code-review` and `/simplify` skills, including a 4-way
parallel reuse/simplification/efficiency/altitude pass). Treat this as a
thoroughly self-reviewed but still fundamentally untested first pass --
build + on-device confirmation on real 01M and 02M hardware is required
before trusting it.

## Why this is on dev/EOP10, not dev/EDP10

This feature was first built on `dev/EDP10` (a branch based on DragonPilot,
targeting original comma three hardware). Per
`nagaspilot/docs/BRANCH_CONCEPT.md` on that branch, EDP10 explicitly does
**not** adopt "EOP's RK3588 HAL, stereo/radar assumptions, or broad daemon
replacements" -- ExoPilot hardware work belongs on `dev/EOP10`, which
already had its own `eop_panel.cc`/`eop_panel.h` (this branch's real
settings-panel file, not `dp_panel.cc`) and active RK3576/RK3588
camera-platform work. The EDP10 copy of this feature was reverted; this doc
is the only record that matters.

## What already existed here (reused, not reimplemented)

Unlike EDP10 (original comma three, front camera only), EOP10 already had
real hardware and software this feature builds on:

- **`system/hardware/registry.py`**: real device-tree-based platform
  detection aliasing `'rk3588'` to `'exopilot01m'` and `'rk3576'` to
  `'exopilot02m'` -- ExoPilot 01M and 02M are genuinely different SoCs, not
  just different screens on the same board.
- **`selfdrive/ui/qt/onroad/bev_widget.{h,cc}` (`BEVWidget`)**: a top-down
  "gridd"-style widget using real `modelV2` lane lines, road edges, and
  `radarState` leads, plus a severity-graded blind-spot signal (fused from
  `controlsState.leftBlindSpot`/`rightBlindSpot` and
  `carState.leftBlindspot`/`rightBlindspot`). Already wired into
  `AnnotatedCameraWidget` as a small 130x180 corner overlay, gated by
  `EOPBEVWidgetEnabled` (default on).
- **`selfdrive/ui/qt/widgets/overlay_camera.{h,cc}` (`OverlayCameraWidget`)
  + `OnroadWindow::createOverlays()`**: real live camera PIP overlays for
  rear (reverse gear), left, and right (turn signal) -- genuine side
  cameras via `uvcd`. This already covers the "lane-change adjacent-lane
  preview" idea that originally motivated this feature (on EDP10, where no
  side camera exists at all) -- nothing new was built for that here.

So this feature is narrower than it first looks: only the wide-screen side
panel itself, real `Hardware::RK3576()` detection, and the width-sizing
plumbing needed adding. The "gridd" default view and the adjacent-lane
preview already existed.

## The panel itself

**`selfdrive/ui/qt/onroad/telemetry_panel.{h,cc}` (`TelemetryPanel`)**: a
swipeable (mouse-drag left/right) two-page widget.

- **Page 0 (default): the existing `BEVWidget`**, reused at full panel size
  rather than reimplemented. `BEVWidget` takes no opinion on its own size or
  visibility (see "BEVWidget: two callers, one shared class" below) -- as a
  `QStackedWidget` page here it's just resized to fill the page area like
  any other page.
- **Page 1: `TelemetryStatsPage`**, a simple readout (speed, steering angle,
  lead distance/relative speed) using `InterFont` and the same
  `MS_TO_KPH`/`MS_TO_MPH` conversion `hud.cc` already uses. Caches the
  previous frame's values and only repaints when something displayed
  actually changed, rather than redrawing four `drawText` calls with font
  switches every UI tick regardless.

`TelemetryPanel::updateState()` only updates whichever page is currently
showing (`pages->currentWidget()`) -- the other page isn't drawn, so there's
no reason to walk `modelV2`/radar data to populate it every tick. A page
picks up fresh data on the tick after a swipe lands on it (at UI_FREQ,
imperceptible).

## Hardware detection: real chip identity, not a guessed screen size

**`system/hardware/hw.h`**: added `Hardware::RK3576()`, sharing a private
`matchesPlatform()`/`deviceTreeCompatible()` helper with the existing
`Hardware::RK3588()` (an `EOP_PLATFORM` env var override checked first, then
a substring match against `/proc/device-tree/compatible`, read from disk
once and cached in a `static const std::string` -- the physical SoC can't
change at runtime, so re-reading it on every call was pure waste).
`ROCKCHIP()` and `get_device_type()` now recognize both chips. The C++ side
was RK3588-only before this change; `registry.py` (Python) already had
RK3576.

**`getTelemetryPanelWidth()`** (`qt_window.h`/`.cc`): returns `0` unless
`Hardware::RK3576()` is true, in which case it returns the installer-set
`EOPTelemetryPanelWidth` param. The *gate* is genuine hardware identity, not
a manual toggle -- one fewer thing an installer can get wrong, since RK3576
vs RK3588 isn't a judgment call.

**`deviceScreenSize()`** (`qt_window.h`): a single `return`, not three
branches -- only RK3576 ever differs from the `{EOP_01M_WIDTH,
EOP_01M_HEIGHT}` baseline that RK3588 and PC/default both want:
```cpp
inline QSize deviceScreenSize() {
  const int extra = Hardware::RK3576() ? getTelemetryPanelWidth() : 0;
  return {EOP_01M_WIDTH + extra, EOP_01M_HEIGHT};
}
```
`EOP_01M_WIDTH`/`EOP_01M_HEIGHT` are shared constants (also used by
`window.cc`, see below) so this and that can't drift apart.

**`EOPTelemetryPanelWidth`** (`common/params_keys.h`, `INT`, default `576`
= `1600 - 1024`): assumes a 1600x600 02M panel. `1024` for 01M is confirmed
by existing shipped code (`deviceScreenSize()` itself, unrelated to this
feature); the `1600` half is **not** independently verified against a
physical 02M panel or spec sheet in this tree -- it's the strongest number
available after several earlier guesses (1080x600, then a disputed
1600x600) turned out wrong, not a confirmed one. Overridable per-unit via
`eop_panel.cc`'s `add_safety_toggles()` (next to the existing BEV toggle)
through a `ParamSpinBoxControl`, so correcting it after an on-device check
doesn't require SSH/`param_set` or a rebuild -- just the param and a UI
restart.

Three places need to agree on the number `576`, and all three now share
one constant (`EOP_TELEMETRY_PANEL_DEFAULT_WIDTH`, `qt_window.h`) instead
of each spelling it out separately:

- `params_keys.h`'s declared default for a param `manager_init()` has never
  seeded yet (the normal case on any real device).
- `ParamSpinBoxControl`'s `default_value` constructor argument in
  `eop_panel.cc`, used only if the Settings screen is somehow opened
  against a params store that never went through `manager_init` (e.g.
  testing the UI binary standalone) and the param is genuinely empty.
- `getTelemetryPanelWidth()`'s own fallback (`qt_window.cc`) for that same
  "param empty or unparseable" case. **A review pass caught this one
  independently defaulting to `0`** while the Settings spin box defaulted
  to `576` for the identical condition -- meaning a device that hit this
  path would silently get no panel at all (`deviceScreenSize()` returns
  the bare `1024x600` baseline, `MainWindow` never constructs `telemetry`)
  while Settings displayed "576" for the width, actively misleading anyone
  trying to debug why the panel wasn't showing. All three now read
  `EOP_TELEMETRY_PANEL_DEFAULT_WIDTH` instead of a private copy of `576`.

`getTelemetryPanelWidth()` also clamps the parsed value to
`[0, EOP_TELEMETRY_PANEL_MAX_WIDTH]` (`800`, `qt_window.h` -- again the
same number the spin box's own range uses, via a shared constant). The spin
box already keeps anyone using the Settings UI inside that range, but
nothing stops the param from being set some other way (`adb param_set`, a
params.db migration, a manual edit) -- and this value feeds straight into
`setMainWindow()`'s `QWidget::setFixedSize()` with no other validation in
between, so an unbounded value there could size the real onroad window
arbitrarily large on real hardware.

## Where the panel lives: MainWindow, not HomeWindow

`TelemetryPanel` is constructed and owned by `MainWindow` (`window.h`/
`window.cc`), **not** `HomeWindow` -- an earlier version of this feature put
it inside `HomeWindow` (alongside `Sidebar`/`OnroadWindow`) and had to patch
around the fallout with a `wrapAtBaselineWidth()` helper pinning
`settingsWindow`/`onboardingWindow` to a fixed width so they wouldn't
stretch across the wider window. That patch worked but was a bandaid one
layer too low: the real problem was that `HomeWindow` growing to include
the panel widened `MainWindow`'s entire rect, and `MainWindow`'s
`QStackedLayout` then force-stretched *every* sibling of `HomeWindow` --
including screens that never needed to know 02M exists.

The fix moves telemetry one layer up instead of patching every affected
sibling individually:

```cpp
// MainWindow::MainWindow()
QHBoxLayout *main_layout = new QHBoxLayout(this);   // was QStackedLayout directly
QWidget *stack_wrapper = new QWidget(this);
main_layout->addWidget(stack_wrapper, 1);            // stretch: takes whatever's left
stack_layout = new QStackedLayout(stack_wrapper);     // homeWindow/settingsWindow/onboardingWindow live here, unmodified

if (telemetryWidth > 0) {
  telemetry = new TelemetryPanel(this);
  telemetry->setFixedWidth(telemetryWidth);
  QSizePolicy sp = telemetry->sizePolicy();
  sp.setRetainSizeWhenHidden(true);           // see the paragraph below -- this line is load-bearing
  telemetry->setSizePolicy(sp);
  telemetry->setVisible(false);               // onroad-only
  main_layout->addWidget(telemetry);          // sibling of stack_wrapper, not nested inside it
}
```

Because `deviceScreenSize()` defines `MainWindow`'s total width as exactly
`EOP_01M_WIDTH + telemetry's width`, and `telemetry` takes a *fixed* width
out of `main_layout`, `stack_wrapper` -- the only other widget in that
`QHBoxLayout`, with stretch and no constraints -- gets handed exactly
`EOP_01M_WIDTH` by Qt's box-layout math. `stack_layout` then keeps doing
exactly what it always did (force its current widget to fill its own
rect), except that rect is now always the correct baseline size.
`homeWindow`, `settingsWindow`, and `onboardingWindow` are added to
`stack_layout` completely unmodified -- `home.h`/`home.cc` are back to
their exact pre-feature state (`git diff <pre-feature-commit> --
selfdrive/ui/qt/home.h selfdrive/ui/qt/home.cc` is empty), and
`settingsWindow`/`onboardingWindow` never needed a per-widget wrapper or a
fixed-width pin at all.

**This depends on one easy-to-miss Qt behavior, caught by a code-review
pass, not by reasoning about the layout math alone**: `QBoxLayout` treats a
*hidden* child widget as zero-size by default
(`QWidgetItem::isEmpty() == widget->isHidden()`) unless that widget's size
policy has `retainSizeWhenHidden` set. `telemetry` starts hidden (offroad)
and is only shown onroad -- without `setRetainSizeWhenHidden(true)`,
`main_layout` would silently hand `stack_wrapper` `telemetry`'s reserved
width back *while telemetry is hidden*, i.e. for the entire time the car
is parked, reintroducing the exact "offroad screens stretch on 02M" bug
this feature had already fixed once -- just intermittent (only while
onroad) instead of constant. With that flag set, the layout keeps
reserving `telemetry`'s width even while it isn't drawing anything there,
so `stack_wrapper` is the correct `EOP_01M_WIDTH` at all times, onroad or
off.

That in turn means there's a rectangle of `MainWindow` -- exactly
`telemetry`'s reserved area -- that nothing paints while `telemetry` is
hidden: `MainWindow` has `Qt::WA_NoSystemBackground` set (so Qt doesn't
auto-erase it), and a hidden widget doesn't paint itself.
`MainWindow::paintEvent()` fills the whole window black for exactly this
reason -- children (`stack_wrapper`'s contents, and `telemetry` itself when
visible) paint over it normally, so this only actually shows through in
that one reserved-but-hidden rectangle.

`MainWindow` gained a `updateState(const UIState &s)` slot (connected to
`uiState()->uiUpdate`, same pattern `HomeWindow`/`Sidebar` already use) to
drive `telemetry->updateState()`, and its existing
`offroadTransition`-connected lambda now also toggles
`telemetry->setVisible(!offroad)` -- the same onroad-only visibility rule
`HomeWindow` used to own, just relocated with the panel.

On ExoPilot 01M and PC, `getTelemetryPanelWidth()` is `0`, `telemetry` is
never constructed, and `main_layout` ends up with exactly one child
(`stack_wrapper`, stretch) -- functionally identical to the original
single-`QStackedLayout` `MainWindow`, just with one extra (invisible,
zero-cost) `QWidget` wrapper layer.

## BEVWidget: two callers, one shared class

`BEVWidget` is reused as-is for both its original small corner overlay
(`AnnotatedCameraWidget`) and this panel's full-size default page, rather
than forked into two classes. Making that reuse clean (rather than each
caller fighting the other's assumptions) took two changes to
`bev_widget.{h,cc}` itself:

- **No default size.** The constructor no longer calls
  `setFixedSize(130, 180)` or sets `Qt::WA_TransparentForMouseEvents` --
  both are presentation choices specific to the corner-overlay use (a fixed
  small size, click-through so buttons/camera underneath stay usable), not
  something the shared class should assume. `AnnotatedCameraWidget` sets
  the fixed size at its call site; `Qt::WA_TransparentForMouseEvents` is
  set by `TelemetryPanel` instead of `BEVWidget`'s constructor -- see "Swipe
  gesture needed WA_TransparentForMouseEvents on the pages, not just the
  dots strip" below for why *both* callers actually want it, just for
  different reasons.
- **No self-managed visibility.** `updateState()` no longer calls
  `setVisible(enabled && data_valid)` on itself. That call only made sense
  for a single caller with a single opinion about what "not showing" means;
  with two callers wanting different behavior (hide entirely vs. show an
  empty grid), the decision doesn't belong inside the widget.
  `bool isShowing() const { return enabled && data_valid; }` reports the
  same information; each caller decides what to do with it --
  `AnnotatedCameraWidget::updateState()` calls
  `bev_widget->setVisible(bev_widget->isShowing())` to hide the corner
  overlay entirely, while `TelemetryPanel` doesn't call it at all, since its
  own page-switching is already the sole visibility authority for its page.

  This also exposed a latent bug the old `setVisible(false)` had been
  masking: when `data_valid` goes false, `paintEvent()` never cleared the
  lane-line/road-edge/lead point arrays -- it just kept drawing whatever
  they last held. Invisible, that didn't matter. Now that a caller can keep
  the widget visible while invalid, `paintEvent()` gates
  `drawRoadEdges()`/`drawLaneLines()`/`drawLeads()`/`drawBlindSpots()`
  behind `if (data_valid)` (the grid and ego vehicle icon still always
  draw), and `updateState()` calls `update()` on every path -- including
  both early returns -- so a transition to invalid actually repaints
  instead of leaving stale content on screen.

- **Blind-spot severity was being collapsed to a bool.** Pre-existing,
  found by the same review pass while looking at this file for the two
  changes above: `leftBlindSpot`/`rightBlindSpot` (`cereal/log.capnp`) are
  `Int8` severity -- `0`=off, `1`=caution, `2`=warning -- and
  `drawBlindSpots()` has a real severity-2 branch (a flashing red warning
  triangle, distinct from the milder caution-color arc). But
  `updateState()` was storing `ctrl.getLeftBlindSpot() > 0` (a bool) into
  the `int left_blind_spot` field, so the value could only ever be `0` or
  `1` -- the warning-triangle branch was dead code, in both this widget's
  original small corner-overlay use and now its new full-size panel page.
  Fixed to store the real severity, with `carState.leftBlindspot`/
  `rightBlindspot` (a plain bool, no severity of its own) fused in via
  `std::max()` so it can only ever raise the displayed severity to at
  least "caution", never suppress a real "warning" from `controlsState`.

  That fix's first version assigned straight into
  `left_blind_spot`/`right_blind_spot` inside each source's own
  `if (sm.valid(...))` block, which a later review pass caught as a new
  staleness bug: since `std::max` can only raise a value, a severity-2
  reading from `controlsState` would survive indefinitely if
  `controlsState` alone went stale afterward (e.g. a `controlsd` hiccup)
  while `carState`/`modelV2`/`radarState` stayed valid -- a phantom
  warning-triangle stuck on screen with no way to clear itself, and
  entirely unreachable before this fix since the severity-2 branch used to
  be dead code. Fixed by reading each source into its own local
  (`ctrl_left`/`car_left` etc.), defaulting to `0` when that source isn't
  valid *right now*, and only then taking the max -- so a stale
  `controlsState` correctly drops back to whatever `carState` alone
  currently supports, rather than freezing at its last reading.

## Swipe gesture needed WA_TransparentForMouseEvents on the pages, not just the dots strip

An earlier version of this doc claimed `TelemetryPanel` deliberately left
`bevPage`/`statsPage` mouse-opaque because the swipe gesture "needs real
mouse events" -- that has it backwards, and a review pass caught the
resulting bug: Qt delivers `MouseButtonPress`/`MouseButtonRelease` to
whichever widget is topmost under the cursor and does **not** bubble an
unaccepted one up to the parent on its own (unlike wheel events, which
Qt does forward to the parent when ignored). `bevPage`/`statsPage` fill the
entire panel above the 28px dots strip and neither overrides
`mousePressEvent`/`mouseReleaseEvent`, so without
`Qt::WA_TransparentForMouseEvents` on them, every press/release over the
main content area was consumed by whichever page is current and never
reached `TelemetryPanel::mousePressEvent`/`mouseReleaseEvent` at all --
`dragging` never became `true`, `goToPage()` was never called, and the
swipe gesture only ever worked if a drag started and ended inside the thin
dots strip at the very bottom (which already had the attribute set, and
happened to still work for that reason alone).

Fixed by setting `Qt::WA_TransparentForMouseEvents` on `bevPage` and
`statsPage` in `TelemetryPanel`'s constructor -- the exact same technique
already used one file over for the same reason (`onroad_home.cc`:
`alerts->setAttribute(Qt::WA_TransparentForMouseEvents, true)` so
`OnroadWindow::mousePressEvent` still fires with `OnroadAlerts` stacked on
top of it). Neither page has interactive content of its own that needs to
actually receive mouse events, so this is safe for both.

**That first attempt was still one container short.** `pages` (the
`QStackedWidget` holding `bevPage`/`statsPage`, itself a direct child of
`TelemetryPanel`'s layout) has no mouse handling of its own either, so once
its two children stopped absorbing the event, `pages` simply absorbed it
instead -- a later review pass caught that the same reasoning above applies
one level up. `pages` needed `Qt::WA_TransparentForMouseEvents` too, not
just its contents, before the swipe actually reached `TelemetryPanel`.

## Efficiency: only repaint what actually changed

Two changes keep this feature from redrawing at UI_FREQ (~20Hz onroad)
regardless of whether anything on screen actually changed:

- **`TelemetryStatsPage`** caches the previous frame's `is_metric`/speed/
  steering angle/lead values and only calls `update()` (and therefore
  repaints -- several `drawText` calls with font switches) when at least
  one of them differs from last frame. `is_metric` has to be part of that
  comparison, not just the numbers: a later review pass caught that
  toggling units while stopped (`vEgo == 0` either way) with no lead would
  otherwise leave the wrong `km/h`/`mph` label on screen indefinitely,
  since nothing else displayed would have changed. Matches the existing
  `OnroadAlerts::updateState()`
  pattern (`alerts.cc`), which only updates on `!alert.equal(a)`.
- **`TelemetryPanel::updateState()`** only calls `updateState()` on
  whichever of its two pages is currently showing (`pages->currentWidget()`)
  -- the other page isn't drawn by the `QStackedWidget`, so there's no
  reason to walk `modelV2`/radar data to populate it every tick. Whichever
  page a swipe lands on picks up fresh data on the very next tick, an
  imperceptible delay at UI_FREQ.
- **`TelemetryPanel::goToPage()`** only calls `update()` (for its page-dot
  indicator) when the target index actually differs from the current one.

`TelemetryStatsPage::paintEvent()` also switched from raw
`QFont("Inter", ...)` construction to the existing `InterFont` helper
(`selfdrive/ui/qt/util.h`), matching every sibling onroad/sidebar widget --
`InterFont` sizes by pixel (`setPixelSize`), which is what the rest of this
UI relies on for consistent on-screen text size; raw `QFont` sizes by
point, which does not track the same way.

## `Hardware::RK3588()`/`RK3576()`: shared, cached detection

Both were near-duplicate implementations of the same two-step check (an
`EOP_PLATFORM` env var, then a `/proc/device-tree/compatible` substring
search) differing only in which platform string to look for -- and each
independently re-read that file from disk on every call. Both now call a
shared private `matchesPlatform(name)` helper, which in turn reads
`/proc/device-tree/compatible` into a `static const std::string` exactly
once per process (the physical SoC cannot change at runtime, so repeated
reads could only ever produce the same string) via a private
`deviceTreeCompatible()` accessor.

## Two callers' current wiring

- **`AnnotatedCameraWidget`** (`annotated_camera.{h,cc}`): only constructs
  its corner `bev_widget` when `getTelemetryPanelWidth() == 0` (01M, PC, or
  an unconfigured 02M unit) -- avoids showing the same top-down view twice
  on a configured 02M unit, where the panel's own page already covers it.
  `bev_widget` stays `nullptr` otherwise, guarded at its one other use site.
- **`TelemetryPanel`**: constructs its own `BEVWidget` instance
  unconditionally (the panel itself only exists on 02M in the first place).

## Known limitation

Whether `deviceScreenSize()`'s resulting wider fixed window actually maps
correctly onto ExoPilot 02M's real physical panel has not been verified --
there is no scons/capnp build available in this environment. This is the
one thing in this feature that a code-review pass cannot substitute for;
it needs real 02M hardware.
