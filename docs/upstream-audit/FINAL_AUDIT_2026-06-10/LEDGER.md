# Final Upstream-Delta Audit & Commit Re-Engineering — Ledger

**Started:** 2026-06-10 • **Branch:** `EOP10` • **Upstream base:** `c085b8af1` (commaai/openpilot)
**Audited tip:** `88a04bc76` • **Tree:** `fb27174aa0d5758317c5c653b92c98a6c9aa9d96`
**Backup:** tag `backup/EOP10-squashed-20260610` (+ `origin/EOP10` until final force-push)

## Goal

Zero unjustified divergence from upstream — every surviving diff must benefit the
RK3576/RK3588 port or a documented EOP feature — then rewrite `EOP10` in place as
~12 foundation-first topic commits. Full method: see plan in this directory's parent
`DELTA_AUDIT.md` + this ledger.

## Pre-established facts

- The 11 squash-rebase rounds that produced `88a04bc76` were verified content-preserving:
  all 12 round tips share tree `fb27174a...`. Prior audits (DELTA_AUDIT steps 0–34,
  22 `COMMIT_*_REVIEW.md`, `CONTROLS_AUDIT.md`) still apply to tree content.
- Baseline delta vs upstream: 1,312 files (+180,494 / −53,911) — `BASELINE_MANIFEST.txt`.
  - 297 M (190 text +9,022/−4,474; ~107 binary assets = LFS pointer materialization)
  - 316 D, 685 A, 11 R, 3 T

## Step progress

| # | Phase | Step | Status | Result / record |
|---|-------|------|--------|-----------------|
| 0.1 | 0 | Backup tag at `88a04bc76` | ✅ done | `backup/EOP10-squashed-20260610` |
| 0.2 | 0 | Baseline manifest + tree hash recorded | ✅ done | `BASELINE_MANIFEST.txt`, tree `fb27174a` |
| 0.3 | 0 | Ledger created | ✅ done | this file |
| 1.1 | 1 | Machine inventory (numstat, groups, hunk counts, prior-review xref) | ✅ done | `INVENTORY.md` (regen: `gen_inventory.sh`). Per-path xref coverage: M 37/297, A 109/685, D 17/316 — most prior coverage was category-level; Phase 2 records per-file verdicts |
| 2.1 | 2 | Meta/dev-machine files audit (`.claude/`, `switch.sh`, pyproject, uv.lock, .github) | ✅ done | `MODIFIED_FILES_AUDIT.md` §meta — defects D4–D9 |
| 2.2 | 2 | Modified upstream text files (190) hunk-level audit | ✅ done | All subsystems audited. Screens: py_compile (0 fail), ruff F821 (D14: 8 bugs), UTF-8 (D4 only). Defects D1–D14 in `MODIFIED_FILES_AUDIT.md` |
| 2.3 | 2 | Deleted upstream files (316) justification mapping | ✅ done | `DELETED_FILES_AUDIT.md` — all groups justified, 0 restorations; D15 dangling imports (4) |
| 2.4 | 2 | Added files (685) reachability pass | ✅ done | `ADDED_FILES_AUDIT.md` — D16: 13 orphan modules (NEEDS-DECISION) |
| 2.5 | 2 | Binary assets LFS-materialization verification | ✅ done | 102/109 byte-exact vs upstream LFS oid; D17 bootstrap-icons emptied + 6 generation mismatches |
| 3.1 | 3 | Apply REVERT verdicts as `[AUDIT-REVERT]` commits → target tree T | ✅ done | `REVERT_LOG.md` — 8 commits, defects D1–D18 (D16=KEEP per user) |
| 3.2 | 3 | Sanity: compileall + daemon import smoke test on T | ✅ done (partial) | py_compile 401/401, F821 clean, pycapnp loads, HAL paths verified; full scons+import test deferred to equipped dev PC (see REVERT_LOG.md) |
| 4.1 | 4 | Rebuild `EOP10-rebuild` from `c085b8af1` as ~12 topic commits | ✅ done | `REBUILD_LOG.md` — 12 commits `063b722ac`..`766de0c9d` |
| 5.1 | 5 | Tree identity: `git diff EOP10-rebuild T` empty | ✅ done | both trees `88e104b4` |
| 5.2 | 5 | Delta accounting: baseline − reverts = rebuilt delta | ✅ done | audit net effect: 39 files +951/−1,390 (= D1–D18) |
| 5.3 | 5 | Final-tree sanity (compileall, imports, lint) | ✅ done (partial) | screens green; scons+daemon-import test deferred to equipped dev PC |
| 5.4 | 5 | Per-commit py_compile (best-effort) | ✅ done | 0 failures across all 12 commits |
| 6.1 | 6 | `git branch -f EOP10 EOP10-rebuild` | ✅ done | EOP10 = 12 topics + rebuild record |
| 6.2 | 6 | Update `DELTA_AUDIT.md` status | ✅ done | header rewritten, points here |
| 6.3 | 6 | Force-push (only after explicit user confirmation) | ✅ done 2026-06-11 | `origin/EOP10` = `bafd89027`; old tip preserved in local tag `backup/EOP10-squashed-20260610` + reflog |

### Follow-up: deep linkage audit (2026-06-12)

| # | Phase | Step | Status | Result / record |
|---|-------|------|--------|-----------------|
| 7.1 | 7 | Deep linkage audit: wiring chains, service/schema/params cross-checks | ✅ done | `DEEP_LINKAGE_AUDIT.md` — defects D19–D23 fixed (incl. CRITICAL manager boot-blocker D21) |
| 7.2 | 7 | Second pass: bidirectional service↔Event parity, read-side/aliased capnp access, C++ service strings | ✅ done | `DEEP_LINKAGE_AUDIT.md` §second-pass — D24–D26 fixed (native ui + plannerd startup crashes, silent reverse-cam/pointcloud failures) |
| 7.3 | 7 | Deferred daemon-import test EXECUTED on this PC (msgq built standalone) | ✅ done — **27/28 pass** | D27 (hw.py PC paths — all dev-PC ops were broken), D28 (modeld None os_version), D29 (reard/sided module-level exit). Sole remaining failure = acados compiled solver → needs scons build only |
| 7.4 | 7 | MPC solvers built on this PC → **28/28 PASS** | ✅ done | D30 (23 LFS-pointer binaries materialized, 82.7 MB sha256-verified — t_renderer/acados/libyuv/raylib/catch2 were corrupt), D31 (`exopilot_shared` phantom dependency → in-repo fallback). Zero LFS pointers remain in repo |
| 7.5 | 7 | **FULL scons C++ BUILD: done building targets** (native ui, loggerd, MPC solvers) | ✅ done | D32–D37 fixed (stale cmake blocks, msgq-fork CL API mismatch, hw.h /data paths, codegen Params, QCOM encoder, UI build wiring). Import test 28/28 with real params_pyx |
| 7.6 | 7 | Test-suite execution (common, messaging 522/522, loggerd deleter 6/6) | ✅ done | D38–D41 fixed (prefix↔fork shm path, deleter import crash, bluetoothd dbus signature, storage split-brain/deadlock). Import test expanded to 40 daemons |

## Verdict legend

- **KEEP** — justified: RK3576/RK3588 port, EOP feature, or no-LFS constraint.
- **REVERT** — no hardware-port benefit; restore upstream bytes.
- **MIXED** — partial revert; kept/reverted hunks listed per file.
- **NEEDS-DECISION** — ambiguous; batched for user.

## How to resume

1. Read this table; find first non-✅ row.
2. Each phase-3 batch = one `[AUDIT-REVERT]` commit; update table + `REVERT_LOG.md` after each.
3. Rebuild commits go on `EOP10-rebuild`; never touch `backup/*` refs.
