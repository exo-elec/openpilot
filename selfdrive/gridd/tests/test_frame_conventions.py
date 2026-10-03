"""gridd's inputs arrive in two lateral conventions; stereoObjects uses one.

modelV2 is openpilot's device frame (y positive right -- upstream ldw.py and
radard rely on it). monod's lateral_m and every stereoObjects yRel are
positive left. These pin down the conversions at ingestion so the ego lane
and the object's side cannot silently flip again.
"""
from types import SimpleNamespace as NS

from openpilot.selfdrive.gridd.gridd import GridD, lane_cache_from_model

XS = [0.0, 10.0, 20.0, 40.0, 60.0]


def _line(y):
  return NS(x=XS, y=[y] * len(XS))


def _model(probs=(0.8, 0.9, 0.9, 0.8)):
  # Model frame: left lines negative, right lines positive.
  return NS(laneLines=[_line(-5.4), _line(-1.8), _line(1.8), _line(5.4)],
            laneLineProbs=list(probs), roadEdges=[_line(-7.0), _line(7.0)])


def _host(cache):
  g = GridD.__new__(GridD)
  g._lane_cache = cache
  return g


class TestLaneCache:
  def test_ego_lane_bounds_are_right_then_left_in_left_positive_frame(self):
    assert _host(lane_cache_from_model(_model()))._ego_lane_bounds(20.0) == (-1.8, 1.8)

  def test_every_zone_lands_on_its_own_side(self):
    g = _host(lane_cache_from_model(_model()))
    expect = {
      0.0: GridD._LANE_ZONE_EGO,
      3.6: GridD._LANE_ZONE_ADJ_LEFT,
      -3.6: GridD._LANE_ZONE_ADJ_RIGHT,
      5.8: GridD._LANE_ZONE_FAR_LEFT,
      -5.8: GridD._LANE_ZONE_FAR_RIGHT,
      8.0: GridD._LANE_ZONE_SHOULDER_LEFT,
      -8.0: GridD._LANE_ZONE_SHOULDER_RIGHT,
    }
    for y_rel, zone in expect.items():
      assert g._classify_lane_zone(20.0, y_rel) == zone, y_rel

  def test_unconfident_near_lanes_give_no_cache(self):
    assert lane_cache_from_model(_model(probs=(0.8, 0.2, 0.9, 0.8))) is None

  def test_far_lines_dropped_when_unconfident(self):
    cache = lane_cache_from_model(_model(probs=(0.1, 0.9, 0.9, 0.1)))
    assert cache['far_left_y'] is None and cache['far_right_y'] is None


class TestMonoIngestion:
  def _det(self, y, x=20.0):
    return NS(x=x, y=y, z=0.0, className='car', confidence=0.9, trackId=1,
              cameraSource='road', width=1.8, height=1.5)

  def test_monod_left_stays_left(self):
    g = GridD.__new__(GridD)
    objs = g._fuse_mono_detections(NS(detections=[self._det(y=2.0)]), None)
    assert objs[0]['yRel'] == 2.0

  def test_same_car_from_monod_and_modeld_is_one_object(self):
    """monod +left 1.0 and a modelV2 lead at model-frame y=-1.0 are the
    same car; with the old mirrored monod y they were 2 m apart and never
    de-duplicated."""
    g = GridD.__new__(GridD)
    mono = g._fuse_mono_detections(NS(detections=[self._det(y=1.0)]), None)
    lead = NS(status=True, x=[20.0], y=[-1.0], v=[0.0], prob=0.95)
    model = g._fuse_modeld_detections(NS(leads=[lead]), None)
    assert mono[0]['yRel'] == model[0]['yRel'] == 1.0
    assert len(g._merge_detections(mono, model)) == 1
