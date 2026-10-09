"""Left/right sign rule, as sunnypilot / comma upstream (CLAUDE.md "Frame conventions").

modelV2 and paths built on it: y right positive. Every yRel: left positive.
radard flips once (yRel = -lead.y[0]); these tests pin the EOP code that has
to do the same.
"""
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock  # noqa: TID251

import numpy as np

_fake_params_pyx = MagicMock()
_fake_params_pyx.Params = MagicMock
_fake_params_pyx.ParamKeyFlag = MagicMock()
_fake_params_pyx.ParamKeyType = MagicMock()
_fake_params_pyx.UnknownKeyName = Exception
sys.modules.setdefault('openpilot.common.params_pyx', _fake_params_pyx)

_fake_msgq = MagicMock()
_fake_msgq.MAX_FDS = 64
sys.modules.setdefault('msgq.ipc_pyx', _fake_msgq)

from cereal import log  # noqa: E402
from openpilot.selfdrive.controls.lib.aeb import AEB  # noqa: E402
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper  # noqa: E402
from openpilot.selfdrive.controls.lib.lc_lead_handoff import LaneChangeLeadHandoff  # noqa: E402
from openpilot.selfdrive.pathd.lat_nudge import LatNudge  # noqa: E402
from openpilot.selfdrive.pathd.soc import SOC  # noqa: E402
from openpilot.selfdrive.pathd.track import TrackedCluster, _extract_clusters  # noqa: E402

LCD = log.LaneChangeDirection
MODEL_LEFT = -3.5  # a car one lane to the left, in modelV2's frame (y right positive)


def _model_with_lead(y: float, v: float = 10.0):
  lead = SimpleNamespace(prob=0.9, x=[20.0], y=[y], v=[v], a=[0.0])
  return SimpleNamespace(leadsV3=[lead])


def test_desire_gap_check_sees_model_lead_on_its_own_side():
  # Fast approaching car on the left blocks a left change, not a right one.
  model = _model_with_lead(MODEL_LEFT, v=0.0)
  assert DesireHelper._evaluate_gap(None, None, model, 'left', 20.0) == (False, 0.0)
  assert DesireHelper._evaluate_gap(None, None, model, 'right', 20.0) == (True, 1.0)


def test_desire_gap_check_radar_yrel_is_left_positive():
  lead = SimpleNamespace(status=True, dRel=20.0, yRel=3.5, vRel=-15.0)
  radar = SimpleNamespace(leadOne=lead, leadTwo=SimpleNamespace(status=False))
  assert DesireHelper._evaluate_gap(None, radar, None, 'left', 20.0) == (False, 0.0)
  assert DesireHelper._evaluate_gap(None, radar, None, 'right', 20.0) == (True, 1.0)


def test_lead_handoff_picks_left_lead_and_reports_left_positive_yrel():
  model = _model_with_lead(MODEL_LEFT)
  proxy = LaneChangeLeadHandoff._pick_adjacent_lead(model, LCD.left, 20.0)
  assert proxy is not None and proxy.yRel == -MODEL_LEFT
  assert LaneChangeLeadHandoff._pick_adjacent_lead(model, LCD.right, 20.0) is None


def test_aeb_keeps_radar_yrel_sign():
  lead = SimpleNamespace(status=True, radar=True, dRel=15.0, yRel=1.2, vRel=-3.0,
                         modelProb=0.9, radarTrackId=7)
  msgs = {'radarState': SimpleNamespace(leadOne=lead, leadTwo=SimpleNamespace(status=False, radar=False)),
          'carState': SimpleNamespace(vEgo=10.0)}

  class _SM:
    valid = {'radarState': True}

    def __getitem__(self, name):
      return msgs[name]

  objects = AEB.__new__(AEB)._collect_objects(_SM())
  assert objects[0].y == 1.2


def _track(y_rel: float, d: float = 20.0, vz: float = 0.0):
  return TrackedCluster(track_id=1, x=y_rel, z=d, vz=vz)


def test_lat_nudge_moves_away_from_left_obstacle():
  nudge = LatNudge()
  nudge.enabled = True
  left, right = [-1.8] * 7, [1.8] * 7  # path frame: left negative
  obstacle_left = _track(y_rel=1.0, d=20.0)  # yRel: left positive
  offsets = nudge._compute_offsets(left, right, [obstacle_left], 10.0)
  assert offsets[4] > 0  # 20 m slice: nudge right (path frame, right positive)
  offsets = nudge._compute_offsets(left, right, [_track(y_rel=-1.0, d=20.0)], 10.0)
  assert offsets[4] < 0


def test_soc_moves_away_from_left_threat():
  soc = SOC()
  result = soc._find_worst_threat([_track(y_rel=0.8, d=20.0, vz=0.0)], 25.0, -1.8, 1.8)
  assert result.offset_m > 0  # right, in the path's frame


def test_tracker_rows_are_forward_and_cols_lateral():
  grid = np.zeros((40, 11), dtype=np.float32)
  grid[30:32, 8:10] = 1.0  # rows 30-31 (15 m ahead), cols 8-9 (left of centre col 5)
  (x_m, z_m, _), = _extract_clusters(grid, 0.5, half_w=5)
  assert abs(z_m - 15.25) < 1e-6
  assert abs(x_m - 1.75) < 1e-6
