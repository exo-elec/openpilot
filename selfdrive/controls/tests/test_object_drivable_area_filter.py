"""Advisory off-road check on detected objects (Autoware's detected object
validation, gridd's drivable layer standing in for lanelets)."""
import numpy as np

import cereal.messaging as messaging
from openpilot.selfdrive.controls.lib.object_drivable_area_filter import (
  CONFIDENCE_FACTOR, MAX_AGE_S, ObjectDrivableAreaFilter)
from openpilot.selfdrive.controls.lib.radar_zones import RadarZoneMonitor

ROWS, COLS, RES, ORIGIN_FORWARD, ORIGIN_LEFT = 150, 60, 0.5, -20.0, -15.0


def _grid_msg(p: np.ndarray | None):
  msg = messaging.new_message('gridObjects')
  g = msg.gridObjects
  g.resolution, g.width, g.height, g.originX, g.originY = RES, COLS, ROWS, ORIGIN_LEFT, ORIGIN_FORWARD
  if p is None:
    g.init('layers', 1)[0].name = 'occupancy'
  else:
    layer = g.init('layers', 1)[0]
    layer.name, layer.scale = 'drivable', 1.0 / 255.0
    layer.data = (p * 255).astype(np.uint8).tobytes()
  return g


def _road_with_verge(verge_left_of_m=5.0):
  """Road (p 0.9) from y = -verge..+verge; not road (p 0.05) beyond; y left positive."""
  p = np.full((ROWS, COLS), 0.9, dtype=np.float32)
  for c in range(COLS):
    y = ORIGIN_LEFT + (c + 0.5) * RES
    if abs(y) > verge_left_of_m:
      p[:, c] = 0.05
  return p


def _obj(d, y, conf=0.6, **kw):
  return {'dRel': d, 'yRel': y, 'confidence': conf, 'prob': conf, **kw}


def _filter(p, t=0.0):
  f = ObjectDrivableAreaFilter()
  f.update_grid(_grid_msg(p), t)
  return f


def test_object_on_the_verge_is_downweighted_not_deleted():
  f = _filter(_road_with_verge())
  objs = f.apply([_obj(-5.0, 8.0), _obj(-5.0, 3.0)], 0.1)   # 8 m left = verge; 3 m left = road
  assert len(objs) == 2
  assert objs[0]['confidence'] == 0.6 * CONFIDENCE_FACTOR and objs[0]['offRoad']
  assert objs[1]['confidence'] == 0.6 and 'offRoad' not in objs[1]


def test_left_and_right_are_not_mirrored():
  p = _road_with_verge()
  p[:, :COLS // 2] = 0.9            # right half (low y) is road
  p[:, COLS // 2:] = 0.05           # left half (positive y) is not road
  f = _filter(p)
  assert f.off_road(_obj(10.0, 4.0)) and not f.off_road(_obj(10.0, -4.0))


def test_unknown_cells_mean_no_filtering():
  assert not _filter(np.full((ROWS, COLS), 0.5, dtype=np.float32)).off_road(_obj(10.0, 8.0))


def test_partly_known_footprint_is_not_enough_evidence():
  p = np.full((ROWS, COLS), 0.5, dtype=np.float32)
  r, c = int((10.0 - ORIGIN_FORWARD) / RES), int((8.0 - ORIGIN_LEFT) / RES)
  p[r, c] = 0.05                    # one known cell under a 4.5 x 1.8 m car
  assert not _filter(p).off_road(_obj(10.0, 8.0))


def test_no_layer_stale_layer_or_outside_grid_mean_no_filtering():
  assert _filter(None).apply([_obj(-5.0, 8.0)], 0.1)[0]['confidence'] == 0.6
  f = _filter(_road_with_verge())
  assert f.apply([_obj(-5.0, 8.0)], MAX_AGE_S + 0.1)[0]['confidence'] == 0.6
  assert not f.off_road(_obj(90.0, 8.0)) and not f.off_road(_obj(-5.0, 40.0))


def test_footprint_follows_class():
  p = np.full((ROWS, COLS), 0.9, dtype=np.float32)
  r, c = int((10.0 - ORIGIN_FORWARD) / RES), int((6.0 - ORIGIN_LEFT) / RES)
  p[r - 2:r + 3, c:c + 2] = 0.05    # a 2.5 x 1 m patch of verge
  f = _filter(p)
  assert f.off_road(_obj(10.0, 6.5, className='person'))      # person fits inside it
  assert not f.off_road(_obj(10.0, 6.5, className='truck'))   # a truck mostly does not


class _Carstate:
  leftBlindspot = False
  rightBlindspot = False
  vEgo = 25.0
  gearShifter = 0


def test_zone_monitor_applies_it_but_the_cars_own_blindspot_is_untouched():
  zm = RadarZoneMonitor()
  zm.cache_drivable(_grid_msg(_road_with_verge(2.0)), 0.0)         # road only +-2 m: adjacent lane is "verge"
  objs = zm.drivable_filter.apply([_obj(-3.0, 3.0, conf=0.5)], 0.1)
  assert objs[0]['confidence'] < 0.5
  cs = _Carstate()
  cs.leftBlindspot = True
  left, _, _ = zm.update(None, cs, None, None, 0.1)
  assert left.detected                                               # native BSM always wins


def test_aeb_is_not_fed_by_the_filter():
  import inspect
  from openpilot.selfdrive.controls.lib import aeb
  src = inspect.getsource(aeb)
  assert 'drivable' not in src.lower() and 'offRoad' not in src
