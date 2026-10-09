"""Exercise EOP lane and turn gates against real cereal enums, without compiled Params."""
from types import SimpleNamespace as NS

import pytest
from cereal import log
from openpilot.selfdrive.controls.lib import desire_helper as module


class Params:
  def get_bool(self, _key):
    return False

  def get(self, _key):
    return None


@pytest.fixture
def helper(monkeypatch):
  monkeypatch.setattr(module, 'Params', Params)
  return module.DesireHelper()


def carstate(v=15.0, left=True, right=False, torque=False, standstill=False):
  return NS(vEgo=v, leftBlinker=left, rightBlinker=right, leftBlindspot=False, rightBlindspot=False,
            steeringPressed=torque, steeringTorque=1.0, standstill=standstill)


def model(confidence):
  line = lambda y: NS(x=[5.0 * i for i in range(8)], y=[y] * 8)  # noqa: E731
  return NS(laneLines=[line(-5.2), line(-1.8), line(1.8), line(5.2)], laneLineProbs=[confidence] * 4,
            laneLineStds=[0.1] * 4, roadEdges=[line(-7.0), line(7.0)], roadEdgeStds=[1.0, 1.0], leadsV3=[])


@pytest.mark.parametrize('confidence,expected', [(0.1, log.LaneChangeState.preLaneChange),
                                               (0.9, log.LaneChangeState.laneChangeStarting)])
def test_lane_confidence_blocks_only_initiation(helper, confidence, expected):
  helper.update(carstate(), True, 1.0, model_v2=model(confidence))
  assert helper.lane_change_state == log.LaneChangeState.preLaneChange
  helper.update(carstate(torque=True), True, 1.0, model_v2=model(confidence))
  assert helper.lane_change_state == expected
  if expected == log.LaneChangeState.laneChangeStarting:
    helper.update(carstate(), True, 1.0, model_v2=model(0.1))
    assert helper.lane_change_state == log.LaneChangeState.laneChangeStarting


@pytest.mark.parametrize('left,right,standstill,v,expected', [
  (True, False, False, 5.0, log.Desire.turnLeft),
  (False, True, False, 5.0, log.Desire.turnRight),
  (True, True, False, 5.0, log.Desire.none),
  (False, False, False, 5.0, log.Desire.none),
  (True, False, True, 0.0, log.Desire.none),
  (True, False, False, 15.0, log.Desire.none),
])
def test_eop_turn_gate_preserved_even_with_inactive_lateral(helper, left, right, standstill, v, expected):
  helper.update(carstate(v, left, right, standstill=standstill), False, 0.0)
  assert helper.desire == expected
