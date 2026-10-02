"""Regression test for v4l2d.py's platform guard, added 2026-08-26.

v4l2d hardcodes one board's MIPI array and device-path candidates. Before
this guard, running it on any other board would silently open whatever
/dev/videoN nodes happened to exist and mislabel them as
road/wide_road/stereo_left/stereo_right -- publishing wrong camera
identities on the VisionIPC bus rather than failing visibly. main() must
refuse to start (return 1) instead of reaching V4L2D().run().

These tests derive the allowed set from v4l2d.SUPPORTED_DEVICE_TYPES rather
than spelling board names. Spelling them meant this file asserted 01M's
answers verbatim on 02M -- "rk3588 is allowed, rk3576 is rejected" -- which
is the exact inverse of what that branch does, and it went unnoticed because
test.sh did not run this suite.
"""
import sys
from unittest.mock import MagicMock  # noqa: TID251

# Stub out missing Cython extensions before importing v4l2d.py (same
# pattern as selfdrive/controls/tests/test_dlon.py in this repo).
_fake_msgq_visionipc = MagicMock()
_fake_msgq_visionipc.VisionIpcServer = MagicMock
_fake_msgq_visionipc.VisionStreamType = MagicMock()
sys.modules['msgq.visionipc'] = _fake_msgq_visionipc

_fake_msgq_ipc_pyx = MagicMock()
sys.modules['msgq.ipc_pyx'] = _fake_msgq_ipc_pyx

_fake_cereal_messaging = MagicMock()
_fake_cereal_messaging.log = MagicMock()
sys.modules['cereal.messaging'] = _fake_cereal_messaging

_fake_params_pyx = MagicMock()
_fake_params_pyx.Params = MagicMock
_fake_params_pyx.ParamKeyFlag = MagicMock()
_fake_params_pyx.ParamKeyType = MagicMock()
_fake_params_pyx.UnknownKeyName = Exception
sys.modules['openpilot.common.params_pyx'] = _fake_params_pyx

import pytest

from openpilot.system.v4l2d.v4l2d import main
import openpilot.system.v4l2d.v4l2d as v4l2d_module


class _FakeHardware:
  def __init__(self, device_type):
    self._device_type = device_type

  def get_device_type(self):
    return self._device_type


@pytest.fixture
def _patch_hardware(monkeypatch):
  def _apply(device_type):
    monkeypatch.setattr(v4l2d_module, 'HARDWARE', _FakeHardware(device_type))
  return _apply


OTHER_BOARDS = ('rk3588', 'rk3576', 'some_future_soc')


@pytest.mark.parametrize("device_type", [
  d for d in OTHER_BOARDS if d not in v4l2d_module.SUPPORTED_DEVICE_TYPES
])
def test_a_board_this_branch_does_not_carry_is_rejected(
    device_type, _patch_hardware, monkeypatch):
  """Including the *other* real board, which is the case that matters: its
  camera array is a different shape, so opening this branch's device paths
  on it mislabels real cameras rather than simply finding none."""
  _patch_hardware(device_type)
  called = []
  monkeypatch.setattr(v4l2d_module, 'V4L2D', lambda: called.append(True) or MagicMock())
  assert main() == 1
  assert called == [], "V4L2D() must not be constructed for an unsupported platform"


@pytest.mark.parametrize("device_type", v4l2d_module.SUPPORTED_DEVICE_TYPES)
def test_every_supported_device_type_is_allowed_through(
    device_type, _patch_hardware, monkeypatch):
  """The branch's own board, plus None/'pc' -- the dev and CI fallback, which
  must keep working exactly as it did before the guard was added."""
  _patch_hardware(device_type)
  fake_instance = MagicMock()
  fake_instance.run.return_value = 0
  monkeypatch.setattr(v4l2d_module, 'V4L2D', lambda: fake_instance)
  assert main() == 0
  fake_instance.run.assert_called_once()


def test_the_guard_admits_exactly_one_board():
  """Two boards in the allowed set would mean one of them gets the other's
  device paths -- the failure the guard exists to prevent."""
  boards = [d for d in v4l2d_module.SUPPORTED_DEVICE_TYPES if d not in (None, 'pc')]
  assert len(boards) == 1, boards
