# System Switching — ExoPilot Software Upgrade Chain

> **⚠️ SUPERSEDED (2026-07-06):** This switch.sh / dual-service concept was never built
> and has been abandoned — each ExoPilot hardware generation runs exactly one software
> stack with no runtime switching (see CLAUDE.md). The "Switch to VisionPilot" settings
> button described below was removed as dead code. openpilot supports only ExoPilot 01M
> (RK3588); ExoPilot 02M is VisionPilot's platform exclusively. Kept below for historical
> reference only.

## Overview

Each ExoPilot hardware generation ships with the proven stable software from the previous generation and can upgrade to the next-generation software. Switching is a one-step operation in either direction and requires a reboot.

```
Hardware      Platform   openpilot   visionpilot   dorapilot
────────────  ─────────  ─────────   ───────────   ─────────
ExoPilot 01M   RK3588     ✅ only      ✗             ✗
ExoPilot 02M   RK3576     ✅           ✅             ✗          ← can switch
ExoPilot 02M  RK3576     ✗            ✅ only        ✗
ExoPilot 03M   RK3688     ✗            ✅             ✅          ← can switch (future)
ExoPilot 03M  RK3688     ✗            ✗              ✅ only
ExoPilot 04   RKxxxx     ✗            ✗              ✅ only
```

**H-variant pattern**: each "H" SKU ships with only the newer-generation software — no backward switch option.
- 02M (adds Hailo-8 + driver cam): VisionPilot only, openpilot not supported
- 03M (Hailo-15H + 7 cameras): dorapilot only, VisionPilot not supported

One `visionpilot-rk3576.service` template covers both 02M variants; VisionPilot auto-detects Hailo at runtime.

---

## How It Works

### Service-based switching

Both ADAS systems are managed by systemd. Only one service is enabled at boot:

| Service | Managed by | Default on |
|---|---|---|
| `openpilot.service` | `~/openpilot/switch.sh` | ExoPilot 01M, 02 |
| `visionpilot.service` | `~/visionpilot/switch.sh` | ExoPilot 03M+ |

### switch.sh — inside each repo

Each repo owns its own `switch.sh`. It:
1. Detects the SoC platform from `/proc/device-tree/compatible`
2. Validates the target repo has a service template for this platform (`tools/systemd/<name>-<platform>.service`)
3. Installs both service files to `/etc/systemd/system/` with correct paths
4. Disables all pilot services, enables only the target
5. Reboots

### Platform enforcement

The service template files are what define compatibility. A repo that has no `tools/systemd/<name>-rk3576.service` cannot run on RK3576 — `switch.sh` aborts before changing anything.

---

## Directory Layout

```
~/openpilot/
├── switch.sh                          # Run as root: sudo ./switch.sh visionpilot
├── launch_openpilot.sh
└── tools/systemd/
    ├── openpilot-rk3576.service       # ExoPilot 02M — Conflicts=visionpilot.service
    └── openpilot-rk3588.service       # ExoPilot 01M — no Conflicts (VP not on rk3588)

~/visionpilot/
├── switch.sh                          # Run as root: sudo ./switch.sh openpilot
├── launch_visionpilot.sh
└── tools/systemd/
    ├── visionpilot-rk3576.service     # ExoPilot 02M — Conflicts=openpilot.service
    └── visionpilot-rk3688.service     # ExoPilot 03M — DoraPilot only, no VP
```

---

## Initial Setup (first deploy)

```bash
# On ExoPilot 02M: start with openpilot
cd ~/openpilot
sudo ./switch.sh visionpilot    # or just enable manually:
# sudo cp tools/systemd/openpilot-rk3576.service /etc/systemd/system/
# sudo sed -i 's|@@DIR@@|'"$PWD"'|g' /etc/systemd/system/openpilot.service
# sudo systemctl daemon-reload && sudo systemctl enable openpilot.service
```

---

## Switching via UI

The switch button appears automatically in the settings panel when:
- The sibling repo is cloned alongside this one
- The sibling has a `tools/systemd/<name>-<platform>.service` for this SoC

**openpilot Settings → Device → "Switch to VisionPilot"**
- Only visible on RK3576 (ExoPilot 02M)
- Disabled while engaged (driving)
- Confirms before rebooting

---

## OS & Python

| Component | Version | Notes |
|---|---|---|
| OS | Ubuntu 22.04 LTS | Both repos target 22.04 |
| openpilot Python | 3.12 | Via uv venv at `~/openpilot/.venv` |
| visionpilot Python | 3.10 | System Python via ROS2 Humble |
| ROS2 distro | Humble | `/opt/ros/humble/` |

The two Pythons coexist without conflict — each system runs in a separate process.

---

## Adding a New Generation (dorapilot, etc.)

1. Clone `~/dorapilot`
2. Create `~/dorapilot/tools/systemd/dorapilot-rk3688.service` with `@@DIR@@` placeholder
3. Create `~/dorapilot/switch.sh` (same structure as this file)
4. Create `~/dorapilot/launch_dorapilot.sh`

The "Switch to dorapilot" button appears automatically in VisionPilot's UI on RK3688. No changes needed in openpilot or visionpilot.
