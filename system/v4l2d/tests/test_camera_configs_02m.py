"""02M 5-camera selection in v4l2d._default_camera_configs(): a role is opened
only from a device path confirmed on hardware (three cameras share the
OX03C10 sensor, so sensor-name discovery would mislabel them)."""
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock  # noqa: TID251

_fake_visionipc = MagicMock()
_fake_visionipc.VisionIpcServer = MagicMock
_fake_visionipc.VisionStreamType = SimpleNamespace(
  VISION_STREAM_ROAD=0, VISION_STREAM_WIDE_ROAD=2, VISION_STREAM_STEREO_LEFT=4,
  VISION_STREAM_STEREO_RIGHT=5, VISION_STREAM_STEREO_DEPTH=6, VISION_STREAM_TELE_ROAD=7)
sys.modules['msgq.visionipc'] = _fake_visionipc
sys.modules['msgq.ipc_pyx'] = MagicMock()
_fake_messaging = MagicMock()
_fake_messaging.log.FrameData.ImageSensor = SimpleNamespace(ox03c10="ox03c10", gc4653="gc4653")
sys.modules['cereal.messaging'] = _fake_messaging
_fake_params_pyx = MagicMock()
_fake_params_pyx.Params = MagicMock
_fake_params_pyx.UnknownKeyName = Exception
sys.modules['openpilot.common.params_pyx'] = _fake_params_pyx

import pytest

import openpilot.system.v4l2d.v4l2d as v4l2d
from openpilot.system.v4l2d.list_cameras import parse_sensor_name, role_for

ALL_ROLES = ["mono_narrow", "mono_wide", "mono_tele", "stereo_left", "stereo_right"]


class _Params:
  def __init__(self, **flags):
    self.flags = flags

  def get_bool(self, key):
    return self.flags.get(key, False)

  def get(self, key):
    return None


@pytest.fixture
def setup(monkeypatch):
  def _apply(paths, **flags):
    monkeypatch.setattr(v4l2d, "_HAL_MIPI_PATHS", paths)
    monkeypatch.setattr(v4l2d, "_params", _Params(**flags))
    monkeypatch.setattr(v4l2d.os.path, "exists", lambda p: True)
    monkeypatch.setattr(v4l2d, "HARDWARE", MagicMock())
  return _apply


def _roles(configs):
  by_id = {c.cam_id: c for c in configs}
  return [c.role for c in v4l2d.CAMERAS_02M if c.cam_id in by_id]


def test_nothing_opens_on_02m_without_confirmed_paths(setup):
  setup({}, EOPTeleEnabled=True, EOPStereoEnabled=True)
  assert v4l2d._default_camera_configs("rk3576") == []


def test_all_five_with_confirmed_paths(setup):
  setup({r: [f"/dev/video{i}"] for i, r in enumerate(ALL_ROLES)}, EOPTeleEnabled=True, EOPStereoEnabled=True)
  configs = v4l2d._default_camera_configs("rk3576")
  assert _roles(configs) == ALL_ROLES
  tele = next(c for c in configs if c.cam_id == "tele_camera")
  assert (tele.msg_name, tele.stream_type, tele.device_path, tele.sensor_name) == \
    ("teleRoadCameraState", 7, "/dev/video2", "ox03c10")
  stereo = [c for c in configs if c.sensor_name == "gc4653"]
  assert all(c.hdr_mode == "sdr" for c in stereo)
  assert next(c for c in configs if c.cam_id == "road_camera").hdr_mode == "hdr4"


def test_gates(setup):
  setup({r: [f"/dev/video{i}"] for i, r in enumerate(ALL_ROLES)})
  assert _roles(v4l2d._default_camera_configs("rk3576")) == ["mono_narrow", "mono_wide"]


def test_stream_name_keys_accepted(setup):
  setup({"road": ["/dev/video7"], "wide_road": ["/dev/video8"]})
  configs = v4l2d._default_camera_configs("rk3576")
  assert [c.device_path for c in configs] == ["/dev/video7", "/dev/video8"]


def test_partial_table_opens_only_confirmed_roles(setup):
  setup({"mono_wide": ["/dev/video1"]}, EOPTeleEnabled=True)
  assert _roles(v4l2d._default_camera_configs("rk3576")) == ["mono_wide"]


def test_duplicate_path_not_opened_twice(setup):
  setup({"mono_narrow": ["/dev/video0"], "mono_wide": ["/dev/video0"]})
  assert _roles(v4l2d._default_camera_configs("rk3576")) == ["mono_narrow"]


def test_dev_pc_uses_plain_fallbacks(setup):
  setup({})
  configs = v4l2d._default_camera_configs("pc")
  assert [c.device_path for c in configs] == ["/dev/video0", "/dev/video1"]


@pytest.mark.parametrize("name, expected", [
  ("m00_b_ox03c10 3-0036", ("ox03c10", 3, 0x36)),
  ("m03_b_gc4653 6-0029", ("gc4653", 6, 0x29)),
  ("rkisp_mainpath", None),
])
def test_parse_sensor_name(name, expected):
  assert parse_sensor_name(name) == expected


def test_i2c_identity_names_the_role():
  assert role_for("ox03c10", 5, 0x36, v4l2d.CAMERAS_02M) == "mono_tele"
  assert role_for("gc4653", 5, 0x29, v4l2d.CAMERAS_02M) == "stereo_left"
  assert role_for("ox03c10", 9, 0x36, v4l2d.CAMERAS_02M) is None
