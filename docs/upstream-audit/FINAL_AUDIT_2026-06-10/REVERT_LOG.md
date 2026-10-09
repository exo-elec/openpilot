# Revert/Fix Log — Phase 3 (defects D1–D18)

Every `[AUDIT-REVERT]`/`[AUDIT-FIX]` commit applied on `EOP10` after Phase 2, in order.
Target tree **T** = tree of the final commit in this log (recorded in LEDGER.md).

| commit | defects | summary | verification |
|---|---|---|---|
| `21d62289e` | D4 D5 D7 D8 D9 | README rebuilt as upstream+clean UTF-8 banner; RELEASES.md restored; stale pytest testpaths dropped; .gitignore cleaned; `.claude/`+`switch.sh` untracked | `iconv` UTF-8 pass; README diff vs upstream = +2 lines |
| `34f1bf399` | D1 D2 D3 | car.capnp CarState @51–60 restored per opendbc `4b203ff5d`, speedLimit→@61; log.capnp Event ordinals restored (reserved-slot renames pinned @107–110, 3 members + 2 structs reinstated, EOP block @150+, ControlsState @65/66 back); services.py upstream entries restored, navRoute decimation None | pycapnp loads both schemas; field assignment test; ordinal scanner = 0 violations vs upstream |
| `8fe6d8fbb` | D10 | params_keys.h upstream initializer-list format; EOP keys appended | key→attr map proven identical (401 keys); diff 533→291 lines |
| `b67525da7` | D14 D15 | 7 missing-import fixes; feedbackd path fix; calibration_fusion imports; stale test_soundd.py removed | ruff F821 clean on all 401 changed files (4 SCons-global false positives only) |
| `0d28eb830` | D11 D13 | DEVICE_CAMERAS upstream entries restored; `pc` platform registered + detect() falls back to pc; base.py LPABase/Thermal classes restored | live import: PC→`Pc`/PC=True; `HARDWARE=rk3588`→RK3588Hardware |
| `11fefca21` | D17 | 7 assets restored byte-exact from upstream LFS (incl. emptied bootstrap-icons.svg) | sha256 vs pointer oid, 7/7 |
| `4f0c8bed9` | D6 | uv.lock regenerated (`uv lock`) | aiortc/pyaudio chain gone; pyopencl retained |
| `0c9013d30` | D18 | SConstruct cache dir: `/data` (aarch64) vs `/tmp` (PC) — dev-PC builds were impossible | scons proceeds past cache setup on PC |

**D12** merged into D14. **D16** resolved KEEP (user: intentional WIP for multi-camera /
rule-based parallel stack — see `ADDED_FILES_AUDIT.md`).

## Sanity on T (step 3.2)

- `py_compile`: 401/401 changed .py files pass
- `ruff F821`: clean (only SCons-injected `Dir`/`Export` false positives in `valhalla_build.py`)
- pycapnp: `car.capnp`, `log.capnp` load; D1 fields assignable; `cereal.services` imports (169 services)
- hardware: PC and forced-RK3588 paths instantiate correct HALs
- **Deferred to equipped dev PC:** full `scons` build + `test_daemon_imports.py` (requires
  initialized third_party submodules, cmake, capnp toolchain; this machine lacks them —
  note `msgq.ipc_pyx` is a compiled artifact). Run there:
  `git submodule update --init --recursive && scons -j$(nproc) && OPENPILOT_STUB_PARAMS_PYX=1 pytest selfdrive/test/test_daemon_imports.py`
