"""AEB braking authority is restricted to built-in 77 GHz radar leads."""

from types import SimpleNamespace

from openpilot.selfdrive.controls.lib.aeb import (
  AEB, AEBState, BrakingController, EgoState, TrackedObject,
)


class _SM:
  def __init__(self, radar_lead: bool):
    lead = SimpleNamespace(
      status=True, radar=radar_lead, dRel=15.0, yRel=0.0,
      vRel=-3.0, modelProb=0.9, radarTrackId=42,
    )
    self.valid = {
      'radarState': True, 'stereoDetections': True,
      'modelV2': True, 'monoDetections': True,
    }
    self._messages = {
      'radarState': SimpleNamespace(leadOne=lead, leadTwo=SimpleNamespace(status=False, radar=False)),
      'carState': SimpleNamespace(vEgo=10.0),
      'stereoDetections': SimpleNamespace(detections=[object()]),
      'modelV2': SimpleNamespace(leadsV3=[object()]),
      'monoDetections': SimpleNamespace(detections=[object()]),
    }

  def __getitem__(self, name):
    return self._messages[name]


def test_built_in_radar_lead_has_aeb_authority():
  objects = AEB.__new__(AEB)._collect_objects(_SM(radar_lead=True))
  assert len(objects) == 1
  assert objects[0].track_id == 42


def test_camera_only_lead_has_no_aeb_authority():
  assert AEB.__new__(AEB)._collect_objects(_SM(radar_lead=False)) == []


def _threat(x: float = 30.0, v_x: float = 0.0, confidence: float = 0.9):
  return TrackedObject(0, x, 0.0, v_x, 0.0, 1.8, 4.5, 'car', confidence)


def _ego(v_ego: float = 12.0):
  return EgoState(v_ego, 0.0, False, False, 0.0)


def test_deceleration_and_jerk_stay_inside_host_and_tc275_limits():
  controller = BrakingController()
  assert controller.DECEL_FULL == -3.48
  assert controller.DECEL_MAX == -3.48
  assert controller.JERK_NORMAL < controller.JERK_EMERGENCY < 5.0
  assert -3.48 <= controller._calculate_decel(_threat(x=10.0), 0.5) <= -1.5


def test_implausible_range_and_low_confidence_do_not_enter_aeb():
  aeb = AEB.__new__(AEB)
  assert not aeb._entry_plausible(_threat(x=0.5), _ego())
  assert not aeb._entry_plausible(_threat(confidence=0.5), _ego())
  assert not aeb._entry_plausible(_threat(x=119.0, v_x=11.0), _ego())


def test_unavoidable_collision_still_requests_bounded_mitigation():
  aeb = AEB.__new__(AEB)
  severe = _threat(x=2.0, v_x=0.0)
  assert aeb._entry_plausible(severe, _ego(v_ego=20.0))
  assert BrakingController()._calculate_decel(severe, 0.1) == -3.48


def test_entry_requires_three_continuous_radar_frames():
  aeb = AEB.__new__(AEB)
  aeb.braking = SimpleNamespace(state=AEBState.IDLE)
  aeb._candidate_track_id = None
  aeb._candidate_frames = 0
  aeb._candidate_x_m = 0.0
  threat = _threat(x=30.0)

  assert aeb._confirmed_entry_threat(threat, _ego()) is None
  assert aeb._confirmed_entry_threat(threat, _ego()) is None
  assert aeb._confirmed_entry_threat(threat, _ego()) is threat
