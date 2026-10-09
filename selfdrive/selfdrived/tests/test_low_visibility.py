"""Tests for the severe-weather low-visibility takeover (selfdrived)."""

from cereal import log

from openpilot.selfdrive.selfdrived.selfdrived import (
    LOW_VIS_CONFIRM_FRAMES,
    SelfdriveD,
)
from openpilot.selfdrive.selfdrived.events import Events

EventName = log.OnroadEvent.EventName


class _ModelV2:
  def __init__(self, probs):
    self.laneLineProbs = probs


class _Radar4D:
  def __init__(self, severity=0, blocked=False):
    self.weatherSeverity = severity
    self.visionBlocked = blocked


class _SM:
  """Minimal SubMaster stand-in: valid dict + modelV2/radar4d accessors."""

  def __init__(self, probs, severity=0, blocked=False, radar_valid=True):
    self.valid = {'modelV2': True, 'radar4d': radar_valid}
    self._sockets = {'modelV2': _ModelV2(probs), 'radar4d': _Radar4D(severity, blocked)}

  def __getitem__(self, key):
    return self._sockets[key]


class _Stub:
  pass


def _run(sm, frames=LOW_VIS_CONFIRM_FRAMES):
  stub = _Stub()
  stub.sm = sm
  stub.low_vis_frames = 0
  stub.events = Events()
  for _ in range(frames):
    SelfdriveD._update_low_visibility(stub)
  return stub.events


BLIND_PROBS = [0.01, 0.0, 0.0, 0.02]
VISIBLE_PROBS = [0.9, 0.8, 0.85, 0.7]


def test_blind_camera_plus_heavy_weather_triggers():
  events = _run(_SM(BLIND_PROBS, severity=3))
  assert EventName.lowVisibility in events.names


def test_blind_camera_clear_weather_no_trigger():
  # unmarked road in clear weather must never trigger takeover
  events = _run(_SM(BLIND_PROBS, severity=0))
  assert EventName.lowVisibility not in events.names


def test_blind_camera_plus_radar_blocked_triggers():
  events = _run(_SM(BLIND_PROBS, severity=0, blocked=True))
  assert EventName.lowVisibility in events.names


def test_visible_camera_heavy_weather_no_trigger():
  events = _run(_SM(VISIBLE_PROBS, severity=3))
  assert EventName.lowVisibility not in events.names


def test_brief_blindness_no_trigger():
  events = _run(_SM(BLIND_PROBS, severity=3), frames=LOW_VIS_CONFIRM_FRAMES - 1)
  assert EventName.lowVisibility not in events.names


def test_radar_absent_blind_camera_no_trigger():
  # no radar corroboration available → treat as unmarked road, no takeover
  events = _run(_SM(BLIND_PROBS, radar_valid=False))
  assert EventName.lowVisibility not in events.names


def test_recovery_resets_streak():
  stub = _Stub()
  stub.low_vis_frames = 0
  stub.events = Events()
  stub.sm = _SM(BLIND_PROBS, severity=3)
  for _ in range(LOW_VIS_CONFIRM_FRAMES - 1):
    SelfdriveD._update_low_visibility(stub)
  stub.sm = _SM(VISIBLE_PROBS, severity=3)  # road visible again
  SelfdriveD._update_low_visibility(stub)
  assert stub.low_vis_frames == 0
  assert EventName.lowVisibility not in stub.events.names
