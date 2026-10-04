from types import SimpleNamespace as NS

from nagaspilot.runtime.pathd import PathD, fill_path_adjust


class SM(dict):
  def __init__(self, dets, alive=True):
    super().__init__({'monoDetections': NS(detections=dets),
                      'modelV2': NS(position=NS(x=[0.0, 40.0], y=[0.0, 0.0]),
                                    laneLines=[NS(y=[-3.6] * 33), NS(y=[-1.9] * 33), NS(y=[1.9] * 33), NS(y=[3.6] * 33)],
                                    laneLineProbs=[0.9] * 4)})
    self.alive, self.valid = {'monoDetections': alive}, {'monoDetections': True, 'modelV2': True}


def det(y=-2.6):
  return NS(trackId=1, className='truck', x=0.0, y=y, vx=0.0, vy=0.0, confidence=0.9)


class Builder:
  def init(self, name, n):
    setattr(self, name, [0.0] * n)
    return getattr(self, name)


def test_step_nudges_away_from_a_truck_and_message_fields():
  d = PathD()
  sel, room, n = d.step(SM([det()]), 25.0)
  assert n == 1 and sel.offset_m > 0 and room[0] > 0
  pa = Builder()
  fill_path_adjust(pa, sel, room, n, 12, 25.0)
  assert len(pa.offsetProfile) == len(pa.speedCapProfile) == 12 and pa.horizonDt == 0.25 and max(pa.speedCapProfile) <= 25.0 and pa.offsetProfile[-1] > 0
  assert pa.frameId == 12 and pa.offsetM > 0 and pa.speedFactor <= 1.0 and pa.reason in ('nudge', 'slow') and pa.numObjects == 1


def test_stale_detections_mean_no_action_and_nan_clearance():
  d = PathD()
  sel, room, n = d.step(SM([det()], alive=False), 25.0)
  assert sel.offset_m == 0.0 and sel.speed_factor == 1.0 and n == 0
  pa = Builder()
  fill_path_adjust(pa, sel, room, n, 1)
  assert pa.minClearanceM != pa.minClearanceM          # NaN
