from types import SimpleNamespace as NS

from nagaspilot.runtime.gridd import Gridd, detections_to_objects
from nagaspilot.runtime.object_sources import GriddSource


class Clock:
  t = 0.0

  def __call__(self):
    return self.t


class SO:
  def init(self, name, n):
    setattr(self, name, [NS() for _ in range(n)])
    return getattr(self, name)


class PM:
  def __init__(self):
    self.sent = []

  def send(self, name, msg):
    self.sent.append(msg)


def det(x, y, name='car', conf=0.9, src='road'):
  return NS(className=name, confidence=conf, x=x, y=y, trackId=0, cameraSource=src)


def sm_for(dets, valid=True, updated=True):
  sm = type('S', (dict,), {})({'monoDetections': NS(detections=dets)})
  sm.updated, sm.valid, sm.alive = {'monoDetections': updated}, {'monoDetections': valid}, {'monoDetections': valid}
  return sm


def make():
  clk = Clock()
  return Gridd(clk), clk, PM()


def tick(g, clk, pm, dets, **kw):
  clk.t += 0.05
  return g.tick(sm_for(dets, **kw), pm, new_message=lambda name, valid=True: NS(stereoObjects=SO(), valid=valid))


def test_publishes_only_confirmed_tracks_with_stable_ids_and_velocities():
  g, clk, pm = make()
  published = []
  for i in range(30):
    tick(g, clk, pm, [det(40.0 - 0.4 * i, 4.0 - 0.075 * i)])
    published.append(len(pm.sent[-1].stereoObjects.objects))
  assert published[0] == 0 and published[-1] == 1                          # nothing until the filter confirms the track
  o = pm.sent[-1].stereoObjects.objects[0]
  assert o.obstacleType == 'vehicle' and abs(o.vyRel + 1.5) < 0.6 and abs(o.vRel + 8.0) < 1.0 and o.trackId > 0
  ids = {m.stereoObjects.objects[0].trackId for m in pm.sent if m.stereoObjects.objects}
  assert len(ids) == 1


def test_class_mapping_filters_and_stale_monod_publishes_nothing():
  d = detections_to_objects([det(20.0, 0.0, 'motorcycle'), det(20.0, 0.0, 'person'), det(20.0, 0.0, 'traffic light'), det(20.0, 0.0, conf=0.0)])
  assert [o['obstacleType'] for o in d] == ['motorcycle', 'person']
  g, clk, pm = make()
  for _ in range(10):
    tick(g, clk, pm, [det(30.0, 0.0)])
  n = len(pm.sent)
  assert tick(g, clk, pm, [], valid=False) is False and len(pm.sent) == n              # a missing detector is not an empty road
  assert tick(g, clk, pm, [det(30.0, 0.0)], updated=False) is False


def test_pathd_reads_gridds_message_through_the_same_gridd_source():
  g, clk, pm = make()
  for i in range(25):
    tick(g, clk, pm, [det(35.0 - 0.3 * i, 3.0 - 0.07 * i, 'motorcycle')])
  fused = pm.sent[-1].stereoObjects
  fused_o = fused.objects[0]
  fused_o.obstacleType = 'motorcycle'                                                      # the enum a real message carries, as its name
  sm = type('S', (dict,), {})({'stereoObjects': fused})
  sm.alive, sm.valid = {'stereoObjects': True}, {'stereoObjects': True}
  objs, fresh = GriddSource().objects(sm)
  assert fresh and objs[0].name == 'motorcycle' and objs[0].vy < 0 and objs[0].track_id > 0
