#!/usr/bin/env bash
# Local development gate for dev/01M.
# Runs the subset of CI checks that can pass on a dev PC without the closed
# `hal` package. Use `./test.sh --full` to run the upstream lint gate as well.
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
cd "$ROOT"

# Use the project-managed venv if available.
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
fi

FULL=0
if [ "${1:-}" = "--full" ]; then
  FULL=1
  shift
fi

# --no-pytest: skip the host-side hardware pytest step, which needs cereal's
# compiled msgq.ipc_pyx Cython extension (built via `scons`, not by this
# script). Bare CI runners without a scons build step can't pass that step;
# ruff + shebang checks below don't need it. Local/full dev environments
# should keep using the default (no flag) so this step still runs there.
NO_PYTEST=0
args=()
for arg in "$@"; do
  if [ "$arg" = "--no-pytest" ]; then
    NO_PYTEST=1
  else
    args+=("$arg")
  fi
done
set -- "${args[@]}"

echo "==> Running focused ruff checks"
ruff check \
  system/hardware/rk3576 \
  system/hardware/rockchip \
  system/inferenced/rockchip_npu.py \
  system/manager/manager.py \
  system/manager/process.py \
  tools/foxglove \
  tools/scripts/ssh.py \
  tools/convert_models_to_rknn.py \
  tools/sim/tests/test_simulated_components.py \
  tools/sim/tests/run_tests.py \
  selfdrive/locationd/locationd.py \
  selfdrive/modeld/modeld.py \
  selfdrive/selfdrived/selfdrived.py \
  selfdrive/ui \
  "$@"

echo "==> Running shebang format check"
python_files_and_shell_files=$(git ls-files | grep -E '\.(py|sh)$' | sed -E 's/^third_party.*|^msgq.*|^msgq_repo.*|^opendbc.*|^opendbc_repo.*|^cereal.*|^panda.*|^rednose.*|^rednose_repo.*|^tinygrad.*|^tinygrad_repo.*//g' | while read -r f; do [ -f "$f" ] && echo "$f"; done)
if [ -n "$python_files_and_shell_files" ]; then
  bash scripts/lint/check_shebang_format.sh $python_files_and_shell_files
fi

if [ "$FULL" -eq 1 ]; then
  echo "==> Running full upstream lint gate"
  bash scripts/lint/lint.sh "$@"
fi

# The UI suite needs neither msgq nor a display, so it runs even under
# --no-pytest: it is the one part of the gate that covers the new Python UI,
# and skipping it on a bare runner would leave the largest Python addition in
# the tree untested. Its own pytest.ini keeps it independent of the repo's
# xdist/asyncio plugins, and --noconftest keeps it off the compiled Params.
echo "==> Running EOP UI tests"
QT_QPA_PLATFORM=offscreen python3 -m pytest \
  selfdrive/ui/tests \
  -c selfdrive/ui/tests/pytest.ini \
  --noconftest

if [ "$NO_PYTEST" -eq 1 ]; then
  echo "==> Skipping host-side hardware tests (--no-pytest)"
else
  echo "==> Running host-side hardware tests"
  python3 -m pytest \
    system/hardware/tests \
    system/v4l2d/tests \
    selfdrive/gridd/tests/test_camera_specs.py \
    system/hardware/rk3576/tests/test_rk3576.py \
    system/hardware/rockchip/tests/test_rockchip.py \
    -v
fi

echo "==> All checks passed"
