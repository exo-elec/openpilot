# Code Review — Commit `a19317ef3` [fix(schema+imports): resolve all Python import crashes blocking daemon startup]

**Commit:** `a19317ef3887e33bc16cfed2b14a62316ada1daa`  
**Subject:** fix(schema+imports): resolve all Python import crashes blocking daemon startup  
**Reviewed:** 2026-05-31  
**Files changed:** 9 (+99 / −28)  
**Method:** line scan + removed-behavior + cross-file consistency  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| **MEDIUM** | `log.capnp` ordinal reassignment @244→@282, @245→@283 breaks backward compatibility with any existing log readers that index by ordinal | `cereal/log.capnp` | Open |
| **MEDIUM** | `SurfaceShock` capnp ID changed; any external tools that decode `custom.capnp` by type ID will fail on old/new boundaries | `cereal/custom.capnp` | Open |
| **LOW** | `commonmodel_pyx.py` stub raises `NotImplementedError` at construction time — `modeld` import test passes but real dev-PC run will crash as soon as `CLContext()` or `DrivingModelFrame()` is instantiated | `selfdrive/modeld/models/commonmodel_pyx.py` | Open |
| **LOW** | `transformations.py` stubs raise at construction time — same pattern as `commonmodel_pyx.py`; import-safe but not runtime-safe | `common/transformations/transformations.py` | Open |
| **LOW** | `numpy_fast.py` wrappers are pure passthrough to `numpy`; no vectorized-fast path or scalar special-casing that upstream `numpy_fast` historically provided | `common/numpy_fast.py` | Open |
| **INFO** | `IntCodec` duplicates logic that upstream `udsoncan` may add later; no version-gate to prefer upstream if available | `selfdrive/obd2d/adapters/udsoncan_adapter.py` | Open |

---

## Detailed Findings

---

### Finding 1 — MEDIUM: `log.capnp` ordinal bump breaks old-log compatibility

| | |
|---|---|
| **File** | `cereal/log.capnp:3677-3678` |
| **Root cause** | `inferenceJobRequest` and `inferenceJobResult` were moved from `@244/@245` to `@282/@283` because those ordinals collided with `powerState`/`wdgState`. Any existing `.rlog`/`.bz2` files or MCAP logs that contain events at those ordinals will decode to the wrong union member when read by new code, and new logs will be unreadable by old code. |
| **Failure** | Log replay tools, CI regression datasets, or customer field logs recorded before this commit will mis-decode Event union members if they happen to hit those ordinals. The collision was itself a bug, so pre-commit logs at those ordinals were already ambiguous. |
| **Fix** | Document the schema break in `CHANGELOG.md` or `cereal/` migration notes. If backward compatibility is required, add a legacy alias field at the old ordinals marked deprecated. |

**Code:**
```capnp
    # Old (conflicting)
    inferenceJobRequest @244 :Custom.InferenceJobRequest;
    inferenceJobResult @245 :Custom.InferenceJobResult;

    # New
    inferenceJobRequest @282 :Custom.InferenceJobRequest;
    inferenceJobResult @283 :Custom.InferenceJobResult;
```

---

### Finding 2 — MEDIUM: `SurfaceShock` capnp type ID changed

| | |
|---|---|
| **File** | `cereal/custom.capnp:758` |
| **Root cause** | `SurfaceShock` was using the same type ID (`@0xd1e2f3a4b5c6d7e8`) as `InferenceJobRequest`. The fix assigns a new unique ID. Capnp type IDs are embedded in serialized messages for dynamic decoding; changing the ID means any existing serialized `SurfaceShock` messages cannot be decoded by the new schema. |
| **Failure** | If any downstream tool (e.g., surfaced telemetry exporter, Foxglove bridge, or cloud analytics pipeline) relies on the old type ID for dynamic schema selection, it will fail to recognize `SurfaceShock` messages. |
| **Fix** | Same as above: document the break, and if necessary keep a deprecated `SurfaceShockLegacy` struct at the old ID for one release cycle. |

**Code:**
```capnp
-struct SurfaceShock @0xd1e2f3a4b5c6d7e8 {
+struct SurfaceShock @0xb1c2d3e4f5a6b7c8 {
```

---

### Finding 3 — LOW: `commonmodel_pyx.py` stub is import-safe but not instantiation-safe

| | |
|---|---|
| **File** | `selfdrive/modeld/models/commonmodel_pyx.py:10-17` |
| **Root cause** | The stub module is designed so `import modeld` succeeds on x86, but `CLContext()` and `DrivingModelFrame(...)` immediately raise `NotImplementedError`. This is acceptable for an import smoke test, yet any attempt to actually run `modeld.main()` on a dev PC without the compiled `.so` will crash at first frame. |
| **Failure** | Dev-PC end-to-end testing (e.g., CARLA) will hit `NotImplementedError` the moment `modeld` tries to construct a `DrivingModelFrame`. The separate `COMMIT_82F453F4_REVIEW.md` already documents that the `commonmodel_pyx.py` stub used there returns wrong buffer sizes; this commit introduces the initial stub. |
| **Fix** | Acceptable as a scaffolding commit. A future commit should make the stub return correctly-shaped zero-filled arrays (see `COMMIT_82F453F4_REVIEW.md` P0 fixes). |

---

### Finding 4 — LOW: `transformations.py` stubs raise on use

| | |
|---|---|
| **File** | `common/transformations/transformations.py:64-91` |
| **Root cause** | `geodetic2ecef_single`, `ecef2geodetic_single`, and `LocalCoord` are added as `NotImplementedError` stubs so `coordinationd` imports succeed. However, any actual call path that exercises GNSS coordinate transforms (e.g., `coordinationd.update()` with live GPS) will crash. |
| **Failure** | Same pattern as Finding 3: import-safe, not runtime-safe. Dev-PC simulation without GPS may never hit these paths, but a hardware-in-the-loop test with live ublox will. |
| **Fix** | Provide pure-Python fallback implementations (the math is straightforward) or at minimum document that dev-PC runs must disable GPS fusion. |

---

### Finding 5 — LOW: `numpy_fast.py` is pure numpy passthrough

| | |
|---|---|
| **File** | `common/numpy_fast.py:1-14` |
| **Root cause** | The new module simply forwards to `np.clip`, `np.interp`, `np.mean`. Historically `numpy_fast` in upstream openpilot contained scalar-optimized fallbacks (e.g., `clip` with Python `min/max` for scalars, `interp` with linear search for small arrays) to avoid numpy overhead in tight loops. |
| **Failure** | No immediate failure — the module satisfies the import requirement. Potential performance regression if downstream code calls these in hot loops expecting the scalar-fast path. |
| **Fix** | Add scalar-fast paths to match upstream semantics, or rename the module to `numpy_compat.py` to signal it is not performance-optimized. |

---

### Finding 6 — INFO: `IntCodec` may shadow upstream `udsoncan.codec.IntCodec`

| | |
|---|---|
| **File** | `selfdrive/obd2d/adapters/udsoncan_adapter.py:95-108` |
| **Root cause** | A local `IntCodec` is defined because the vendored `udsoncan` lacks `codec.IntCodec`. If the vendored package is ever upgraded to a version that includes it, the local class will shadow the upstream one. |
| **Failure** | None today. Future package upgrade could lead to subtle behavioral differences if upstream `IntCodec` has extra features (e.g., validation). |
| **Fix** | Add a runtime check: `if hasattr(udsoncan.codec, 'IntCodec'): IntCodec = udsoncan.codec.IntCodec`. This makes the local definition a graceful fallback. |

---

## Other Findings

| Finding | Severity | Notes |
|---------|----------|-------|
| `timeout_ms` → `timeoutMs` rename in `custom.capnp` | ✅ OK | Matches capnp camelCase convention; accessor updated in `inferenced.py` correctly. |
| `GNSSMeasurement` import moved to `fusion.py` | ✅ OK | Canonical source is `fusion.py`; removes phantom import. |
| `compute_disparity` alias in `stereod/__init__.py` | ✅ OK | Clean re-export of existing function. |
| `noqa: F401` not needed here | — | Not present in this commit; the `RoadConstraint` fix is in commit `907cb47b8`. |

---

## Priority Fix Order

### P1 — Schema stability
1. **`cereal/log.capnp` + `cereal/custom.capnp`** — Document the ordinal and type-ID breaks in release notes. If customer logs exist at the old ordinals, add deprecated legacy aliases.

### P2 — Dev-PC runtime safety
2. **`commonmodel_pyx.py`** — Return zero-filled arrays of correct shape instead of raising, so dev-PC/CARLA end-to-end runs proceed (detailed in `COMMIT_82F453F4_REVIEW.md`).
3. **`transformations.py`** — Add pure-Python `geodetic2ecef_single` / `LocalCoord` fallbacks so coordinationd runs without compiled Cython.

### P3 — Polish
4. **`numpy_fast.py`** — Add scalar-fast paths or rename to avoid performance expectations.
5. **`udsoncan_adapter.py`** — Gate `IntCodec` behind `hasattr(udsoncan.codec, 'IntCodec')`.

---

## Verdict

**Safe to keep with documentation.**

This commit is a necessary "break glass" fix: 22/22 critical daemons were blocked by import crashes, and the changes un-block development. The schema fixes are correct but introduce breaking changes to capnp ordinals and type IDs that must be documented. The stub modules (`commonmodel_pyx.py`, `transformations.py`) are import-safe scaffolding that will need runtime-safe implementations before dev-PC end-to-end testing is viable. No security or safety regressions.
