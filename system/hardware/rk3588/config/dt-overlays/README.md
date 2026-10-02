# DT Overlays — ExoPilot 01M (RK3588)

Device tree overlays are owned by the BSP layer, not by openpilot.

**Canonical source**: `~/pilot/exopilot/kernel/rk3588/dt-overlays/`

exopilot is installed first on any board and owns all kernel/BSP configuration, including:
- `exopilot01m-usbhub-rts5411.dts` / `.dtsi` — RTS5411S USB 3.0 hub overlay
- `onboard-hub.config` — kernel config fragment for USB onboard hub driver

## How to install

The overlay is compiled and installed by the exopilot first-boot setup script:

```bash
sudo ~/pilot/exopilot/scripts/install/setup_rk3588.sh
sudo reboot
```

openpilot does not build or install overlays. This directory contains only this README.
