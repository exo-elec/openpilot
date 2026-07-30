# Code Review — Commit `a00344102` [fix(phase-3): standardize error handling]

**Commit:** `a0034410269bcd01f829161defc98dee3d94a978`  
**Subject:** fix(phase-3): standardize error handling in 8 critical control daemons  
**Reviewed:** 2026-05-30  
**Files changed:** 8 · scope: controlsd, plannerd, radard, pathd, selfdrived, pandad, reard, coordinationd  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification

---

## Bugs Found and Fixed

4 bugs fixed; 1 finding documented but not fixed (pre-existing).

---

### Bug 1 — HIGH: `except Exception: return 1` in all 7 daemons absorbs crashes before `launcher()`'s `sentry.capture_exception()` — fatal crashes invisible to Sentry

| | |
|---|---|
| **Files** | `controlsd.py:447`, `plannerd.py:45`, `radard.py:278`, `pathd.py:1448`, `selfdrived.py:764`, `reard.py:279`, `coordinationd.py:363` |
| **Root cause** | `system/manager/process.py:46` — `launcher()` wraps `mod.main()` in `try/except Exception: sentry.capture_exception(); raise`. Before this commit, exceptions from daemon code propagated through `main()` (no handler) and reached `launcher()`, triggering Sentry. After this commit, `except Exception as e: cloudlog.exception(...); return 1` in each `main()` absorbs the exception and returns normally. `launcher()` sees a clean return — `sentry.capture_exception()` is never called. |
| **Failure** | controlsd raises `RuntimeError` mid-drive. Old: exception reaches `launcher()`, Sentry fires with full traceback, process exits non-zero. New: `except Exception` in `main()` catches it, logs locally, returns 1. `launcher()` receives normal return. Sentry never fires. Fatal ADAS crashes vanish from remote crash reporting for all 7 daemons. |
| **Fix** | Changed `return 1` → `raise` in all 7 `except Exception` handlers. `cloudlog.exception()` still fires for local logs (daemon-specific context), then the exception re-propagates to `launcher()` for Sentry capture. Added defensive `return 0` back to `radard.main()` (after the except block) to satisfy the `-> int` return-type annotation for the unreachable while-loop-exit path. |

---

### Bug 2 — MEDIUM: `GlobalD.run()` logs exception and re-raises; `coordinationd.main()` catches and logs again — every fatal crash produces two log entries

| | |
|---|---|
| **File** | `selfdrive/coordinationd/coordinationd.py:349–351, 362–363` |
| **Root cause** | `GlobalD.run()` had `except Exception as e: cloudlog.error(f"GlobalD: fatal error: {e}"); raise`. The re-raised exception propagated to `main()`'s new `except Exception as e: cloudlog.exception(f"CoordinationD fatal error: {e}")`. Two distinct log entries for one crash event, at different severity levels (`error` vs `exception`). |
| **Failure** | A fatal error in `GlobalD.update()` produces two log entries. Log-analysis deduplication fails, alerting pipelines fire twice, triage confusion between the `error`-level (no traceback) and `exception`-level (with traceback) entries. |
| **Fix** | Removed the `cloudlog.error()` from `GlobalD.run()`'s except block — it now just re-raises (`except Exception: raise`). The single `cloudlog.exception()` in `main()` is the authoritative log point. |

---

### Bug 3 — MEDIUM: `RearD.run()` logs exception and re-raises; `reard.main()` catches and logs again — same double-logging pattern

| | |
|---|---|
| **File** | `selfdrive/reard/reard.py:265–267, 278–279` |
| **Root cause** | `RearD.run()` had `except Exception as e: cloudlog.error("RearD: main loop error: %s", e); raise`. Identical to coordinationd's double-logging. |
| **Failure** | Same as Bug 2 — two log entries per fatal RearD crash, alerting fires twice. |
| **Fix** | Removed `cloudlog.error()` from `RearD.run()`. Now just `except Exception: raise`. |

---

### Bug 4 — LOW: `except KeyboardInterrupt` in `coordinationd.main()` is unreachable dead code

| | |
|---|---|
| **File** | `selfdrive/coordinationd/coordinationd.py:359–361` |
| **Root cause** | `GlobalD.run()` has its own `except KeyboardInterrupt: cloudlog.info("GlobalD: shutting down")` that handles SIGINT internally without re-raising. SIGINT is consumed inside `run()` and control returns normally to `main()`. The `except KeyboardInterrupt: return 0` added to `main()` can never be reached. |
| **Failure** | A developer assumes the `except KeyboardInterrupt` in `main()` is the active SIGINT handler and modifies it, while the real handler in `run()` is unchanged. No runtime failure, but misleading code that causes maintenance errors. |
| **Fix** | Removed the dead `except KeyboardInterrupt` block from `coordinationd.main()`. `GlobalD.run()`'s handler is the sole SIGINT handler and the `return 0` after `GlobalD().run()` is the clean-exit path. |

---

## Other Findings (documented, not fixed)

| Finding | Severity | Notes |
|---------|----------|-------|
| `pandad.py`: `process.wait()` return code (./pandad binary exit) never inspected; subprocess crash loops silently | Low | Pre-existing behavior — the Python wrapper is a supervisor that intentionally restarts the binary. `return 0` added by this commit is correctly placed outside the while loop (reached only on clean shutdown via `do_exit=True`). Not a regression; the restart-on-crash behavior is correct. |
