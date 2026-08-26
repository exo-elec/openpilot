"""Regression test for v4l2d.py's platform guard, added 2026-08-26.

v4l2d hardcodes ExoPilot 01M's 4-camera MIPI array and device-path
candidates (hal.platform.rk3588_camera_paths). Before this guard, running
it on any other platform (notably RK3576/ExoPilot 02M, which has no
equivalent hal.platform.rk3576_camera_paths module) would silently open
whatever /dev/videoN nodes happened to exist and mislabel them as
road/wide_road/stereo_left/stereo_right -- publishing wrong camera
identities on the VisionIPC bus rather than failing visibly. main() must
refuse to start (return 1) instead of reaching V4L2D().run() on an
unsupported platform.
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


def test_rk3576_is_rejected_without_touching_v4l2d(_patch_hardware, monkeypatch):
  _patch_hardware('rk3576')
  called = []
  monkeypatch.setattr(v4l2d_module, 'V4L2D', lambda: called.append(True) or MagicMock())
  assert main() == 1
  assert called == [], "V4L2D() must not be constructed for an unsupported platform"


def test_unknown_platform_is_rejected(_patch_hardware, monkeypatch):
  _patch_hardware('some_future_soc')
  called = []
  monkeypatch.setattr(v4l2d_module, 'V4L2D', lambda: called.append(True) or MagicMock())
  assert main() == 1
  assert called == []


def test_rk3588_is_allowed_through_to_v4l2d(_patch_hardware, monkeypatch):
  _patch_hardware('rk3588')
  fake_instance = MagicMock()
  fake_instance.run.return_value = 0
  monkeypatch.setattr(v4l2d_module, 'V4L2D', lambda: fake_instance)
  assert main() == 0
  fake_instance.run.assert_called_once()


def test_pc_is_allowed_through_to_v4l2d(_patch_hardware, monkeypatch):
  """pc is the dev/CI fallback -- must keep working exactly as before this
  guard was added (some_future_soc/rk3576 are the only new rejections)."""
  _patch_hardware('pc')
  fake_instance = MagicMock()
  fake_instance.run.return_value = 0
  monkeypatch.setattr(v4l2d_module, 'V4L2D', lambda: fake_instance)
  assert main() == 0
  fake_instance.run.assert_called_once()
