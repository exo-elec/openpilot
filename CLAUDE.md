# CLAUDE.md

## Review and sub-agents

These add to any review or model-routing rules above (for example a local inference stack or a deep-review tier); follow those first.

- **Scrutinize (`/scrutinize`).** Skill at `.claude/skills/scrutinize/SKILL.md`. Use it for every review, audit, sanity check or second opinion on a plan, PR, diff or design, and run it on your own non-trivial changes before reporting them done.
- **Antigravity sub-agent (`antigravity`, `.claude/agents/antigravity.md`).** Sends a self-contained task to the Google Antigravity CLI (`agy`, Gemini models on the owner's paid plan). Use it when the owner asks for Antigravity, agy or Gemini, for an independent non-Claude second opinion during `/scrutinize` on large or risky changes, or to offload a well-specified side task in parallel. It runs a preflight before every use: `agy` installed and signed in on this PC, and plan quota left (`agy -p /quota`). If any check fails, continue without it and say so. It is a cloud model: do not use it where this repo requires local-only processing. Default model `gemini-4-neon-high`; `gemini-4-carbon-high` for the hardest design work. Verify its output before relying on it.
