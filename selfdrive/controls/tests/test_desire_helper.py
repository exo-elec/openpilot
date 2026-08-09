"""Tests for EOP DesireHelper's DLAT lane-confidence LCA initiation gate."""
import sys
from unittest.mock import MagicMock

_fake_params_pyx = MagicMock()
_fake_params_pyx.Params = MagicMock
_fake_params_pyx.ParamKeyFlag = MagicMock()
_fake_params_pyx.ParamKeyType = MagicMock()
_fake_params_pyx.UnknownKeyName = Exception
sys.modules['openpilot.common.params_pyx'] = _fake_params_pyx

_fake_msgq = MagicMock()
_fake_msgq.Context = MagicMock
_fake_msgq.Poller = MagicMock
_fake_msgq.SubSocket = MagicMock
_fake_msgq.PubSocket = MagicMock
_fake_msgq.SocketEventHandle = MagicMock
_fake_msgq.toggle_fake_events = MagicMock()
_fake_msgq.fake_event_callback = MagicMock()
_fake_msgq.async_sleep = MagicMock()
_fake_msgq.async_wait_for_one_event = MagicMock()
_fake_msgq.MAX_FDS = 64
sys.modules['msgq.ipc_pyx'] = _fake_msgq

from cereal import log

from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState
from openpilot.selfdrive.controls.lib.dlat import LANEFUL_TO_LANELESS_THRESH


def _make_model_v2(lane_line_probs):
  mv = MagicMock()
  mv.laneLineProbs = lane_line_probs
  return mv


def _make_carstate(v_ego=15.0, left_blinker=False, right_blinker=False,
                    steering_pressed=False, steering_torque=0.0):
  cs = MagicMock()
  cs.vEgo = v_ego
  cs.leftBlinker = left_blinker
  cs.rightBlinker = right_blinker
  cs.leftBlindspot = False
  cs.rightBlindspot = False
  cs.steeringPressed = steering_pressed
  cs.steeringTorque = steering_torque
  return cs


class TestDesireHelperLaneConfidenceGate:
  def setup_method(self):
    self.dh = DesireHelper()
    self.dh._load_params = lambda: None
    self.dh.lca_enabled = False
    self.dh.auto_lane_change = False
    self.dh.gap_eval_enabled = False
    self.dh.lane_width_check_enabled = False

  def _enter_pre_lane_change(self):
    cs = _make_carstate(left_blinker=True)
    self.dh.update(cs, lateral_active=True, lane_change_prob=1.0)
    assert self.dh.lane_change_state == LaneChangeState.preLaneChange

  def test_blocks_initiation_on_low_lane_confidence(self):
    self._enter_pre_lane_change()
    low_conf_model = _make_model_v2([0.05, 0.05, 0.05, 0.05])
    cs = _make_carstate(left_blinker=True, steering_pressed=True, steering_torque=1.0)
    self.dh.update(cs, lateral_active=True, lane_change_prob=1.0, model_v2=low_conf_model)
    assert self.dh.lane_change_state == LaneChangeState.preLaneChange

  def test_allows_initiation_on_high_lane_confidence(self):
    self._enter_pre_lane_change()
    high_conf_model = _make_model_v2([0.9, 0.95, 0.95, 0.9])
    cs = _make_carstate(left_blinker=True, steering_pressed=True, steering_torque=1.0)
    self.dh.update(cs, lateral_active=True, lane_change_prob=1.0, model_v2=high_conf_model)
    assert self.dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_missing_model_does_not_block_initiation(self):
    self._enter_pre_lane_change()
    cs = _make_carstate(left_blinker=True, steering_pressed=True, steering_torque=1.0)
    self.dh.update(cs, lateral_active=True, lane_change_prob=1.0, model_v2=None)
    assert self.dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_does_not_abort_in_progress_lane_change_on_low_confidence(self):
    self._enter_pre_lane_change()
    high_conf_model = _make_model_v2([0.9, 0.95, 0.95, 0.9])
    cs = _make_carstate(left_blinker=True, steering_pressed=True, steering_torque=1.0)
    self.dh.update(cs, lateral_active=True, lane_change_prob=1.0, model_v2=high_conf_model)
    assert self.dh.lane_change_state == LaneChangeState.laneChangeStarting

    # Confidence collapses mid-maneuver -- must not abort back to off/preLaneChange.
    low_conf_model = _make_model_v2([0.05, 0.05, 0.05, 0.05])
    cs2 = _make_carstate(left_blinker=True)
    self.dh.update(cs2, lateral_active=True, lane_change_prob=1.0, model_v2=low_conf_model)
    assert self.dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_threshold_matches_dlat_laneful_to_laneless(self):
    just_below = LANEFUL_TO_LANELESS_THRESH - 0.05
    just_above = LANEFUL_TO_LANELESS_THRESH + 0.05
    assert not self.dh._validate_lane_confidence(_make_model_v2([just_below] * 4))
    assert self.dh._validate_lane_confidence(_make_model_v2([just_above] * 4))
