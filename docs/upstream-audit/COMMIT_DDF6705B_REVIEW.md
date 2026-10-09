# Code Review — Commit `ddf6705b9` [chore(skills): tune Claude Code skills]

**Commit:** `ddf6705b97a1f4fbb00525d3997f3072701e5ff1`  
**Subject:** chore(skills): tune Claude Code skills for EOP/openpilot codebase  
**Reviewed:** 2026-05-31  
**Files changed:** 7 (all new `.claude/skills/**/*.md`)  
**Method:** content review + cross-reference against AGENTS.md  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| 🟢 LOW | `misc/README.md` is a placeholder ("_(none yet)_"); either populate or remove | `.claude/skills/misc/README.md` | Open |
| ✅ OK | debug-mantra correctly notes hardware debugger unavailability on RK targets | `.claude/skills/engineering/debug-mantra/SKILL.md` | — |
| ✅ OK | scrutinize adds EOP-specific checklist (capnp patterns, daemon init, DELTA_AUDIT.md) | `.claude/skills/engineering/scrutinize/SKILL.md` | — |
| ✅ OK | post-mortem removes JIRA/ADF posting; replaces with print-only + DELTA_AUDIT.md refs | `.claude/skills/engineering/post-mortem/SKILL.md` | — |
| ✅ OK | management-talk replaces JIRA fetch with `gh CLI` + DELTA_AUDIT.md | `.claude/skills/productivity/management-talk/SKILL.md` | — |

---

## Other Findings

| Finding | Severity | Notes |
|---------|----------|-------|
| `debug-mantra` EOP-specific failure modes list is accurate and covers the most common dev-PC crash classes (capnp List(Struct), missing `__init__` attrs, bad imports) | Low | Good addition. Consider adding "cereal shared-memory architecture mismatch (ARM .so on x86_64)" since that is now documented in DEV_PC_GUIDE.md. |
| `post-mortem` skill references `management-talk` handoff via relative path (`../../productivity/management-talk/SKILL.md`) | Low | Path is correct for the current directory structure. |
| `scrutinize` skill references `docs/upstream-audit/DELTA_AUDIT.md` — good upstream attribution check | Low | Reinforces existing AGENTS.md rule. |

---

## Verdict

✅ **Safe to keep.** Pure documentation commit with no runtime impact. Skills are well-scoped, correctly reference EOP-specific context, and remove external dependencies (JIRA/ADF). Minor nit: `misc/README.md` is empty scaffolding.
