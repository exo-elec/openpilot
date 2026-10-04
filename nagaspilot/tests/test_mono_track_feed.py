from types import SimpleNamespace as NS

from nagaspilot.runtime.mono_track_feed import MonoTrackFeed


class Clock:
  def __init__(self):
    self.t = 0.0

  def __call__(self):
    return self.t


def det(x, y, name='car', conf=0.9, tid=1):
  return NS(trackId=tid, className=name, x=x, y=y, confidence=conf)


def test_velocity_comes_from_positions_and_objects_feed_the_cores():
  clk = Clock()
  f = MonoTrackFeed(clk)
  from nagaspilot.controls.ngp_cutin_speed import evaluate
  mid = None
  for i in range(30):
    clk.t += 0.1
    f.update([det(40.0 - 0.8 * i, 4.0 - 0.15 * i)], True)       # closing 8 m/s, drifting to the lane at 1.5 m/s
    if i == 12:
      mid = f.cutin_objects()[0]
  o = f.cutin_objects()[0]
  assert abs(o.vx + 8.0) < 1.0 and abs(o.vy + 1.5) < 0.6 and o.name == 'car' and o.conf > 0
  assert evaluate(25.0, mid) is not None          # the cut-in core sees the cut-in that raw EOP10 detections (vx=vy=0) hide
  assert f.path_objects()[0].name == 'car'


def test_filters_and_staleness():
  clk = Clock()
  f = MonoTrackFeed(clk)
  for _ in range(5):
    clk.t += 0.1
    f.update([det(30.0, 2.0, 'traffic light'), det(0.2, 1.0), det(30.0, 2.0, conf=0.0), det(200.0, 0.0)], True)
  assert f.tracks == []                            # light, too close, zero confidence, too far
  for _ in range(5):
    clk.t += 0.1
    f.update([det(30.0, 2.0)], True)
  assert len(f.tracks) == 1
  for _ in range(40):
    clk.t += 0.1
    f.update([], False)                            # stale stream: coast then drop
  assert f.tracks == []


def test_occluded_track_has_zero_confidence_for_the_cores():
  clk = Clock()
  f = MonoTrackFeed(clk)
  for _ in range(6):
    clk.t += 0.1
    f.update([det(30.0, 2.0)], True)
  clk.t += 0.1
  f.update([], True)
  assert f.tracks and f.cutin_objects()[0].conf == 0.0 and f.path_objects()[0].conf == 0.0
