from types import SimpleNamespace as NS

from nagaspilot.runtime.cutin_adapter import cutin_objects


def det(**k):
  base = dict(trackId=3, x=22.0, y=3.0, vx=-6.0, vy=-1.5, sigmaX=1.0, confidence=0.9, className='motorcycle')
  base.update(k)
  return NS(**base)


class SM(dict):
  def __init__(self, alive, valid, dets):
    super().__init__({'monoDetections': NS(detections=dets)})
    self.alive, self.valid = {'monoDetections': alive}, {'monoDetections': valid}


def test_fresh_message_maps_fields():
  objs, fresh = cutin_objects(SM(True, True, [det()]))
  assert fresh and objs[0].track_id == 3 and objs[0].y == 3.0 and objs[0].conf == 0.9 and objs[0].name == 'motorcycle'


def test_not_alive_or_invalid_is_not_fresh_and_empty():
  assert cutin_objects(SM(False, True, [det()])) == ([], False)
  assert cutin_objects(SM(True, False, [det()])) == ([], False)


def test_cutin_path_flips_model_y_once():
  from nagaspilot.runtime.cutin_adapter import cutin_path
  sm = {'modelV2': NS(position=NS(x=[0.0, 20.0, 40.0], y=[0.0, 1.0, 2.0]))}
  sm = type('S', (dict,), {})(sm)
  sm.valid = {'modelV2': True}
  p = cutin_path(sm)
  assert p is not None and p.y_at(40.0) == -2.0          # model y-right +2 is 2 m to the RIGHT -> left-positive -2
  sm.valid = {'modelV2': False}
  assert cutin_path(sm) is None
