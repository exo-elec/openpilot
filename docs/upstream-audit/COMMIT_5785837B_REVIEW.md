# Code Review — Commit `5785837b5` [test: add daemon import smoke test for all 22 critical EOP modules]

**Commit:** `5785837b5b59fc10813c3743d891f3f53ebac10a`  
**Subject:** test: add daemon import smoke test for all 22 critical EOP modules  
**Reviewed:** 2026-05-31  
**Files changed:** 1 (+49 / −0)  
**Method:** line scan + test structure review + coverage audit  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| **MEDIUM** | Test does not set `OPENPILOT_STUB_PARAMS_PYX=1` itself; CI or developers must remember to export it externally | `selfdrive/test/test_daemon_imports.py` | Open |
| **LOW** | No check that imported modules define a `main()` entry point or conform to daemon lifecycle (`__init__` attributes, `get_state`, etc.) | `selfdrive/test/test_daemon_imports.py` | Open |
| **LOW** | `manager.py` is in `system.manager.manager`, not `selfdrive.manager.manager`; path is correct but categorization comment says "Infrastructure" alongside `inferenced` | `selfdrive/test/test_daemon_imports.py` | Open |
| **INFO** | Missing `__main__` guard — not required for pytest but useful for `python selfdrive/test/test_daemon_imports.py` | `selfdrive/test/test_daemon_imports.py` | Open |

---

## Detailed Findings

---

### Finding 1 — MEDIUM: Environment variable not encapsulated in test

| | |
|---|---|
| **File** | `selfdrive/test/test_daemon_imports.py:1-49` |
| **Root cause** | The commit message says to run with `OPENPILOT_STUB_PARAMS_PYX=1`, but the test file itself does not set this variable. On a fresh checkout or CI node, running `pytest selfdrive/test/test_daemon_imports.py` without the env var will fail for any daemon that depends on `params_pyx` (e.g., `controlsd`, `selfdrived`). |
| **Failure** | Import failures that this test is meant to catch will themselves cause the test to fail in CI if the environment is not configured. Developers reading the commit message may run the test correctly, but automated runners (GitHub Actions, Jenkins) may not. |
| **Fix** | Add a `pytest` fixture or module-level setup that sets `os.environ['OPENPILOT_STUB_PARAMS_PYX'] = '1'` before importing the daemon modules. Example: use `monkeypatch.setenv` in a module-scoped autouse fixture, or `importlib.reload` after setting the env var inside the test function. |

**Code:**
```python
# Suggested addition
import os

@pytest.fixture(autouse=True, scope="module")
def stub_params_pyx():
    os.environ.setdefault("OPENPILOT_STUB_PARAMS_PYX", "1")
```

---

### Finding 2 — LOW: Import-only test does not validate daemon interface contract

| | |
|---|---|
| **File** | `selfdrive/test/test_daemon_imports.py:47-49` |
| **Root cause** | `importlib.import_module(module_path)` only verifies that top-level imports resolve. It does not check that the module defines `main()`, initializes attributes used in `get_state()`, or can instantiate its daemon class. The previous commit (`a19317ef3`) introduced stubs that raise `NotImplementedError` on construction; this test would pass even though those daemons cannot run. |
| **Failure** | False confidence: CI is green, but `modeld` or `coordinationd` still crash on dev PC as soon as they try to instantiate `DrivingModelFrame` or `LocalCoord`. |
| **Fix** | Add an optional second-phase check: after import, attempt to retrieve the daemon class and verify it has a `main` attribute. This should be gated behind a separate pytest mark (e.g., `@pytest.mark.runtime`) so import-only CI jobs stay fast. |

---

### Finding 3 — LOW: Minor comment inaccuracy

| | |
|---|---|
| **File** | `selfdrive/test/test_daemon_imports.py:28-29` |
| **Root cause** | The comment says `# Infrastructure` for the last two entries, but `system.inferenced.inferenced` and `system.manager.manager` live under `system/`, not `selfdrive/`. The module paths are correct; the comment is slightly imprecise about the package root. |
| **Failure** | None — purely cosmetic. |
| **Fix** | Update comment to `# system/ infrastructure` or split the two `system.*` entries into their own category block. |

---

### Finding 4 — INFO: Missing `__main__` guard

| | |
|---|---|
| **File** | `selfdrive/test/test_daemon_imports.py` |
| **Root cause** | The file lacks `if __name__ == "__main__": pytest.main([__file__, "-v"])`. This is optional for pytest but helps developers who run `python selfdrive/test/test_daemon_imports.py` directly. |
| **Failure** | Running the file directly does nothing (no tests execute). |
| **Fix** | Add a `__main__` guard at the bottom. |

---

## Other Findings

| Finding | Severity | Notes |
|---------|----------|-------|
| Module list is comprehensive | ✅ OK | Covers all 22 critical daemons as advertised. |
| `@pytest.mark.parametrize` used correctly | ✅ OK | Clean, idiomatic pytest. |
| Docstring includes run instructions | ✅ OK | Helpful for developers. |
| No external dependencies beyond pytest | ✅ OK | Uses only stdlib `importlib` and `pytest`. |
| Policy statement in commit message | ✅ OK | "any new EOP daemon must appear here before merging" — good gate. |

---

## Priority Fix Order

### P1 — CI reliability
1. **`test_daemon_imports.py`** — Encapsulate `OPENPILOT_STUB_PARAMS_PYX=1` inside the test (autouse fixture or `os.environ.setdefault`) so CI does not depend on external env configuration.

### P2 — Test depth
2. **`test_daemon_imports.py`** — Add a second-phase smoke test that instantiates the daemon class (or calls `main()`) behind a separate mark, to catch `NotImplementedError` stubs that import successfully but cannot run.

### P3 — Polish
3. **`test_daemon_imports.py`** — Add `__main__` guard and fix the `# Infrastructure` comment.

---

## Verdict

**Safe to keep with CI follow-up.**

This is a high-value regression test that codifies the "22 daemons must import cleanly" policy. It already caught real bugs fixed in the preceding commit. The only blocker for CI adoption is the un-encapsulated environment variable (`OPENPILOT_STUB_PARAMS_PYX`). Once that is moved into the test file, this test should be added to the standard CI pipeline. No safety or security concerns.
