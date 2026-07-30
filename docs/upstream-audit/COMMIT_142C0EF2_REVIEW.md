# Code Review — Commit `142c0ef24` [EOP: Hardware ID, UI resolution, and system switcher]

**Commit:** `142c0ef24ce2b977a1322b47be2fc8fb96ccb545`  
**Subject:** EOP: Hardware ID, UI resolution, and system switcher  
**Reviewed:** 2026-05-31  
**Files changed:** 22 (+312 / −109)  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification  

---

## Bugs Found

---

### Bug 1 — 🔴 CRITICAL: `rk_device_id.py` is imported but not included in the commit

| | |
|---|---|
| **File** | `system/hardware/rk3576/hardware.py:9`, `system/hardware/rk3588/hardware.py:10` |
| **Root cause** | The commit message states "Add rk_device_id.py with OTP chip ID + eMMC CID readers", but `git diff-tree` shows the file is **not present** in the commit. Both `rk3576/hardware.py` and `rk3588/hardware.py` add `from openpilot.system.hardware.rk_device_id import get_emmc_cid, get_rk_otp_chip_id`. |
| **Failure** | On RK hardware, `manager.py` will crash with `ModuleNotFoundError: No module named 'openpilot.system.hardware.rk_device_id'` on daemon startup. The `get_serial()` and `get_dongle_id()` methods are unreachable. |
| **Fix** | Include `system/hardware/rk_device_id.py` in the commit (or in an immediately preceding commit). Verify with `python3 -c "from openpilot.system.hardware.rk_device_id import get_emmc_cid"`. |

---

### Bug 2 — 🟠 HIGH: `tools/systemd/openpilot-rk3588.service` missing `Conflicts=visionpilot.service`

| | |
|---|---|
| **File** | `tools/systemd/openpilot-rk3588.service` |
| **Root cause** | The commit message says "openpilot.service: Conflicts=visionpilot.service". The RK3576 service template (`system/hardware/rk3576/config/openpilot.service`) gets the `Conflicts=` line, and the new `tools/systemd/openpilot-rk3576.service` also has it. The new `tools/systemd/openpilot-rk3588.service` does **not**. |
| **Failure** | On RK3588, switching to VisionPilot via `switch.sh` leaves `openpilot.service` enabled; both services may attempt to start after reboot, causing resource contention or undefined behavior. |
| **Fix** | Add `Conflicts=visionpilot.service` to `[Unit]` in `tools/systemd/openpilot-rk3588.service`. |

---

### Bug 3 — 🟡 MEDIUM: `switch.sh` uses case-sensitive `grep` for device-tree compatible

| | |
|---|---|
| **File** | `switch.sh:30–36` |
| **Root cause** | `switch.sh` does `grep -q "rk3588" /proc/device-tree/compatible` without `-i`. `installer.cc` in the same commit lower-cases the compatible string before comparison. Some BSPs may emit mixed-case compatible strings (e.g., `RK3588`). |
| **Failure** | Platform detection fails on images with uppercase device-tree strings, causing `switch.sh` to exit with "unknown platform". |
| **Fix** | Change all three `grep` calls to `grep -iq` for consistency with `installer.cc`. |

---

## Other Findings (documented, not fixed)

| Finding | Severity | Notes |
|---------|----------|-------|
| `system/hardware/hw.h` `get_serial()` reads `/sys/bus/nvmem/devices/rockchip-otp0/nvmem` — path is mainline-specific; some BSP kernels use `rockchip-otp1` or different nvmem naming | Low | Graceful fallback to device-tree serial and then `"cccccc"` mitigates the issue. |
| `launch_openpilot.sh` now hard-requires `.venv/bin/python` and exits if missing | Low | Correct for EOP10 workflow, but breaks direct invocation on systems without uv-managed venv. Error message is clear. |
| `system/ui/lib/application.py` calls `HARDWARE.get_device_type()` — method exists on all known subclasses, but `HardwareBase` does not declare it abstract | Low | Pre-existing gap; not introduced here. |
| `selfdrive/ui/qt/offroad/settings.cc` removes `pair_device` and `regulatoryBtn` cleanly; header field removed; no dangling references | Low | Good cleanup. |
| `device.py` `subprocess.Popen` for system switcher is fire-and-forget (no `wait()` or `communicate()`) | Low | Correct — the script triggers a reboot; waiting would block UI indefinitely. |

---

## Priority Fix Order

1. **P0** — Add `system/hardware/rk_device_id.py` to the commit (or adjacent commit) so RK hardware imports resolve.  
2. **P1** — Add `Conflicts=visionpilot.service` to `tools/systemd/openpilot-rk3588.service`.  
3. **P2** — Use `grep -iq` in `switch.sh` for robust platform detection.

---

## Verdict

🔴 **Not safe to keep as-is.** The missing `rk_device_id.py` file is a hard startup crash on RK hardware. The RK3588 service template inconsistency is a system-switcher reliability issue. Both are small fixes; once applied the rest of the commit (UI resolution, cleanup, switcher logic) is sound.
