# UI (Qt) Audit — `selfdrive/ui/qt`

**Scope:** `selfdrive/ui/qt/` — all files that differ from upstream `v0.10.0`
**Reviewer:** Claude Code
**Date:** 2026-09-01
**Status:** 🟠 HIGH findings fixed; all findings resolved or explicitly deferred below

---

## Overview

76 files total changed vs `v0.10.0`. Split into three categories:

| Category | Count | Verdict |
|---|---|---|
| **Group A** — net-new EOP files (pure additions, no upstream equivalent) | 24 files | ✅ Keep, no audit needed |
| **Group B** — upstream files modified | 52 files | See per-file sections below |
| **Group C** — upstream files deleted outright (feature removed, not modified) | 4 files | ✅ Keep, verified no dangling references |

Methodology: for each Group B/C file, diffed against `git show v0.10.0:<file>`, read the changed regions in full, and checked for runtime-behavior bugs (not style/rebrand). Findings are severity-tagged (🔴 CRITICAL / 🟠 HIGH / 🟡 MEDIUM / 🟢 LOW) per the convention in `CONTROLS_AUDIT.md`. Four parallel review passes (core/network, offroad panels, onroad, widgets) plus one self-review pass (this session's own telemetry-panel work: `qt_window.*`, `window.*`, `annotated_camera.*`) covered all 52 Group B files.

---

## Group A: Net-new files (no upstream equivalent)

All 24 files are pure additions — no upstream behavior modified. Classification: **KEEP**, out of scope for this audit (they have no upstream baseline to diff against; any bugs in them are ordinary code-review territory, not upstream-porting risk).

| File | Feature |
|---|---|
| `network/bluetooth_manager.cc/.h` | BLE/classic Bluetooth pairing manager backing the new Settings Bluetooth panel |
| `offroad/eop_panel.cc/.h` | ExoPilot-specific settings panel (safety toggles, tuning spin boxes) |
| `offroad/model_selector.cc/.h` | Driving-model selection UI |
| `offroad/openblt_update_widget.cc/.h` | OpenBLT firmware update flow |
| `offroad/safety_panel.cc/.h` | Safety-related settings panel |
| `onroad/bev_widget.cc/.h` | Bird's-eye-view top-down widget (corner overlay and/or telemetry-panel page) |
| `onroad/telemetry_panel.cc/.h` | ExoPilot 02M wide-screen swipeable telemetry side panel |
| `widgets/assistant_card.cc/.h` | Voice-assistant status card |
| `widgets/bluetooth.cc/.h` | Bluetooth pairing UI widgets |
| `widgets/drive_stats.cc/.h` | Local (no-cloud) drive statistics widget, replaces comma Prime ad |
| `widgets/obd2_settings.cc/.h` | OBD2/SPP scanner settings |
| `widgets/overlay_camera.cc/.h` | Reusable PIP camera overlay widget (rear/side cameras) |

---

## Group C: Files deleted outright (feature removed, not modified)

Git classifies these as "modified" only because the path existed at `v0.10.0`; at HEAD they are fully absent. Each was verified to be a clean, fully-threaded-through removal or relocation — zero dangling references anywhere in `selfdrive/ui/` (translation `.ts` files aside, which are inert generated strings).

| File | Verdict | Why |
|---|---|---|
| `offroad/driverview.cc/.h` | ✅ Keep (clean deletion) | `DriverViewWindow` (driver-camera preview + face-box overlay) removed together with its only caller (`settings.cc`'s "Driver Camera" button, `home.cc`'s instantiation) and its only signal (`showDriverView`, removed from `settings.h`/`window.cc` in the same change). |
| `onroad/driver_monitoring.cc/.h` | ✅ Keep (clean deletion, relocated) | `DriverMonitorRenderer` (face-keypoint overlay driven by upstream's `driverStateV2`) removed; `driverd` is documented "not implemented" on this fork ( `docs/eop/00_Index/OVERVIEW.md` ), so it had no data source. The on-road DMS status indicator was **not** dropped — it was reimplemented in `onroad/hud.cc`'s new `HudRenderer::drawDriverStatus()`, sourced from this fork's own `driverPoseState`/`driverStatus` messages. Removal from `annotated_camera.cc`'s `paintGL()` (the `dmon.updateState()`/`dmon.draw()` calls) is consistent with this relocation. |

---

## Group B: Modified upstream files

### Legend

| Severity | Meaning |
|---|---|
| 🔴 CRITICAL | Crash, safety-relevant misbehavior, or data corruption |
| 🟠 HIGH | Real user-facing behavioral regression, no crash |
| 🟡 MEDIUM | Functional gap or perf issue, narrower impact |
| 🟢 LOW | Dead code, stale comment, or informational note |

### B1. `selfdrive/ui/qt/offroad/settings.cc`

**Verdict:** MIXED → fixed
Panel list gained a new "Bluetooth" panel between "WiFi" and "Toggles", shifting Toggles from index 2 → 3. `SettingsWindow::setCurrentPanel()` only resolves a panel by name when the `param` string ends in `"Panel"` — a toggle name like `"ExperimentalMode"` or `"RecordAudio"` doesn't, so it fell through to trusting the caller's raw (now-stale) index.

- 🟠 HIGH — `experimental_mode.cc:16` (`openSettings(2, "ExperimentalMode")`) and `sidebar.cc:73` (`openSettings(2, "RecordAudio")`) both opened the **Bluetooth** panel instead of Toggles.
  - **Fix applied:** `SettingsWindow::setCurrentPanel()`'s `else` branch (the non-`"*Panel"` path) now resolves `index` by looking up the "Toggles" button by name, the same way the `"*Panel"` path already does — instead of trusting the caller's raw index. This fixes both call sites without touching them, and makes any future panel insertion/reorder safe against this exact bug recurring.
- 🟡 MEDIUM — `langToEop` map (syncs UI language → `EOPLanguage` TTS param) was missing `main_ja`/`main_ko`/`main_th`, all real selectable UI languages (`.ts` files exist for all three). Selecting any of them silently set `EOPLanguage` to `"en"`.
  - **Fix applied:** added `{"main_ja","ja"}, {"main_ko","ko"}, {"main_th","th"}` to the map.

### B2. `selfdrive/ui/qt/widgets/controls.h`

**Verdict:** MIXED → fixed
`ParamSpinBoxControl`/`ParamDoubleSpinBoxControl` (new EOP-added classes backing tuning spin boxes) correctly fall back to `default_value` in their constructor when the backing Param is unset, but `default_value` was never stored as a member.

- 🟠 HIGH — `refresh()` (called from `showEvent()`, i.e. every time the settings panel becomes visible) recomputed with raw `atoi`/`atof` and no fallback. Any control whose Param was still unset was silently reset to `0`/`0.0` the moment its panel was shown, overwriting the intended default — for any non-zero default (or a default below `min`), this is a visible, silent value change.
  - **Fix applied:** added a `default_val` member to both classes, set from the constructor's `default_value` argument, and reused in `refresh()` with the same empty-string fallback + `std::clamp` (against the spin box's live `minimum()`/`maximum()`) as the constructor.

### B3. `selfdrive/ui/qt/sidebar.cc`

**Verdict:** MIXED → fixed (2 of 3 findings), 1 deferred
Replaces the comma "CONNECT" (Athena) metric with WIFI/ETH + a new BLE pairing-status metric; layout resized for the smaller EOP sidebar.

- 🟠 HIGH — BLE sidebar indicator only checked classic SPP pairing (`EOPSPPPairedDevice`), never `EOPNavPilotPaired` — the param `ncp_session.py` sets when a BLE GATT session (the fork's primary, exclusive companion-app transport per `docs/eop/04_Integration/BLE_DESIGN.md`) connects. Result: sidebar showed "BLE OFF" the entire time a phone was actually connected via BLE GATT.
  - **Fix applied:** added `gatt_connected = params.getBool("EOPNavPilotPaired")`, OR'd into the "ON" condition alongside `spp_connected`.
- 🟢 LOW — the onroad bookmark-button tap handler was commented out with a stale/incorrect comment claiming `bookmarkButton` "not in Event union" — it is (`cereal/log.capnp:3854`, registered in `cereal/services.py`).
  - **Fix applied:** restored the `initBookmarkButton()`/`pm->send()` call; the `PubMaster` for `"bookmarkButton"` was already being constructed unconditionally, so this now actually uses it.
- 🟡 MEDIUM (deferred) — three `Params()` disk reads happen unconditionally in `updateState()`, which runs at `UI_FREQ` (20 Hz), instead of being cached/event-driven via this fork's own `ParamWatcher` (`selfdrive/ui/qt/util.h`, `QFileSystemWatcher`-based). Real but perf-only (not a correctness bug), and the same pattern pre-exists elsewhere in this fork (`onroad_home.cc`, see B4) — **not fixed in this pass**; flagged for a follow-up that converts both sites to `ParamWatcher` together, since building/testing an event-driven refactor isn't verifiable without a working build/hardware environment.

### B4. `selfdrive/ui/qt/onroad/onroad_home.cc` / `.h`

**Verdict:** MIXED → fixed (1 of 2 findings), 1 deferred + 1 not fixed (cosmetic)
Adds three PIP camera overlays (`rear_overlay_`/`left_overlay_`/`right_overlay_`, reverse gear / turn-signal triggered) and a Bluetooth-pairing-PIN overlay (`pairing_overlay_`), all added to the same `QStackedLayout::StackAll` stack `alerts` lives in.

- 🟠 HIGH — unlike `alerts` (which is explicitly `Qt::WA_TransparentForMouseEvents`), none of the four new overlay widgets had that attribute, so whenever visible they sat on top of `nvg` in Z-order and swallowed mouse events. Concretely: `right_overlay_` (top-right 28%×62%) and `pairing_overlay_` both overlap `ExperimentalButton` (`annotated_camera.cc`, also top-right) — turning on the right blinker or pairing Bluetooth silently blocked the Experimental Mode toggle. `rear_overlay_` (bottom strip) could similarly block `bev_widget`.
  - **Fix applied:** added `Qt::WA_TransparentForMouseEvents` to all three `OverlayCameraWidget`s (in `createOverlays()`) and to `pairing_overlay_` (right after construction), mirroring the existing `alerts` pattern.
- 🟡 MEDIUM (deferred) — `updatePairingOverlay()` calls `Params().get()` twice, unthrottled, every UI frame (20 Hz) — a blocking synchronous file read on the Qt GUI thread for the entire onroad session, not just while pairing. Same `ParamWatcher`-based fix as B3's sidebar finding, deferred for the same reason (best done as one combined follow-up, unverifiable without a build).
- 🟢 LOW (not fixed, cosmetic) — a no-op `mousePressEvent(QMouseEvent*)` override that just forwards to the base class. Harmless dead stub; left as-is.

### B5. `selfdrive/ui/qt/widgets/prime.cc` / `.h`

**Verdict:** MIXED → fixed
Comma Prime subscription/pairing UI stripped (no-cloud-account fork), `PrimeUserWidget`/`PrimeAdWidget`/`SetupWidget` rebranded to static "EOP is ready, all-local" messaging.

- 🟡 MEDIUM — `SetupWidget::openSettings` is still declared (`prime.h`) and still connected by `home.cc:151` ("SetupWidget shows EOP info dialog on click"), but the rewritten constructor never emits it anywhere — upstream emitted it via the now-removed embedded `WiFiPromptWidget`. The widget was inert/non-interactive despite the surrounding code implying it opens a dialog on click.
  - **Fix applied:** added a `mousePressEvent()` override to `SetupWidget` that emits `openSettings(0, "ExoPilotPanel")` — resolved by name via `SettingsWindow::setCurrentPanel()`'s existing `"*Panel"` lookup, so it's robust against future panel reordering (same mechanism as the B1 fix).

### B6. `selfdrive/ui/qt/network/networking.cc`

**Verdict:** MIXED → fixed
`Networking::setPrimeType()` was simplified to unconditionally enable GSM visibility/tethering forwarding (no Prime subscription tiers on this fork), but the `connect(prime_state, &PrimeState::changed, networking, &Networking::setPrimeType)` wiring that used to invoke it (removed from `settings.cc`) was not replaced — the function became dead code, so `WifiManager::ipv4_forward` stayed `false` and GSM settings stayed hidden by default.

- 🟡 MEDIUM — **Fix applied:** call `setPrimeType(PrimeState::Type::PRIME_TYPE_UNKNOWN)` directly from the `Networking` constructor, right after `an`/`wifi` are constructed (the `type` argument is unused by the function body, so any value works — `PRIME_TYPE_UNKNOWN` matches `PrimeState`'s own default).

### B7. `selfdrive/ui/qt/api.cc` / `.h`

**Verdict:** KEEP → 1 LOW fixed
`CommaApi` → `ExoApi` rebrand; `sendRequest()` now immediately emits a synthetic failure instead of making a real network call (no cloud account).

- 🟢 LOW — `sendRequest()` no longer sets `this->reply`, so `HttpRequest::active()` (`return reply != nullptr`) permanently reports `false`. `RequestRepeater` uses `!active()` to avoid overlapping requests on its periodic timer — harmless today (each call just schedules one more `QTimer::singleShot(0, ...)` failure emission), but silently defeats that guard.
  - **Fix applied:** added an `eop_request_pending` bool member, set `true` at the start of `sendRequest()` (with an early-return if already pending) and cleared right before `requestDone` is emitted; `active()` now ORs it with the `reply != nullptr` check.

### B8. `selfdrive/ui/qt/prime_state.cc` / `.h`

**Verdict:** KEEP → 1 LOW fixed
Removes Athena/comma-cloud device polling; emits the locally-stored `PrimeType` param once at startup.

- 🟢 LOW — `handleReply()` was declared in the header but its definition (which used to handle the now-removed `RequestRepeater`'s replies) was deleted, leaving a dead private-method declaration. Compiles fine (unreferenced), but misleading, and would produce a link error the moment anything started calling it again.
  - **Fix applied:** removed the stale declaration from `prime_state.h`.

### B9. `selfdrive/ui/qt/offroad/firehose.cc` / `.h`

**Verdict:** KEEP (not fixed — dead code, no user-visible impact)
Firehose Mode's comma-account upload UI was gutted to a static "not available — offline-first system" message; the panel and its nav button were removed entirely from `settings.cc`'s panel list, making `FirehosePanel` (and its orphaned `refresh()` slot) unreachable dead code. A related dangling reference (`widgets/wifi.cc:24`'s `openSettings(1, "FirehosePanel")`) is also currently unreachable, since nothing instantiates `WiFiPromptWidget` in this fork.
- 🟢 LOW — flagged for a future cleanup pass (delete `FirehosePanel` class + header) rather than fixed now, since it has no runtime effect.

### B10. `selfdrive/ui/qt/util.cc`

**Verdict:** KEEP (informational only, no fix)
`hasLongitudinalControl()` dropped the upstream `AlphaLongitudinalEnabled`/`getAlphaLongitudinalAvailable()` ternary, always returning `car_params.getOpenpilotLongitudinalControl()`. Behaviorally equivalent today given this fork's single-vehicle (Tesla, `opendbc` removed) scope, where `CarParams.alphaLongitudinalAvailable` is presumably never set. Not scored as a bug; noted for visibility if `vehicled`'s `CarParams` construction ever changes.

### All other Group B files (43 files): KEEP, no findings

`api.h`, `body.cc/.h`, `home.cc/.h`, `network/wifi_manager.h`, `sidebar.h`, `settings.h`, `developer_panel.cc`, `experimental_mode.cc/.h` (cosmetic only — its finding is scored under B1), `onboarding.cc`, `software_settings.cc`, `alerts.cc`, `buttons.h`, `hud.cc/.h`, `model.cc/.h`, `prime.h`, `cameraview.cc/.h`, `controls.cc`, `input.cc`, `keyboard.cc`, `offroad_alerts.cc`, `scrollview.cc`, `ssh_keys.cc`, `toggle.cc`, `wifi.cc`, `qt_window.cc/.h`, `window.cc/.h`, `annotated_camera.cc/.h`. All changes in this set are either (a) rebrand/copy/resize for the smaller EOP display, (b) `#ifdef QCOM2`/Wayland removal for Rockchip hardware (dead code either way, since the macro was never defined for this fork's targets), or (c) this session's own already-multiply-reviewed telemetry-panel work. Verified no dangling references from any removed comma-account/DMS-camera feature.

---

## Priority fix order

| Priority | Finding | Status |
|---|---|---|
| P0 | B1: `openSettings(2, ...)` stale panel index (Experimental Mode, Record Audio) | ✅ Fixed |
| P0 | B2: `ParamSpinBoxControl`/`ParamDoubleSpinBoxControl` `refresh()` zeroing unset params | ✅ Fixed |
| P0 | B3: sidebar BLE status ignores BLE GATT (`EOPNavPilotPaired`) | ✅ Fixed |
| P0 | B4: PIP/pairing overlays block clicks to `ExperimentalButton`/`bev_widget` | ✅ Fixed |
| P1 | B1: `langToEop` missing ja/ko/th | ✅ Fixed |
| P1 | B5: `SetupWidget::openSettings` never emitted | ✅ Fixed |
| P1 | B6: `Networking::setPrimeType()` dead code | ✅ Fixed |
| P1 | B3/B4: unthrottled 20 Hz `Params().get()` reads (sidebar + onroad_home) | ⏸ Deferred — needs a `ParamWatcher`-based refactor across both files, best done together and verified on real hardware/build |
| P2 | B3: stale bookmarkButton comment | ✅ Fixed (restored the publish) |
| P2 | B7: `HttpRequest::active()` always false | ✅ Fixed |
| P2 | B8: dead `handleReply()` declaration | ✅ Fixed |
| P2 | B9: `FirehosePanel` dead code cleanup | ⏸ Deferred — no runtime impact, low value under time pressure |
| — | B10: `hasLongitudinalControl()` alpha-longitudinal check dropped | Informational only, no action needed under current single-vehicle scope |

## Fix tracking checklist

- [x] `settings.cc`: `setCurrentPanel()` resolves the Toggles panel by name instead of a hardcoded index
- [x] `settings.cc`: `langToEop` covers ja/ko/th
- [x] `widgets/controls.h`: `ParamSpinBoxControl`/`ParamDoubleSpinBoxControl` store and reuse `default_value` in `refresh()`
- [x] `sidebar.cc`: BLE status also checks `EOPNavPilotPaired`
- [x] `sidebar.cc`: restored the onroad bookmark-button publish, removed the stale comment
- [x] `onroad_home.cc`: all four new overlay widgets are mouse-transparent
- [x] `widgets/prime.cc/.h`: `SetupWidget` is clickable again, emits `openSettings`
- [x] `network/networking.cc`: `setPrimeType()`'s intent applied directly from the constructor
- [x] `api.cc/.h`: `HttpRequest::active()` reflects a pending synthetic request
- [x] `prime_state.h`: removed dead `handleReply()` declaration
- [ ] `sidebar.cc` + `onroad_home.cc`: convert unthrottled per-frame `Params().get()` reads to `ParamWatcher` (deferred — needs a build/hardware environment to verify)
- [ ] `offroad/firehose.cc/.h`: delete the now-fully-unreachable `FirehosePanel` class (deferred — cosmetic cleanup, no runtime impact)

## Known limitation

Same as every other pass in this fork's UI work: **none of these fixes have been compiled or run** — no `scons`/Qt toolchain is available in this environment. All findings and fixes are from static reading of the diffs against `v0.10.0` plus targeted `grep` verification (dangling-reference checks, capnp schema/service registration checks, param-key existence checks). Build and on-device verification remain the one gap no amount of code review can close.
