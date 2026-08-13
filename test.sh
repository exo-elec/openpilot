#!/usr/bin/env bash
# Local development gate for dev/EOP10.
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

echo "==> Running focused ruff checks"
ruff check \
  system/hardware/rk3588 \
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

echo "==> Running RK3588 host-side tests"
python3 -m pytest \
  system/hardware/rk3588/tests/test_rk3588.py \
  system/hardware/rockchip/tests/test_rockchip.py \
  -v

echo "==> All checks passed"
