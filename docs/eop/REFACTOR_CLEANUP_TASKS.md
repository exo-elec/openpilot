# Refactor and cleanup tasks: C++ Qt UI for 01M/02M, repo cleanup

Owner's direction (2026-11-09): PyQt5 is the wrong choice. Go back to EOP10's way (`UI_BACKEND = "cpp"`, the C++ Qt UI built by scons against the system Qt 5.15), and **rewrite that C++ UI to the design of the PyQt5 UI**. Start on `dev/01M`, then rebase `dev/02M` on top of it and adapt the wide profile there. Repos live in `~/Pilot/{openpilot,exopilot,RK3576}` on the PRO-BOX-RK3576 bench.

Status keys: `[x]` done, `[~]` in progress, `[ ]` to do, `[!]` blocked (reason given). Every step names its proof. Update this file in the same commit as the step it tracks.

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
- [ ] 1.4 `.gitmodules`: drop the stale `panda` entry (the submodule was removed in `863c0722d`). Proof: `git submodule status` is clean, and `git config -f .gitmodules --list` has no panda.
- [ ] 1.5 `docs/eop/RK3576_02M_SUPPORT.md` Install section: says the installer "builds PyQt5"; update it after Phase 4.
- [ ] 1.5a **tinygrad pin (owner question):** `CLAUDE.md` says `tinygrad_repo` is pinned to the stable `v0.13.0` tag, "do not follow master". But the gitlink has been at a 0.14.0 checkout since `0b6918a37` (2026-10-09), and `d429d91f5` locked tinygrad 0.14.0 to match it. Decide which one is right: move the submodule back to `v0.13.0` (and re-lock), or update `CLAUDE.md`.
- [ ] 1.5b Run `./test.sh` (the repo's pre-push gate, per `CLAUDE.md`) on `d429d91f5`. It was pushed after the board's hardware tests only.
### exopilot (`main`)
- [ ] 1.6 Bring `codex/gpu-stereo-readiness` into `main`: rebase it (9 commits) onto `main`, run `hal/tests` (expected 126 pass), fast-forward `main`. The GPU check (`hal/gpu.py`), the USB-C phone/eGPU mode switch and its 02M overlays belong in exopilot `main`, not on a side branch.
- [ ] 1.7 Board identity: `hal/hal/platform/bsp.py` maps every `rk3576` to `exopilot02m`. Read `/etc/rk-board.json` first (written by the OS image), fall back to the device tree, and add a `probox` entry without cameras or modem. Proof: a new test in `hal/tests`.
- [ ] 1.8 `setup_rk3576.sh`: `modprobe rockchip_can` does not exist on 6.1 (CAN-FD is built in as `rk3576_canfd`); add a guard so it refuses to install a DTB unless the board identity is `exopilot02m`.
- [ ] 1.9 Docs: RK3576 is Ubuntu 24.04 / kernel 6.1 (not 22.04 / 5.10); `CLAUDE.md` names `rk3576-rongpin.dts`, which does not exist.
- [ ] 1.10 Kernel fragments: list the symbols that do not exist on 6.1 (`VIDEO_V4L2`, `I2C_ROCKCHIP`, `MMC_SDHCI_ROCKCHIP`, `CAN_ROCKCHIP`, `ROCKCHIP_WATCHDOG`) with their 6.1 names; needs the 6.1 Kconfig (supplier kernel tree, on the PC).
### RK3576 (`os-build`)
- [ ] 1.11 `OS_FOR_APPS.md`: replace decision 3 (prebuilt PyQt5 wheel) with "C++ Qt UI, no PyQt5"; keep the Qt5 dev packages (the C++ UI build needs them); drop the PyQt5 rows from `NEXT_BUILD.md`/`PACKAGES.md`.
- [ ] 1.12 `HANDOFF_BOARD_CLAUDE.md`: remove the login password from the file (history keeps it; see 1.3).

## Phase 2: C++ UI build baseline on the board (`dev/01M`)
- [ ] 2.1 Set `UI_BACKEND = "cpp"` on `dev/01M` (this replaces 01M's one commit, "select shared PyQt5 baseline"). Keep `DISPLAY_SIZE = (1024, 600)`.
- [ ] 2.2 Build the C++ UI target only, with a job limit (`scons -j2 selfdrive/ui`). Record time and `memory.peak`; the same out-of-memory risk as PyQt5 applies.
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
- [ ] 6.1 Delete the Python UI (`selfdrive/ui/{main.py,qt.py,views,components,layouts,styles,settings,state.py,...}`) and `scripts/ensure_pyqt5.sh`; the `pyqt5` branch in `launch_openpilot.sh`; the PythonProcess branch for `ui` in `nagaspilot/manager/process_config.py`; `PyQt5`/`PyQt5-sip` from `pyproject.toml`; re-lock.
- [ ] 6.2 `UI_BACKEND` stays as a single value or is removed; decide when 6.1 is done.
- [ ] 6.3 RK3576 docs: PyQt5 no longer part of the OS-to-app contract.

## Log
| Date | Step | Result |
|---|---|---|
| 2026-11-09 | 0.1, 0.2 | fix pushed on EOP10; 01M/02M rebased locally, identical trees |
| 2026-11-09 | 0.3 | force-push blocked by the safety check; owner to run |
| 2026-11-09 | 1.1, 1.2 | `~/code` -> `~/Pilot`; worktree and scratch venv removed; venv rebuilt |
| 2026-11-09 | 1.2a | Remote Control service installed and running as PRO_BOX_RK3576; waits on the owner's trust answer |
| 2026-11-09 | tracker | moved to `dev/EOP10` (pushable without force); 01M/02M rebased on it |
