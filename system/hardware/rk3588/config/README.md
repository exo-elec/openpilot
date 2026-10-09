# RK3588 Target openpilot Configuration

This folder contains only the **openpilot-specific** configuration for an
RK3588 target device. All board-bring-up details (vendor BSP packages, udev
rules, environment variables, NPU power management, device-tree overlays) live
in the closed ExoPilot HAL package and are installed by ExoPilot first.

## Files

| File | Install Target | Purpose |
|------|---------------|---------|
| `openpilot.service` | `/etc/systemd/system/` | systemd service for openpilot |
| `install_openpilot.sh` | run manually as root | Installs openpilot after ExoPilot HAL setup |
| `dt-overlays/` | README only | Canonical overlay source is `~/pilot/exopilot/kernel/rk3588/dt-overlays/` |

## Install order

1. Flash the SOM supplier's Ubuntu 22.04 image.
2. Run ExoPilot HAL hardware setup (installs BSP, udev rules, env, overlays):
   ```bash
   sudo ~/pilot/exopilot/scripts/install/setup_rk3588.sh
   sudo reboot
   ```
3. Run openpilot install:
   ```bash
   sudo /data/openpilot/system/hardware/rk3588/config/install_openpilot.sh
   ```
4. Start openpilot:
   ```bash
   systemctl start openpilot
   ```
