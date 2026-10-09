# Removed: comma tici (Qualcomm) hardware code

**Confirmed 2026-08-12 with the team**: dev/EOP10 targets RK3588 only.
dev/NGP10 (a sibling branch) targets comma's own device 3 (tici,
Qualcomm-based) and legitimately needs this code — this removal is
EOP10-specific, not applied to NGP10.

## What was removed

18 files under `system/hardware/tici/` — comma's Qualcomm/tici-specific
hardware layer: `agnos.py`/`agnos.json`/`updater` (comma's own AGNOS OS
updater), `restart_modem.sh`, `amplifier.py`, `esim.py`/`esim.nmconnection`,
`iwlist.py`, `power_monitor.py`, `precise_power_measure.py`,
`all-partitions.json`, `hardware.py`/`hardware.h`, an `id_rsa` private key
file, and the 5 test files under `tici/tests/` that exercised the deleted
modules.

**Confirmed dead before removing anything**: `system/hardware/__init__.py`
selects `RK3588Hardware` as the actual `HARDWARE` implementation — nothing
in `tici/` is the live hardware abstraction. `TICI` is a repurposed boolean
constant ("Legacy compatibility alias — TICI = any Rockchip platform"), a
different thing entirely from this directory despite the shared name. Every
code path that reached into `tici/` was gated behind checking for a literal
`/AGNOS` marker file (`launch_chffrplus.sh`'s `agnos_init`, `tools/op.sh`'s
build/start/stop branches) — comma's own AGNOS-OS marker, which doesn't
exist on the LubanCat/RK3588 rootfs this fork actually boots.

## Also cleaned up (now-dead references)

- `launch_chffrplus.sh`: removed the `agnos_init` function (referenced
  `system/hardware/tici/agnos.py`/`agnos.json`/`updater`, none of which
  exist anymore) and its `if [ -f /AGNOS ]; then agnos_init; fi` call site.
  `bash -n launch_chffrplus.sh` — clean.
- `tools/scripts/ssh.py`: defaulted `--key` to
  `system/hardware/tici/id_rsa`. Changed default to `None` with an updated
  help string — this tool connects through `ssh.comma.ai`'s comma-account
  proxy anyway, which isn't something an ExoPilot device would authenticate
  against either way. Removed the now-unused `BASEDIR` import.

## Kept — real dependencies, not dead

**`system/hardware/tici/pins.py`** (32 lines, `__init__.py` alongside it)
survives. Two hardware-in-the-loop test files import `GPIO` from it:
- `selfdrive/pandad/tests/test_pandad.py` (`GPIO.STM_RST_N` — panda's STM32
  reset pin)
- `system/ubloxd/tests/test_pigeond.py` (`GPIO.UBLOX_RST_N`,
  `GPIO.GNSS_PWR_EN` — u-blox GPS module reset/power pins)

Both test classes are already `@pytest.mark.tici`-gated and require real
tici hardware physically attached (`HARDWARE.recover_internal_panda()`,
internal-panda concepts that don't apply to this fork's external-panda-over-
USB architecture) — they're correctly-scoped hardware tests this fork
inherited, not accidentally-dead code, and deleting them wasn't asked for or
attempted. The only actual problem was the module-level `from
openpilot.system.hardware.tici.pins import GPIO` breaking test *collection*
(not just execution) if `pins.py` went away. Keeping this one small,
data-only file (pin-number constants, no OS-update or hardware-abstraction
logic) avoids that breakage without leaving any real AGNOS/hardware dead
code behind. Verified both `GPIO.STM_RST_N`/`GPIO.UBLOX_RST_N`/
`GPIO.GNSS_PWR_EN` still resolve after the rest of the directory was removed.

## Verified

- `bash -n launch_chffrplus.sh` — clean
- `python3 -m py_compile tools/scripts/ssh.py` — clean
- Loaded `pins.py` standalone post-cleanup and confirmed all three GPIO
  constants the two test files depend on still resolve
- Grepped the whole repo for any remaining reference to every deleted
  filename — none found
