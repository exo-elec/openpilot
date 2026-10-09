# Code Review — Commit `23527f7f6` [fix(adas): remove KeyboardInterrupt from critical daemons]

**Commit:** `23527f7f6a4d84d37445a72e8c0d0520fee196c5`  
**Subject:** fix(adas): remove KeyboardInterrupt from critical control path daemons  
**Reviewed:** 2026-05-30  
**Files changed:** 5 · scope: controlsd.py, plannerd.py, radard.py, pathd.py, selfdrived.py  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification

---

## Bugs Found and Fixed

2 bugs fixed; 2 findings documented but not fixed (design intent / cosmetic).

---

### Bug 1 — MEDIUM: `radard.main()` has no explicit `return 0` — implicit `None` return if loop exits

| | |
|---|---|
| **File** | `selfdrive/controls/radard.py:279` |
| **Root cause** | After removing `except KeyboardInterrupt: return 0`, `radard.main()` only has one explicit return: `return 1` on exception. Every other daemon in this diff (`controlsd`, `pathd`, `selfdrived`) has `return 0` inside the try block. `plannerd` has `return 0` after the try/except. `radard` has neither — it falls off the end of the function if `while 1:` exits, implicitly returning `None`. |
| **Failure** | `while 1:` is effectively unreachable for normal exits, so no immediate runtime crash. But `main()` violates its `-> int` annotation, and any future refactor that adds a `break` (e.g., a shutdown flag) causes `exit(None)` → exit code 0 regardless of the actual exit reason — the process manager cannot distinguish clean exit from unexpected loop termination. |
| **Fix** | Added `return 0` after `except Exception: return 1`. |

---

### Bug 2 — LOW-MEDIUM: `SelfdriveD.run()` calls `t.join()` with no timeout in `finally` block — clean shutdown can hang indefinitely

| | |
|---|---|
| **File** | `selfdrive/selfdrived/selfdrived.py:753` |
| **Root cause** | `run()` has `finally: e.set(); t.join()`. The `params_thread` polls `time.sleep(0.1)` between Params reads. On `KeyboardInterrupt`, `finally` fires, `e.set()` signals the thread to stop, then `t.join()` blocks until the thread exits. If any `params.get_bool()` call inside `params_thread` blocks (filesystem stall, lock contention), `t.join()` never returns. Removing the `except KeyboardInterrupt` in `main()` means this `finally` path is the live production clean-shutdown path for every manager-initiated SIGINT. |
| **Failure** | Manager sends SIGINT → `selfdrived` starts its `finally` cleanup → `params_thread` is stuck in a blocking Params call → `t.join()` hangs → manager eventually fires SIGKILL after its timeout, leaving no clean log. In the old code, this path existed but was less prominent. |
| **Fix** | Changed `t.join()` to `t.join(timeout=2.0)`. Thread polls every 100ms so it exits well within 2s under normal conditions; the timeout prevents a stuck thread from hanging clean shutdown. |

---

## Other Findings (documented, not fixed)

| Finding | Severity | Notes |
|---------|----------|-------|
| `plannerd.py:47` — `return 0` is dead code; `while True:` has no `break` | Low / cosmetic | Was reachable via `except KeyboardInterrupt` before this commit; now unreachable. Harmless — correct value for a hypothetical future normal-exit path. Not removed to preserve intent. |
| Direct-script invocation (`python -m selfdrive.controls.controlsd`) now produces an uncaught-KI traceback on Ctrl+C | Low | By design per commit intent: in production daemons are launched via `manager.py`'s `launcher()` which catches KI cleanly. In dev-PC direct invocation, Ctrl+C now produces stderr traceback + exit 130 instead of clean exit 0. Acceptable tradeoff; adding signal handlers would reintroduce the removed behavior. |

---

## Context Note

The per-daemon `except KeyboardInterrupt` handlers were effectively dead code in production. The `launcher()` wrapper in `system/manager/process.py:41` catches `KeyboardInterrupt` at the process level for all `PythonProcess` daemons. In managed execution, KI raised inside a daemon propagates through `main()` (where the old handler would have caught it) and then to `launcher()`. Both paths produce a clean exit. The behavioral change is: per-daemon `cloudlog.info("DaemonX stopped")` → `launcher()`-level `cloudlog.warning("child ... got SIGINT")`.
