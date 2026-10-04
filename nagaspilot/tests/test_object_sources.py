from types import SimpleNamespace as NS

from nagaspilot.runtime.object_sources import GriddSource, MonoDetectionsSource


class SM(dict):
  def __init__(self, key, items, alive=True, valid=True):
    super().__init__({key: NS(detections=items, objects=items)})
    self.alive, self.valid = {key: alive}, {key: valid}


def g(obstacle='vehicle', prob=0.9, **kw):
  base = dict(trackId=7, obstacleType=obstacle, prob=prob, dRel=30.0, yRel=2.0, vRel=-4.0, vyRel=-1.0)
  base.update(kw)
  return NS(**base)


def test_gridd_source_maps_classes_velocity_and_filters():
  src = GriddSource()
  objs, fresh = src.objects(SM('stereoObjects', [g(), g('motorcycle', trackId=8), g('person', trackId=9), g('trafficLight'), g('bump'), g('vehicle', prob=0.2)]))
  assert fresh and [o.name for o in objs] == ['car', 'motorcycle', 'person']
  o = objs[0]
  assert o.track_id == 7 and o.x == 30.0 and o.y == 2.0 and o.vx == -4.0 and o.vy == -1.0 and o.conf == 0.9


def test_gridd_source_without_vyRel_defaults_to_zero_and_stale_is_empty():
  o = NS(trackId=1, obstacleType='vehicle', prob=0.9, dRel=20.0, yRel=0.0, vRel=0.0)
  objs, _ = GriddSource().objects(SM('stereoObjects', [o]))
  assert objs[0].vy == 0.0
  assert GriddSource().objects(SM('stereoObjects', [g()], alive=False)) == ([], False)
  assert GriddSource().objects(SM('stereoObjects', [g()], valid=False)) == ([], False)


def test_mono_source_passes_monod_tracks_through():
  d = NS(trackId=3, className='truck', x=25.0, y=-1.0, vx=-2.0, vy=0.3, confidence=0.8)
  objs, fresh = MonoDetectionsSource().objects(SM('monoDetections', [d]))
  assert fresh and objs[0].name == 'truck' and objs[0].vy == 0.3
