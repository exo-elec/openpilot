# Refactor and cleanup tasks: C++ Qt UI for 01M/02M, repo cleanup

Owner's direction (2026-11-09): PyQt5 is the wrong choice. Go back to EOP10's way (`UI_BACKEND = "cpp"`, the C++ Qt UI built by scons against the system Qt 5.15), and **rewrite that C++ UI to the design of the PyQt5 UI**. Start on `dev/01M`, then rebase `dev/02M` on top of it and adapt the wide profile there. Repos live in `~/Pilot/{openpilot,exopilot}` (projects), `~/Robot/{openrobot,opendojo,exorobot}` (robot projects), `~/RK3576` (this board's OS build, top level: it is not a sibling of the projects) on the PRO-BOX-RK3576.

Boards: **01M** = LubanCat-5 BTB (RK3588, EmbedFire), not RPDZKJ. **02M** = our own RK3576 board (RPDZKJ/Rongpin RP-RK3576-C module), still in development. **PRO-BOX-RK3576** (Rongpin) = the **trial board for 02M development**: same SoC, so SoC-level code and the 02M software are exercised on it, but it has none of the 02M wiring (cameras, EC25 modem, RTS5411S hub, 02M DTB).

Status keys: `[x]` done, `[~]` in progress, `[ ]` to do, `[!]` blocked (reason given). Every step names its proof. Update this file in the same commit as the step it tracks.

## Status by repo (updated 2026-11-09)
| Repo / branch | Pushed tip | Done | Remaining |
|---|---|---|---|
| openpilot `dev/EOP10` | (this commit) | aarch64 venv fix; `.gitmodules` cleanup; EC25 GPIO guard for the trial board; this tracker | 1.5a tinygrad pin (owner); 1.5b full `./test.sh` after the scons build |
| openpilot `dev/01M`, `dev/02M` | pushed (0.3 done) | rebased on EOP10; 01M back to `UI_BACKEND = "cpp"`; 02M = cpp / rk3576 / 1600x600; 4.1 theme tokens (01M) | Phase 2 build, rest of Phase 4 (01M), Phase 5 (02M), Phase 6 PyQt5 removal |
| exopilot `main` | `5410961`+ | GPU check merged (eGPU commits left out); `hal.platform.identity`; setup guard; docs; `docs/00-START/PROGRESS.md` | eGPU decision; 6.1 Kconfig names (1.10); 02M Phase B (needs the 02M board) |
| RK3576 `os-build` | pushed | `OS_FOR_APPS.md`; test record; Remote Control at boot; Mali proof `tools/qtgl_probe.cc` (LOG 35); `PROGRESS.md` checkpoint | trust prompt + reboot test; build packages; eGPU over USB-C check; v5 image items |

**Remaining plan, in order:** (1) owner: trust prompt, then reboot to prove Remote Control on boot; (2) ~~build packages~~ installed; (3) ~~force-push 01M/02M~~ done; (4) ~~2.2 scons build~~ done, then the full `./test.sh`; (5) eGPU over USB-C check for the next OS build; (6) 2.3-2.4 screenshots; (7) Phase 4 port on 01M, one screen per commit; (8) Phase 5 on 02M; (9) Phase 6 removal of the PyQt5 path.

## Why C++ and not PyQt5 (evidence)
- PyPI has no Linux aarch64 PyQt5 wheel, so `scripts/ensure_pyqt5.sh` compiles it on the board. On the 8 GB PRO-BOX without swap, that compile is **killed for running out of memory** (4 attempts, cgroup `memory.peak` 7.46 GB, `oom_kill` counter rose; RK3576 `os/docs/LOG.md` §33).
- The C++ UI links the system Qt directly (`qtbase5-dev`, already in the image). It needs no Python binding and no per-install build.
- Both UIs are in the tree today, about 13k lines each. The PyQt5 one (`selfdrive/ui/{views,components,layouts,styles,settings}`) is itself a port of the C++ one (`components/theme.py` cites `ui.h`, `sidebar.cc`, `alerts.cc`) plus newer EOP/Nagasware screens. So the work is to **port the new screens and the new look back to C++**, not to start a UI from scratch.

## Phase 0: branch lineage (precondition)
- [x] 0.1 Dependency fix `d429d91f5` on `dev/EOP10` (pyopencl wheel, OpenCV at runtime, fresh `uv.lock`), pushed.
- [x] 0.2 Clean rebase done locally: `dev/EOP10` -> `dev/01M` (`198ff1088`, +1) -> `dev/02M` (`b0d4b1bbe`, +1). Cherry-picked duplicates dropped, trees identical to the remote ones. Local backups: `backup/dev-0{1,2}M-before-rebase-20261109`.
- [x] 0.3 Force-push `dev/01M`/`dev/02M` (`--force-with-lease`, owner approved): 01M `1565bdbf8...014dadb32`, 02M `64894cf9b...1b244e1ca`. `git cherry` proof: every replaced remote commit is in the new tip, except 02M's PyQt5 profile commit `c433b5732`, superseded by the cpp one. Old tips kept in local `backup/dev-0{1,2}M-before-rebase-20261109`. Remotes now use SSH (`git@github.com:exo-elec/*.git`).

## Phase 1: cleanup (small, independent; each its own commit)
### Board and folders
- [x] 1.1 Rename the top folder `~/code` to `~/Pilot`; openpilot `.venv` rebuilt there (`uv sync --locked` ok).
- [x] 1.2 Remove the temporary `exopilot-gpu` worktree and the scratch `hal` test venv.
- [x] 1.2a Remote Control at boot: `/home/vcar/claude-remote.sh` (copy in RK3576 `tools/claude-remote.sh`), systemd user service `claude-remote.service`, session **PRO_BOX_RK3576**, starts in `~/Pilot` (trust for the home folder is never saved, so boot would hang at the prompt). Starts on **every reboot** through the desktop autologin (`AutomaticLogin=vcar` starts the user manager, which starts the enabled service); `sudo loginctl enable-linger vcar` is only a fallback if autologin is ever turned off. Owner: answer the trust prompt once (`~/claude-remote.sh attach`, `y`), or each boot waits there.
- [ ] 1.3 Before the board leaves the bench: remove `~/.ssh/id_ed25519` (it can push) and change the `vcar` password (it was written into RK3576 `HANDOFF_BOARD_CLAUDE.md`). **Owner action.**
### openpilot (`dev/EOP10`, then carried to 01M/02M by the rebase)
- [x] 1.4 `.gitmodules`: dropped the stale `panda` entry (the submodule was removed in `863c0722d`) and `carla` (it never had a gitlink). Proof: the paths in `.gitmodules` now equal the gitlinks exactly (13), and `git submodule status` is clean.
- [ ] 1.5 `docs/eop/RK3576_02M_SUPPORT.md` Install section: says the installer "builds PyQt5"; update it after Phase 4.
- [ ] 1.5a **tinygrad pin (owner question):** `CLAUDE.md` says `tinygrad_repo` is pinned to the stable `v0.13.0` tag, "do not follow master". But the gitlink has been at a 0.14.0 checkout since `0b6918a37` (2026-10-09), and `d429d91f5` locked tinygrad 0.14.0 to match it. Decide which one is right: move the submodule back to `v0.13.0` (and re-lock), or update `CLAUDE.md`.
- [~] 1.5b `./test.sh` on `d429d91f5`: ruff and the shebang check **pass**. The gate then always runs `selfdrive/ui/tests` (the PyQt5 UI, even with `--no-pytest`): 14 collection errors, because there is no PyQt5 on the board (moot after Phase 6). The hardware pytest block needs `msgq.ipc_pyx`, so it waits for the scons build (2.2). Direct run of `system/hardware/{rockchip,rk3576}` tests: 17 pass, 1 skip.
### exopilot (`main`)
- [x] 1.6 GPU work into exopilot `main` (`656670c`'s parents): the six commits that do not depend on the eGPU were cherry-picked (Mali OpenCL check `hal/gpu.py` + `test_gpu.py`, GPU source audit docs, IWR1843 gateway docs). The three eGPU/phone-switch commits were **left out on purpose**: `main` removed the external GPU in `e934d0f` (2026-10-13), and its phone host port supersedes them. hal/tests 126 pass. Owner question: is the eGPU wanted back on 02M? (The PRO-BOX OS image keeps its eGPU helper.)
- [x] 1.7 Board identity: `hal.platform.identity` (`board_id()`: `/etc/rk-board.json`, then DT model, then SoC; `is_exopilot_board()`, `soc()`), `BSP` uses it; exopilot `656670c`. On the PRO-BOX: `probox-rk3576`, SoC `rk3576`, not ExoPilot wiring. hal/tests 133 pass.
- [x] 1.7b openpilot: `RK3576Hardware.modem_power_on/off` refuse unless `_on_02m_wiring()` (exopilot `hal.platform.identity.is_exopilot_board()`; older hal keeps the old behaviour). New test: with the EC25 pins marked confirmed, nothing is written on the trial board. rk3576+rockchip tests 18 pass/1 skip with hal, 16/3 without; on the PRO-BOX `_on_02m_wiring()` = False.
- [x] 1.8 `setup_rk3576.sh` refuses on non-02M boards (`/etc/rk-board.json` or DT model; tested on the PRO-BOX: exit 1, override passes) and loads the real CAN-FD module `rk3576_canfd` (built in); exopilot `a876559`.
- [x] 1.9 exopilot docs: RK3576 = Ubuntu 24.04 on the RPDZKJ 6.1 kernel, RK3588 = Debian 12 on the LubanCat 5.10 kernel; the never-committed `rk3576-rongpin.dts` removed from `CLAUDE.md`; exopilot `647e9fb`.
- [ ] 1.10 Kernel fragments: list the symbols that do not exist on 6.1 (`VIDEO_V4L2`, `I2C_ROCKCHIP`, `MMC_SDHCI_ROCKCHIP`, `CAN_ROCKCHIP`, `ROCKCHIP_WATCHDOG`) with their 6.1 names; needs the 6.1 Kconfig (supplier kernel tree, on the PC).
### RK3576 (`os-build`)
- [x] 1.11 RK3576 `OS_FOR_APPS.md` decision 3 is now "C++ Qt UI, no PyQt5"; `NEXT_BUILD.md`/`PACKAGES.md` updated; Qt5 dev packages kept.
- [x] 1.12 Password removed from `HANDOFF_BOARD_CLAUDE.md` (still in git history: change it, step 1.3).

## Phase 2: C++ UI build baseline on the board (`dev/01M`)
- [x] 2.0 **The C++ Qt GL path works on Rockchip** (RK3576 Mali-G52; same method on the RK3588 boards): RK3576 `tools/qtgl_probe.cc` with this UI's `QSurfaceFormat` and camera shaders, on the real display through the vendor `libmali` g24p0: OpenGL ES 3.2, shaders compile and link, 1280x720 NV12 upload 1.41 ms/frame, colours correct, GL error 0. `EGL_EXT_image_dma_buf_import` is present (a zero-copy camera path is possible later). `setSamples(16)` in `qt/util.cc` is not granted on Mali (0 samples): set it to 4 or 0 during the port (4.1). RK3576 LOG 35.
- [x] 2.1 `dev/01M`: new commit "build(01m): back to the native C++/Qt UI" (`UI_BACKEND = "cpp"`, 1024x600; `CLAUDE.md` branch model updated). `dev/02M` rebased on it with its profile resolved to cpp / rk3576 / 1600x600. Local until the owner's force-push (0.3).
- [x] 2.2 C++ UI built on the PRO-BOX (RK3576, 8 GB): `scons -j4 selfdrive/ui/ui` from scratch **~15 min** (813 s compile + 81 s link), four clang jobs at ~400 MB each, no memory pressure; binary 20 MB, links the Mali `libGLESv2`. Needed on the way: (a) **the SoC defines broke every board build**: `-DROCKCHIP/-DRK3588/-DRK3576` rewrote `Hardware::RK3576()`/`ROCKCHIP()` and cereal's `DeviceType` enum, so nothing including `hw.h` or `log.capnp.h` compiled once the SoC was detected; now `SOC_ROCKCHIP`/`SOC_<SOC>` (nothing tested them; `ROCKCHIP_MPP` kept); (b) the 12 build packages, installed by the owner's go (0 removed; also in RK3576 `OPENPILOT_PKGS`); (c) `SConstruct --extra-prefix=DIR` builds against packages unpacked with `dpkg -x` when there is no root, and `SCONS_CACHE_DIR` (`/data` does not exist on the trial board). Full `./test.sh` still to run.
- [~] 2.3 Runs on the board's real X display (`:0`, xcb, Mali `libmali` g24p0), 1024x600, 20 s with no error. It needs `/data` and `/persist` (params, logs, keys): `launch_openpilot.sh` now creates them, owned by the run user (sudo once if needed). Checked against the originals: upstream `launch_chffrplus.sh` uses `/data` but never creates it (comma's OS ships it), and neither did our launcher nor exopilot's `setup_rk35xx.sh`; **owner 2026-11-09: keep it in the launcher.** Mali on the board: stable variant, kernel DDK g25p0 + `libmali` g24p0 (the g29p1 / 6.1.172 image hung at boot and was dropped; RK3576 LOG 38). **Owner 2026-11-09: stay on g24p0.** First "before" shot: `docs/eop/ui_design/before/01m_settings_exopilot.png` (Settings, ExoPilot panel). Seen: the settings nav labels are clipped ("Bluetooth", "Developer"), and so is the "Disengage" button; check whether 4.1's app-wide `QPushButton` padding causes the button clip (4.1/4.4). Remaining: the other screens (home, onroad needs camera/visionipc).
- [ ] 2.4 Screenshot the PyQt5 design as the target set. Needs PyQt5 for a moment, so do it on the PC (x86 wheels exist) with `python -m openpilot.selfdrive.ui.main --demo`. Store under `docs/eop/ui_design/`.

## Phase 3: design inventory (PyQt5 -> C++ map)
| Screen / part | PyQt5 source | C++ today | Work |
|---|---|---|---|
| Theme tokens (geometry, status/alert colours, Inter fonts) | `components/theme.py`, `styles/qss/*.qss` | `ui.h`, `util.cc` | one `theme.h` + one QSS, shared by every widget |
| Main window: home/onroad/settings/onboarding stack | `views/window.py` | `qt/window.cc` | align page order and transitions |
| Sidebar (buttons, signal, TEMP/PANDA/WIFI/BLE cards) | `views/sidebar.py` | `qt/sidebar.cc` | small: the PyQt5 is a port; check the BLE card |
| Offroad home (date, version, chips, drive summary) | `views/home.py`, `widgets/offroad_alerts.py` | `qt/home.cc`, `widgets/drive_stats.cc` | restyle, add the chips |
| **Settings: top tab bar + grouped pages** (replaces the left list) | `views/offroad.py`, `views/settings.py`, `views/panels/*`, `settings/{descriptor,params_registry,exclusions}.py` | `qt/offroad/settings.cc` (left nav, 210 px) | **largest item**: data-driven pages from one params registry |
| EOP panel | `views/panels/exopilot.py` | `qt/offroad/eop_panel.cc` | align |
| Onboarding (EOP terms/training) | `views/onboarding.py` | `qt/offroad/onboarding.cc` | align |
| Onroad: camera, model, HUD, alerts, exp button | `views/driving.py`, `onroad/*`, `components/{hud,alerts,model_renderer}.py` | `qt/onroad/*` | restyle; check the chrome bar |
| Chrome bar | `components/chrome.py` | none | new |
| Side/rear overlays, blind spot, border | `components/{camera_overlay,blind_spot,border_overlay}.py` | `widgets/overlay_camera.cc` | extend |
| BEV | `components/bev.py` | `onroad/bev_widget.cc` | align |
| Warnings | `components/warnings.py` | `onroad/alerts.cc` (part) | new widget |
| **Navigation view** (maneuver icons, route cards) | `views/navigation.py` | none | new |
| **Map panel + OSM tiles** | `components/{map_panel,osm_tiles}.py` | none | new (tile cache, no network on the critical path) |
| **DVR browser/player** (over recordd output) | `views/dvr.py` | none | new (MPP decode via existing wrappers if possible) |
| **Voice panel + popup** | `components/{voice_panel,voice_popup}.py` | none | new |
| Dialogs, keyboard, list controls | `components/{dialogs,keyboard,list_controls}.py` | `widgets/{input,keyboard,controls}.cc` | align |
| Network (Wi-Fi, BT) | `views/panels/network.py`, `components/nm.py` | `qt/network/*` | align |

## Folder rename `nagaspilot/` -> `exopilot/` (owner, 2026-11-10: on every branch from NGP10)
- [x] R.1 `dev/NGP10` `474a4f262`: `git mv` + every lowercase word `nagaspilot` -> `exopilot` (304 files). `NagasPilot` (product name) and `NAGASPILOT_DATA` (env var) unchanged. Proof: byte-compile clean; ruff 89 findings before and after (all pre-existing); product-layer tests 660 pass before / 661 after, same import errors (no `opendbc` / native builds on the board); the one differing test (`test_bigmodel_server_client`) is flaky on both trees (error on 2 of 3 runs each). Pushed as a fast-forward.
- [x] R.2 `dev/EOP10`, `dev/01M`, `dev/02M` rebased onto it (rename-only conflicts resolved as the replayed commit's file + the word rename), then the same word rename on lines their own commits added. Proof: EOP10's and 01M's trees are **identical** to the pre-rebase branches with the rename applied (02M = new 01M + its profile commit). The check caught three `pathd` shims a later commit had changed, restored. Backups: `backup/dev-{EOP10,01M,02M}-before-rename`.
- [x] R.3 EOP10/01M/02M force-pushed (owner's OK; EOP10 `48301a286`). [x] R.4 `dev/EDP10` (dragonpilot line, not based on NGP10): its own rename commit `a9352a698`, pushed.
- [x] R.5 **Missed by R.2:** the tracked package link `openpilot/nagaspilot -> ../nagaspilot` was never renamed, so on a fresh clone of EOP10/01M/02M `import openpilot.exopilot` (189 files) failed with `ModuleNotFoundError`; the board only ran through an untracked local link. Now `openpilot/exopilot -> ../exopilot`: EOP10 `545fa20f2`, cherry-picked to 01M and 02M (plain fast-forwards; the next rebase drops the duplicates). Proof: a clean worktree of each branch imports `openpilot.exopilot.lineage`. NGP10 and EDP10 have no such link. The leftover ignored `nagaspilot/` folder (only `__pycache__`) removed from the board checkout.
- exopilot docs updated (`a7801e0`); RK3576 has no reference.


Order: what the driver sees first, the new screens last. Each step must build (`scons -j2 selfdrive/ui`), start on the board, and match its target screenshot. Commit as `ui(01m): <screen>` with this file updated.
- [x] 4.1 Theme tokens + QSS: `qt/theme.{h,cc}` (geometry, status/alert/palette colours, dark-automotive chrome, `theme::inter()`, `theme::styleSheet()` composing base + dark + per-view sheets from `:/qss/`); the QSS moved to `selfdrive/assets/qss/` and into `assets.qrc` (PyQt5 `style_manager.py` reads the same files); `ui.h` status colours, `alerts.{h,cc}` colours/heights from `theme::`; `setSamples(4)` (2.0). Proof so far: `theme.cc` + rcc-built `:/qss/` compiled with `-std=c++1z` against the system Qt; the composed sheet has all four layers, no `@import`. **Changed on screen:** the app-wide sheet is not applied any more: the theme's `QWidget` background painted a dark box behind every `QLabel` on the not-yet-ported screens (home dates/stats). Sheets are applied per screen as each is ported. The clipped "Disengage" was not 4.1 (the control sets its own padding): three 100 px-minimum buttons in a narrow row, fixed with the settings layout (4.4).
- [x] 4.2 Main window stack, sidebar: `window.cc` already matches `views/window.py` (home/settings/onboarding stack, settings closed on going onroad, interactive timeout); the voice popup comes with 4.11. Sidebar: status colours from the design palette (`theme::GOOD` blue, `WARNING` amber, `DANGER`, `INACTIVE`, `SIDEBAR_BG`, `SIDEBAR_W`). Buttons drawn dark with white icons (owner: comma's white `button_settings.png`/`button_home.png` looked wrong): SETTINGS = gear + text; the offroad home (comma logo) did nothing and is gone; onroad the bookmark button is gear-style "MARK" with the flag. Kept the C++ button geometry: the PyQt5 one put SETTINGS over the signal dots (y 35-152 vs dots at 115) and the home button at y 860, off a 600 px panel. **Bug fixed:** the sidebar and the onroad pairing overlay read `BluetoothPairingPin/Active`, keys that do not exist; bluetoothd writes `EOPBluetoothPairing*`, so BLE PAIRING never showed. Shots: `docs/eop/ui_design/01m/home_sidebar.png`, `settings_device.png` (settings nav labels no longer clipped once the app-wide sheet was dropped).
- [ ] 4.3 Offroad home
- [ ] 4.4 Settings tab bar + params registry (data-driven pages)
  Review of every page on the board (2026-11-10, all scrolled, shots in the scratch tour):
  - **Button shapes: at least six styles.** Device RESET/REVIEW/CHANGE (grey, bold caps); Reboot/Power Off (large, rounded); WiFi FORGET (light grey, square, other font); Software CHECK/SELECT/UNINSTALL (small); multi-choice (Maintain/Pause/Disengage, Aggressive/Standard/Relaxed: "Aggressive" clipped); value fields ("Off", "0.5 s", "2.0 m", "0 mins") with a half-cut right edge. Target: one primary, one secondary, one danger button, one segmented control, one value stepper (theme tokens).
  - **Clipping/overflow:** WiFi "Advanced" button cut at the top right; Bluetooth empty-state text runs off the right edge; Driver Attention description truncated.
  - **ExoPilot panel is one ~40-row list (11 screens of scroll)** with two kinds of header: real section headers (DRIVING, ASSISTANCE, DISPLAY, DEVICE) and plain rows used as sub-headers (Lateral Control, Speed Control, Safety, UI, Recording, Device), plus indentation faked with a full-width space (ALCC Brake Behaviour, BLE Navigation App). One dropdown style (SAM) appears nowhere else.
  - **Categories overlap:** "Device" is a nav page and a section inside ExoPilot; lane change is split (LCA speed, Auto Lane Change, delay, one-change-only, and a separate "Lane Change Assistant (LCA)" toggle); BLE Navigation App sits under speed control.
  - **Proposed tabs** (top tab bar, PyQt5 design): Driving (steering & lane change; speed control), Safety (BSD, SAM, driver attention, LDW, rear camera), Display, Connectivity (Wi-Fi, Bluetooth, BLE nav app), Device (calibration, recording, power, language), System (software, developer). Toggles with icons all the same size.
- [ ] 4.5 EOP panel, onboarding
- [ ] 4.6 Onroad restyle: HUD, alerts, chrome bar, warnings
- [ ] 4.7 Side/rear overlays, blind spot, BEV
- [ ] 4.8 Navigation view
- [ ] 4.9 Map panel + OSM tiles
- [ ] 4.10 DVR browser/player
- [ ] 4.11 Voice panel + popup
- [ ] 4.12 UI tests: port `selfdrive/ui/tests` that test behaviour (not PyQt5 widgets) to the C++ UI's test harness

## Phase 5: carry to 02M
- [ ] 5.1 Rebase `dev/02M` on the new `dev/01M` (its one commit sets `rk3576` and `DISPLAY_SIZE = (1600, 600)`).
- [ ] 5.2 Wide-layout pass: the settings tab bar and the onroad side panels at 1600x600. The PyQt5 `views/offroad.py` was designed for that size (500 px of content height).
- [ ] 5.3 Build and run on this RK3576 board (the board is the 02M SoC).

## Phase 6: remove the PyQt5 path (after 4 and 5 pass)
- [ ] 6.1 Delete the Python UI (`selfdrive/ui/{main.py,qt.py,views,components,layouts,styles,settings,state.py,...}`) and `scripts/ensure_pyqt5.sh`; the `pyqt5` branch in `launch_openpilot.sh`; the PythonProcess branch for `ui` in `exopilot/manager/process_config.py`; `PyQt5`/`PyQt5-sip` from `pyproject.toml`; re-lock.
- [ ] 6.2 `UI_BACKEND` stays as a single value or is removed; decide when 6.1 is done.
- [ ] 6.3 RK3576 docs: PyQt5 no longer part of the OS-to-app contract.

## Log
| Date | Step | Result |
|---|---|---|
| 2026-11-09 | 0.1, 0.2 | fix pushed on EOP10; 01M/02M rebased locally, identical trees |
| 2026-11-09 | 0.3 | force-push blocked by the safety check; owner to run |
| 2026-11-09 | 1.1, 1.2 | `~/code` -> `~/Pilot`; worktree and scratch venv removed; venv rebuilt |
| 2026-11-09 | 1.2a | Remote Control service installed and running as PRO_BOX_RK3576; waits on the owner's trust answer |
| 2026-11-09 | 1.4, 1.5b | `.gitmodules` cleaned (panda, carla); test.sh: ruff + shebang pass, UI tests need PyQt5, hardware block needs scons |
| 2026-11-09 | 1.6-1.9 | exopilot `main` 870a19a -> 656670c: GPU check merged (eGPU commits left out), board identity, setup guard, docs; hal 133 pass |
| 2026-11-09 | 1.11, 1.12 | RK3576 docs: no PyQt5 wheel; password removed |
| 2026-11-09 | 1.7b | modem GPIO guard for the trial board, test added |
| 2026-11-09 | 2.1 | 01M back to cpp (local), 02M rebased (cpp/rk3576/1600x600) |
| 2026-11-09 | 2.2 | blocked: 12 build packages missing; install simulated clean (0 removed) |
| 2026-11-09 | 2.0 | C++ Qt GL path proven on the Mali-G52 (RK3576 LOG 35) |
| 2026-11-09 | status | per-repo status table and remaining plan; PROGRESS docs in exopilot and RK3576 |
| 2026-11-09 | tracker | moved to `dev/EOP10` (pushable without force); 01M/02M rebased on it |
| 2026-11-09 | 0.3 | 01M/02M force-pushed over SSH (owner approved); remotes switched to SSH |
| 2026-11-09 | 4.1 | theme tokens + shared QSS on 01M; smoke-compiled, scons build pending 2.2 |
| 2026-11-09 | 2.2 | UI built on the PRO-BOX (~15 min, -j4); SoC-define clash fixed; build packages installed; `--extra-prefix` |
| 2026-11-09 | 2.3 | UI runs on :0 via Mali; `/data` `/persist` created by `launch_openpilot.sh`; first before-shot |
| 2026-11-09 | 2.3 | launcher check vs upstream (no one created `/data`); Mali pairing g25p0/g24p0 recorded |
| 2026-11-09 | 4.2 | sidebar buttons dark with white icons; comma logo removed; settings click checked |
| 2026-11-10 | 4.2 | Settings button in the status-card style; every settings page reviewed (4.4 notes) |
| 2026-11-10 | R.1, R.2 | `nagaspilot/` -> `exopilot/`: NGP10 pushed; EOP10/01M/02M rebased, trees verified |
| 2026-11-10 | R.3-R.5 | R.3/R.4 ticked; stale `openpilot/nagaspilot` package link fixed on EOP10/01M/02M |
| 2026-11-09 | 4.1, 4.2 | 4.1 done (no app-wide sheet: it boxed every label); 4.2 sidebar palette + BLE pairing key bug; built and checked on :0 |
