# Code Review — Commit `edd691fb4` [docs(sim): CARLA GPU requirement + MetaDrive]

**Commit:** `edd691fb4eef5417a1a4c80a6107d7cf6f9e7d37`  
**Subject:** docs(sim): document CARLA GPU requirement + MetaDrive as low-resource alternative  
**Reviewed:** 2026-05-31  
**Files changed:** 2 (`docs/eop/DEV_PC_GUIDE.md`, `tools/sim/start_carla.sh`)  
**Method:** line scan + cross-file consistency check  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| 🟢 LOW | `start_carla.sh` comments duplicate CARLA Python client extraction steps already in DEV_PC_GUIDE.md — risk of documentation drift | `tools/sim/start_carla.sh` | Open |
| 🟢 LOW | Hardcoded wheel path `/workspace/PythonAPI/carla/dist/carla-0.9.16-cp312-cp312-manylinux_2_31_x86_64.whl` in comment may become stale if CARLA image layout changes | `tools/sim/start_carla.sh` | Open |
| 🟢 LOW | GPU prerequisite (`nvidia-container-toolkit`) is mentioned but not enforced or checked in the script | `tools/sim/start_carla.sh` | Open |
| ✅ OK | MetaDrive path is correctly documented as the default for low-resource dev machines | `docs/eop/DEV_PC_GUIDE.md` | — |
| ✅ OK | CARLA GPU requirement warning is prominent and accurate (Intel iGPU crashes on UE4 Vulkan) | `docs/eop/DEV_PC_GUIDE.md` | — |

---

## Other Findings

| Finding | Severity | Notes |
|---------|----------|-------|
| `v4l2d` limitation note updated from "CARLA bridge provides camera feed" to "simulator bridge provides camera feed" — covers both simulators | Low | Good generic wording. |

---

## Verdict

✅ **Safe to keep.** Documentation-only commit with a small shell-script comment addition. No runtime impact. Consider adding a runtime `nvidia-smi` / `nvidia-container-cli` check in `start_carla.sh` to fail fast on missing GPU/container toolkit.
