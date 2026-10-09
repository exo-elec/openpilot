# Rebuild Log — Phase 4/5: EOP10 re-engineered as 12 topic commits

**Base:** upstream `c085b8af1` • **Target tree T:** `88e104b4` (commit `d5633eb62`)
**Method:** each topic materialized from T by pathspec (`git rm` for deletions,
`git checkout T --` for adds/mods) — deterministic, no cherry-pick conflicts possible.
Path partition: 1,322 changed paths, disjoint, 0 unmatched (driver lists preserved in
session records; regenerate with the rules table below).

## Commit series (foundation-first)

| # | commit | topic | files | delta |
|---|---|---|---|---|
| 1 | `063b722ac` | [INFRA] Remove heavy submodules | 10 | −43 |
| 2 | `17ed1e941` | [INFRA] third_party → submodules | 16 | +340/−4 |
| 3 | `1e790950e` | [THIRD_PARTY] Drop comma-specific libs | 47 | +49,450/−15,286 |
| 4 | `112cf8804` | [BUILD] Root build system + repo meta | 11 | +304/−594 |
| 5 | `a6e0bf42d` | [CEREAL] Messaging schema | 6 | +2,983/−44 |
| 6 | `c0a8de179` | [COMMON] Core utilities | 19 | +2,116/−48 |
| 7 | `7940b316d` | [MODELS] Model scripts + setup helpers | 11 | +1,182/−1 |
| 8 | `c848459e4` | [SYSTEM] RK daemons + HAL | 245 | +24,255/−12,334 |
| 9 | `4c764e409` | [SELFDRIVE/ASSETS] Asset bundle | 222 | +444/−167 |
| 10 | `af0dc850a` | [SELFDRIVE] Controls/perception/UI | 457 | +64,626/−10,840 |
| 11 | `77d082bf7` | [TOOLS] Developer tooling | 157 | +6,819/−14,533 |
| 12 | `766de0c9d` | [DOCS] Architecture + audit records | 117 | +30,507/−19 |

Path partition rules (in order, first match wins): INFRA1 = panda/opendbc*/tinygrad*/
teleoprtc* roots + `.lfsconfig` + `.gitattributes`; INFRA2 = `.gitmodules`, `msgq_repo`,
`rednose_repo`, `third_party/SConscript` + third_party submodule paths (valhalla/
arm_compute/clblast/rockchip*/rknpu/hailort/udsoncan/isotp/pygnssutils/carla);
THIRD_PARTY = rest of `third_party/`; BUILD = root build/meta files + `site_scons/`,
`.github/`, `release/`; CEREAL/COMMON/MODELS(+scripts)/SYSTEM(+launch_*.sh)/
ASSETS(selfdrive/assets)/SELFDRIVE/TOOLS/DOCS(+CLAUDE.md) = by directory.

## Phase 5 verification results

| check | result |
|---|---|
| 5.1 Tree identity | ✅ `git diff EOP10-rebuild(766de0c9d) T(d5633eb62)` empty; both trees = `88e104b4ca2c32c1fbb1b612f766777099dd1b16` |
| 5.2 Delta accounting | Baseline (squashed `88a04bc76`): 1,312 files +180,494/−53,911. Audited rebuild vs upstream: 1,311 files +182,956/−53,843 (insertions grew from restored upstream schema/material + audit docs). Audit net effect vs baseline excluding audit records: **39 files +951/−1,390** — exactly the D1–D18 fixes in `REVERT_LOG.md`. |
| 5.3 Final-tree sanity | py_compile 401/401; ruff F821 clean; pycapnp loads car/log schemas (D1 fields assignable, ordinal scanner 0 violations); services import (169); HAL: PC→Pc, env-forced→RK3588Hardware. Full `scons` + `test_daemon_imports.py` deferred to equipped dev PC (this machine lacks submodule checkouts/cmake/capnp toolchain). |
| 5.4 Per-commit py_compile | ✅ all touched .py files compile at every one of the 12 commits (0 failures; 412 file-compiles) |
