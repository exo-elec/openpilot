# EOP10 Code Quality & Lint Cleanup

**Branch:** `dev/EOP10`  
**Last Updated:** 2026-08-12  
**Scope:** RK3588/openpilot Python lint, shebang, executable-bit and mypy hardening.

---

## Goal

Bring the EOP10 openpilot fork closer to upstream lint discipline so that:

- `./test.sh` (focused dev gate) stays green.
- `./test.sh --full` only fails on pre-existing mypy debt, not on ruff, shebang, large-file, merge-marker or codespell checks.
- Real Python runtime bugs caught during lint are fixed, not just silenced.

---

## Gate Status

| Gate | Command | Status | Notes |
|------|---------|--------|-------|
| Focused dev gate | `./test.sh` | ✅ Pass | RK3588/Rockchip host tests + lint subset |
| Full lint gate | `./test.sh --full` | ⚠️ mypy only | 553 mypy errors in 149 files (down from ~740/203) |
| ruff | `ruff check` | ✅ Pass | No errors |
| Large files | `check_added_large_files --maxkb=120` | ✅ Pass | Pre-existing assets excluded by extension |
| Shebang executable | `check_shebang_scripts_are_executable` | ✅ Pass | ~200 shebang files marked `+x` |
| Shebang format | `check_shebang_format.sh` | ✅ Pass | `#!/usr/bin/env python3` normalized |
| Merge markers | `check_nomerge_comments.sh` | ✅ Pass | No `<<<<<<<` markers |
| codespell | `codespell` | ✅ Pass | Domain terms added to ignore list |

---

## What Was Fixed

### 1. Lint tooling configuration

- **`pyproject.toml`** — Expanded `tool.codespell.ignore-words-list` with EOP/Rockchip domain terms (`AHD`, `FPR`, `metres`, `centre`, `Behaviour`, `optimisation`, `initialise`, `serialisation`, `signalling`, `manoeuvring`, `neighbour`, `deques`, `unparseable`, `keypair`, `falsy`, `Normalise`, `StarD`, `NOO`, `assertIn`, `preemptively`, `pre-emptively`, `canceled`).
- **`scripts/lint/lint.sh`** — Excluded legitimately large pre-existing assets from the 120 KB gate: `\.png|jpg|jpeg|gif|ttf|wav|dlc|onnx$` and `system/hardware/tici/updater`.

### 2. Executable bits / shebangs

- Marked ~200 shebang-bearing files executable with `git add --chmod=+x` so the pre-commit `check_shebang_scripts_are_executable` hook passes without changing file contents.
- Normalized shebangs to `#!/usr/bin/env python3` where needed.

### 3. Real runtime bugs found and fixed

| File | Bug | Fix |
|------|-----|-----|
| `selfdrive/gridd/yolo_objdet.py` | Used `det.cls_id` / `det.score`, which do not exist on the RKNN result object. | Changed to `det.class_id` / `det.confidence`. |
| `selfdrive/surfaced/surfaced.py` | `CellState` enum aliases `DRIVABLE`, `ROUGH`, `OCCUPIED` were missing. | Added the three aliases so downstream costmap code can use them. |
| `system/hardware/rk_device_id.py` | `_read_file` returned `bytes` for some callers and `str` for others, causing `TypeError`. | Split into `_read_text` and `_read_bytes` with explicit return types. |
| `selfdrive/modeld/runners/rknn_runner.py` | Hard-coded `rk3588` platform detection, failed on `rk3588s2` and other variants. | Added multi-platform detection and removed tici dead code. |

### 4. Type-stub / mypy fixes

- **`common/params_pyx.pyi`** — Added missing `put_bool_nonblocking` and `cpp2python` symbols so callers type-check.
- **UI FontWeight** — Casts added in multiple `system/ui/` widgets where `str` defaults were passed to `FontWeight`-typed parameters.
- **Python 3.10 compatibility** — Added `StrEnum` fallback in `tools/lib/logreader.py`.
- **UTC timezone** — Replaced `datetime.UTC` (3.11+) with `datetime.timezone.utc` in `system/athena/registration.py` and `tools/lib/azure_container.py`.

### 5. RK3588/Rockchip/NPU hardening

- Ported KA2-proven RKNN/manager fixes.
- Restored device-falling detection (see `docs/eop/DEVICE_FALLING_DETECTION.md`).
- Removed tici dead code no longer reachable on RK3588 builds.

---

## Remaining Debt

### mypy (553 errors, 149 files)

The remaining errors are concentrated in a few categories:

1. **Params stub mismatch** — `Params.get(block=...)` and `clear_all`/`all_keys`/`get_default_value` are used by EOP/manager code but not declared in `common/params_pyx.pyi`.
2. **Capnp/msgq API typing** — `log_from_bytes` expects `bytes` but `Params.get` can return `None`; `PubMaster`/`SubMaster` are dynamically typed in several daemons.
3. **Hardware abstraction gaps** — `HardwareBase.set_display_power` is referenced in UI code but not in the base class.
4. **Cereal struct drift** — `SideObject` fields used in `reard.py` (`bbox`, `track_id`) do not match the generated schema; `sided.py` returns `Any` from typed helpers.
5. **Daemon-specific logic bugs surfaced by mypy** — `monod.py` unreachable branches, `pathd/long_horizon_planner.py` `None` in waypoint tuples, `obd2d.py` return-type mismatch.

These are real issues, not cosmetic. The next cleanup pass should target the stub/params layer first, then the cereal schema drift, then per-daemon fixes.

---

## Why This Matters for RK3588 / EOP10

On the RK3588 we do not have the upstream comma three toolchain or the same pre-merge CI. Lint gates are the cheapest safety net we have before flashing hardware. Keeping `./test.sh` green means:

- A Python `AttributeError` in `gridd`, `surfaced`, or `monod` is caught on the dev PC, not after a 10-minute board boot cycle.
- Shebang/executable-bit hygiene prevents scripts from silently failing when invoked directly on the device.
- mypy errors point to schema drift between cereal, Params, and daemon code — exactly the kind of mismatch that causes msgq crashes on boot.

---

## Recommended Next Steps

1. **Land the Params stub** — Add `clear_all`, `all_keys`, `get_default_value`, and fix `Params.get` return type (`bytes | None`) across stubs. This alone will clear dozens of errors.
2. **Fix `SideObject` / `sided.py` / `reard.py`** — Align cereal schema with usage, or update the daemons to match the generated structs.
3. **Hardware abstraction** — Add `set_display_power` to `HardwareBase` or guard the call with `hasattr` / platform check.
4. **Per-daemon mypy pass** — Tackle `monod.py`, `pathd/long_horizon_planner.py`, `obd2d.py`, `navd/navd.py` next.
5. **Add CI** — Once the full gate is green, wire `./test.sh --full` into a GitHub Actions workflow so regressions are blocked at PR time.

---

## Files Changed (Summary)

- `pyproject.toml`
- `scripts/lint/lint.sh`
- `common/params_pyx.pyi`
- `selfdrive/gridd/yolo_objdet.py`
- `selfdrive/surfaced/surfaced.py`
- `system/hardware/rk_device_id.py`
- `selfdrive/modeld/runners/rknn_runner.py`
- `system/ui/widgets/*.py`
- `tools/lib/logreader.py`
- `system/athena/registration.py`
- `tools/lib/azure_container.py`
- ~200 executable-bit/shebang fixes across the tree.

Full diff available via `git diff dev/EOP10~317..dev/EOP10` (317 files, ~6,300 insertions / ~6,100 deletions).

---

*This document records the 2026-08 lint cleanup. Update it after each significant lint pass.*
