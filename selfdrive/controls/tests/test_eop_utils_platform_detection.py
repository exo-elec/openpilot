"""Regression test for eop_utils.py::detect_exopilot_platform()'s
2026-08-26 HARDWARE env-var override -- added so this function's rk3576
branch is exercisable without a real device tree, matching the
PlatformRegistry.detect() convention it was previously inconsistent with.
"""

import os

import pytest

from nagaspilot.runtime.eop_utils import detect_exopilot_platform


@pytest.fixture(autouse=True)
def _clear_hardware_env():
  old = os.environ.pop('HARDWARE', None)
  yield
  if old is not None:
    os.environ['HARDWARE'] = old
  else:
    os.environ.pop('HARDWARE', None)


def test_rk3576_env_override_returns_exopilot02m():
  os.environ['HARDWARE'] = 'rk3576'
  assert detect_exopilot_platform() == 'exopilot02m'
