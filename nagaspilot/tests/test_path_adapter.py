from types import SimpleNamespace as NS

from nagaspilot.runtime.path_adapter import lane_room, pobjects


def model(left_y=-1.8, right_y=1.8, pl=0.9, pr=0.9):
  mk = lambda y: NS(y=[y] * 33)
  return NS(laneLines=[mk(-3.6), mk(left_y), mk(right_y), mk(3.6)], laneLineProbs=[0.9, pl, pr, 0.9])


def test_lane_room_from_model_lines_y_right_convention():
  l, r = lane_room(model())
  assert abs(l - (1.8 - 0.95 - 0.15)) < 1e-9 and abs(r - (1.8 - 0.95 - 0.15)) < 1e-9
  l, r = lane_room(model(left_y=-1.2, right_y=2.4))     # we sit closer to the left line
  assert l < r and abs(l - 0.1) < 1e-9


def test_unknown_or_missing_lines_give_no_room():
  assert lane_room(model(pl=0.2)) == (0.0, lane_room(model())[1])
  assert lane_room(NS()) == (0.0, 0.0)
  assert lane_room(model(left_y=+1.0)) [0] == 0.0       # nonsense sign: no room


class SM(dict):
  def __init__(self, alive, valid, dets):
    super().__init__({'monoDetections': NS(detections=dets)})
    self.alive, self.valid = {'monoDetections': alive}, {'monoDetections': valid}


def test_pobjects_fresh_and_stale():
  d = NS(trackId=4, className='truck', x=10.0, y=-2.0, vx=0.0, vy=0.0, confidence=0.9)
  objs, fresh = pobjects(SM(True, True, [d]))
  assert fresh and objs[0].name == 'truck' and objs[0].y == -2.0
  assert pobjects(SM(False, True, [d])) == ([], False)
