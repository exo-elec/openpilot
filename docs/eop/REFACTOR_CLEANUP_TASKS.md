# Refactor and cleanup tasks: C++ Qt UI for 01M/02M, repo cleanup

Owner's direction (2026-11-09): PyQt5 is the wrong choice. Go back to EOP10's way (`UI_BACKEND = "cpp"`, the C++ Qt UI built by scons against the system Qt 5.15), and **rewrite that C++ UI to the design of the PyQt5 UI**. Start on `dev/01M`, then rebase `dev/02M` on top of it and adapt the wide profile there. Repos live in `~/Pilot/{openpilot,exopilot}` (projects), `~/Robot/{openrobot,opendojo,exorobot}` (robot projects), `~/RK3576` (this board's OS build, top level: it is not a sibling of the projects) on the PRO-BOX-RK3576.

Boards: **01M** = LubanCat-5 BTB (RK3588, EmbedFire), not RPDZKJ. **02M** = our own RK3576 board (RPDZKJ/Rongpin RP-RK3576-C module), still in development. **PRO-BOX-RK3576** (Rongpin) = the **trial board for 02M development**: same SoC, so SoC-level code and the 02M software are exercised on it, but it has none of the 02M wiring (cameras, EC25 modem, RTS5411S hub, 02M DTB).

Status keys: `[x]` done, `[~]` in progress, `[ ]` to do, `[!]` blocked (reason given). Every step names its proof. Update this file in the same commit as the step it tracks.

## Status by repo (updated 2026-11-09)
| Repo / branch | Pushed tip | Done | Remaining |
|---|---|---|---|
| openpilot `dev/EOP10` | (this commit) | aarch64 venv fix; `.gitmodules` cleanup; EC25 GPIO guard for the trial board; this tracker | 1.5a tinygrad pin (owner); 1.5b full `./test.sh` after the scons build |
| openpilot `dev/01M`, `dev/02M` | **local only** (force-push 0.3, owner) | rebased on EOP10; 01M back to `UI_BACKEND = "cpp"`; 02M = cpp / rk3576 / 1600x600 | Phase 2 build, Phase 3-4 port (01M), Phase 5 (02M), Phase 6 PyQt5 removal |
| exopilot `main` | `5410961`+ | GPU check merged (eGPU commits left out); `hal.platform.identity`; setup guard; docs; `docs/00-START/PROGRESS.md` | eGPU decision; 6.1 Kconfig names (1.10); 02M Phase B (needs the 02M board) |
| RK3576 `os-build` | pushed | `OS_FOR_APPS.md`; test record; Remote Control at boot; Mali proof `tools/qtgl_probe.cc` (LOG 35); `PROGRESS.md` checkpoint | trust prompt + reboot test; build packages; eGPU over USB-C check; v5 image items |

**Remaining plan, in order:** (1) owner: trust prompt, then reboot to prove Remote Control on boot; (2) owner: install the 12 build packages; (3) owner: force-push 01M/02M; (4) 2.2 scons build of the C++ UI with `-j2`, then the full `./test.sh`; (5) eGPU over USB-C check for the next OS build; (6) 2.3-2.4 screenshots; (7) Phase 4 port on 01M, one screen per commit; (8) Phase 5 on 02M; (9) Phase 6 removal of the PyQt5 path.

## Why C++ and not PyQt5 (evidence)
- PyPI has no Linux aarch64 PyQt5 wheel, so `scripts/ensure_pyqt5.sh` compiles it on the board. On the 8 GB PRO-BOX without swap, that compile is **killed for running out of memory** (4 attempts, cgroup `memory.peak` 7.46 GB, `oom_kill` counter rose; RK3576 `os/docs/LOG.md` §33).
- The C++ UI links the system Qt directly (`qtbase5-dev`, already in the image). It needs no Python binding and no per-install build.
- Both UIs are in the tree today, about 13k lines each. The PyQt5 one (`selfdrive/ui/{views,components,layouts,styles,settings}`) is itself a port of the C++ one (`components/theme.py` cites `ui.h`, `sidebar.cc`, `alerts.cc`) plus newer EOP/Nagasware screens. So the work is to **port the new screens and the new look back to C++**, not to start a UI from scratch.

## Phase 0: branch lineage (precondition)
- [x] 0.1 Dependency fix `d429d91f5` on `dev/EOP10` (pyopencl wheel, OpenCV at runtime, fresh `uv.lock`), pushed.
- [x] 0.2 Clean rebase done locally: `dev/EOP10` -> `dev/01M` (`198ff1088`, +1) -> `dev/02M` (`b0d4b1bbe`, +1). Cherry-picked duplicates dropped, trees identical to the remote ones. Local backups: `backup/dev-0{1,2}M-before-rebase-20261109`.
- [!] 0.3 Force-push `dev/01M`/`dev/02M` (`--force-with-lease`): **blocked by the auto-mode safety check; the owner runs it.** Until then, new 01M/02M commits stay local, and the same force-push carries them.

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
- [!] 2.2 Build the C++ UI (`scons -j2`, record time and `memory.peak`). **Blocked: build packages missing on the board** (the handoff said they were installed; they are not). `apt-get -s` simulation: 34 new, **0 removed**, no Mali/Mesa/EGL/X/Rockchip package touched. Owner runs: `sudo apt-get install --no-upgrade capnproto libcapnp-dev libzmq3-dev libqt5charts5-dev libqt5serialbus5-dev libqt5x11extras5-dev libcurl4-openssl-dev libsystemd-dev libglew-dev libglfw3-dev portaudio19-dev cython3`
- [ ] 2.3 Run it on the board's real X display at 1024x600 (no `xvfb`: see RK3576 `OS_FOR_APPS.md`), take a screenshot of each screen as the "before" set.
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

## Phase 4: port, one screen per commit (`dev/01M`)
Order: what the driver sees first, the new screens last. Each step must build (`scons -j2 selfdrive/ui`), start on the board, and match its target screenshot. Commit as `ui(01m): <screen>` with this file updated.
- [ ] 4.1 Theme tokens + QSS (`theme.h`)
- [ ] 4.2 Main window stack, sidebar
- [ ] 4.3 Offroad home
- [ ] 4.4 Settings tab bar + params registry (data-driven pages)
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
