"""Tests for NGP10 DesireHelper's DLAT lane-confidence LCA initiation gate.

Stubs cereal.log directly (rather than importing the real cereal package)
because this dev-PC worktree's cereal/log.capnp hits a pre-existing,
unrelated capnp/opendbc version-skew blocker (Car.RadarData.ErrorDEPRECATED)
at schema-compile time -- documented in nagaspilot/docs/NGP10_FEATURE_MATRIX.md
and reproducible on origin/dev/NGP10 too, not something introduced here.
"""
import sys
import types
from enum import IntEnum


class LaneChangeState(IntEnum):
  off = 0
  preLaneChange = 1
  laneChangeStarting = 2
  laneChangeFinishing = 3


class LaneChangeDirection(IntEnum):
  none = 0
  left = 1
  right = 2


class Desire(IntEnum):
  none = 0
  laneChangeLeft = 1
  laneChangeRight = 2
  keepLeft = 3
  keepRight = 4


_fake_log = types.SimpleNamespace(LaneChangeState=LaneChangeState, LaneChangeDirection=LaneChangeDirection, Desire=Desire)
_fake_cereal = types.ModuleType('cereal')
_fake_cereal.log = _fake_log
sys.modules['cereal'] = _fake_cereal

pkg_openpilot = types.ModuleType('openpilot'); pkg_openpilot.__path__ = []
sys.modules.setdefault('openpilot', pkg_openpilot)
pkg_common = types.ModuleType('openpilot.common'); pkg_common.__path__ = []
sys.modules.setdefault('openpilot.common', pkg_common)

mod_constants = types.ModuleType('openpilot.common.constants')
class CV:
  MPH_TO_MS = 0.44704
mod_constants.CV = CV
sys.modules['openpilot.common.constants'] = mod_constants

mod_realtime = types.ModuleType('openpilot.common.realtime')
mod_realtime.DT_MDL = 0.05
sys.modules['openpilot.common.realtime'] = mod_realtime

sys.path.insert(0, '.')
from types import SimpleNamespace
from selfdrive.controls.lib.desire_helper import DesireHelper


def _make_carstate(v_ego=15.0, left_blinker=False, right_blinker=False,
                    steering_pressed=False, steering_torque=0.0):
  return SimpleNamespace(
    vEgo=v_ego, leftBlinker=left_blinker, rightBlinker=right_blinker,
    leftBlindspot=False, rightBlindspot=False,
    steeringPressed=steering_pressed, steeringTorque=steering_torque,
  )


def _enter_pre_lane_change(dh):
  cs = _make_carstate(left_blinker=True)
  dh.update(cs, lateral_active=True, lane_change_prob=1.0)
  assert dh.lane_change_state == LaneChangeState.preLaneChange


def test_blocks_initiation_on_low_lane_confidence():
  dh = DesireHelper()
  _enter_pre_lane_change(dh)
  cs = _make_carstate(left_blinker=True, steering_pressed=True, steering_torque=1.0)
  dh.update(cs, lateral_active=True, lane_change_prob=1.0, low_lane_confidence=True)
  assert dh.lane_change_state == LaneChangeState.preLaneChange


def test_allows_initiation_on_high_lane_confidence():
  dh = DesireHelper()
  _enter_pre_lane_change(dh)
  cs = _make_carstate(left_blinker=True, steering_pressed=True, steering_torque=1.0)
  dh.update(cs, lateral_active=True, lane_change_prob=1.0, low_lane_confidence=False)
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting


def test_does_not_abort_in_progress_lane_change_on_low_confidence():
  dh = DesireHelper()
  _enter_pre_lane_change(dh)
  cs = _make_carstate(left_blinker=True, steering_pressed=True, steering_torque=1.0)
  dh.update(cs, lateral_active=True, lane_change_prob=1.0, low_lane_confidence=False)
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting

  cs2 = _make_carstate(left_blinker=True)
  dh.update(cs2, lateral_active=True, lane_change_prob=1.0, low_lane_confidence=True)
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting


def test_default_is_no_block_when_caller_omits_the_argument():
  dh = DesireHelper()
  _enter_pre_lane_change(dh)
  cs = _make_carstate(left_blinker=True, steering_pressed=True, steering_torque=1.0)
  dh.update(cs, lateral_active=True, lane_change_prob=1.0)
  assert dh.lane_change_state == LaneChangeState.laneChangeStarting


def _line(y, n=8):
  return SimpleNamespace(x=[5.0 * (i + 1) for i in range(n)], y=[y] * n)


def _model(lead_y=None):
  leads = [SimpleNamespace(prob=0.9, x=[20.0], y=[lead_y], v=[5.0])] if lead_y is not None else []
  return SimpleNamespace(laneLines=[_line(-5.2), _line(-1.8), _line(1.8), _line(5.2)], roadEdges=[_line(-7.0), _line(7.0)], leadsV3=leads)


def _start(dh, model):
  _enter_pre_lane_change(dh)
  cs = _make_carstate(left_blinker=True, steering_pressed=True, steering_torque=1.0)
  dh.update(cs, lateral_active=True, lane_change_prob=1.0, model_v2=model)
  return dh.lane_change_state


def test_gap_guard_blocks_only_when_enabled():
  closing_left = _model(lead_y=-3.0)  # right-positive model frame: left lane
  assert _start(DesireHelper(ngp_lca_gap_eval=True), closing_left) == LaneChangeState.preLaneChange
  assert _start(DesireHelper(), closing_left) == LaneChangeState.laneChangeStarting
  assert _start(DesireHelper(ngp_lca_gap_eval=True), _model()) == LaneChangeState.laneChangeStarting
  assert _start(DesireHelper(ngp_lca_gap_eval=True), None) == LaneChangeState.laneChangeStarting


def test_lane_width_guard_blocks_shoulder_only_when_enabled():
  shoulder = _model()
  shoulder.roadEdges = [_line(-2.4), _line(2.4)]
  assert _start(DesireHelper(ngp_lca_lane_width=True), shoulder) == LaneChangeState.preLaneChange
  assert _start(DesireHelper(), shoulder) == LaneChangeState.laneChangeStarting
