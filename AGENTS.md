# Repository Guidelines

## Project Structure & Module Organization
`selfdrive/` hosts the runtime daemons (controls, planners, sensors, UI) while shared helpers live in `common/`. Messaging schemas stay in `cereal/`, embedded firmware and safety code in `panda/`, and vehicle signals inside `opendbc_repo/opendbc/`. Tooling, simulators, and log viewers live in `tools/`, docs plus governance material stay in `docs/`, and release automation sits in `release/` and `site_scons/`. Tests accompany their modules: `selfdrive/*/tests`, `system/tests/`, `tools/lib/tests/`, `panda/tests/`, and `opendbc_repo/opendbc/car/tests/`.

## Build, Test, and Development Commands
Bootstrap any workstation with `tools/op.sh setup` followed by `source .venv/bin/activate`. The canonical build is `scons -u -j$(nproc)`; target smaller scopes with `scons -j$(nproc) cereal/ common/` or UI-specific targets (`scons -j$(nproc) selfdrive/ui`). Use `pytest -n auto` for the full host suite, and `selfdrive/test/scons_build_test.sh` before submitting to confirm deterministic builds. Run `ruff check .`, `ruff format .`, `codespell`, and `mypy` (all configured in `pyproject.toml`) to match CI.

## Coding Style & Naming Conventions
Python targets 3.11, two-space indentation, and extensive type hints (see `selfdrive/controls/controlsd.py`). Keep modules and functions `snake_case`, classes `CapWords`, and constants `SCREAMING_SNAKE_CASE`. Favor the shared abstractions—update `cereal/` schemas and generated bindings before touching downstream consumers, and add new car assets under `opendbc_repo/opendbc/dbc/` with matching safety stubs. Use Ruff for lint/formatting, MyPy for type regressions, and avoid drive-by stylistic rewrites outside the touched lines.

## Testing Guidelines
Pytest is configured to pick up `test_*.py` across the directories listed in `pyproject.toml:testpaths`, and C++ harnesses named `test_*` use `selfdrive/test/cpp_harness.py`. Honor the shared markers: skip `tici` or `slow` tests unless you have the hardware/time, and mention any skips in your PR. Always run the suites touching your change (`pytest selfdrive/controls/tests/test_lateral_mpc.py`, `opendbc_repo/opendbc/safety/tests/test.sh`, etc.) and report the exact command plus replay route or log you used for validation.

## Commit & Pull Request Guidelines
Recent history uses short release-style subjects (`dragonpilot_0.10.0_prebuild`); keep summaries equally concise and topic-first. Per `docs/CONTRIBUTING.md`, every PR must declare its purpose, list the verification commands you executed, and include evidence for tuning or performance alterations. Keep diffs narrow, document interface updates, and link the relevant Discord thread or GitHub issue so reviewers can trace the context.

## Security & Configuration Tips
Follow `SECURITY.md` for reporting vulnerabilities and `docs/SAFETY.md` for any change that can impact ISO26262 compliance. Mirror the launch scripts (`launch_openpilot.sh`, `launch_env.sh`) or Dockerfiles when defining environment variables (for example `AGNOS`, `CEREAL_CACHE`), never hard-code tokens in `Params`, and document any new configuration defaults in `README.md`.
