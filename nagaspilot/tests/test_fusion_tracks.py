from nagaspilot.runtime.fusion_tracks import CameraTrackAnnotator


class Clock:
  t = 0.0

  def __call__(self):
    return self.t


def obj(x, y, name='car', src='road', v=0.0, tid=1):
  d = {'dRel': x, 'yRel': y, 'obstacleType': name, 'confidence': 0.9, 'trackId': tid, 'vRel': v}
  if src:
    d['source'] = src
  return d


def test_lateral_and_relative_speed_are_written_back_for_camera_objects():
  clk, a = Clock(), CameraTrackAnnotator(Clock())
  a._clock = clk
  last = None
  for i in range(30):
    clk.t += 0.05
    last = [obj(40.0 - 0.4 * i, 4.0 - 0.075 * i)]            # closing 8 m/s, drifting toward the lane at 1.5 m/s
    a.annotate(last)
  o = last[0]
  assert abs(o['vRel'] + 8.0) < 1.0 and abs(o['vyRel'] + 1.5) < 0.6


def test_radar_objects_and_existing_doppler_are_left_alone():
  clk, a = Clock(), CameraTrackAnnotator(Clock())
  a._clock = clk
  for i in range(30):
    clk.t += 0.05
    radar = obj(40.0 - 0.4 * i, 0.0, src=None, v=-7.5)       # no 'source': not a camera object
    cam = obj(40.0 - 0.4 * i, 3.0, v=-6.0)                   # gridd already has a speed for it
    a.annotate([radar, cam])
  assert 'vyRel' not in radar and radar['vRel'] == -7.5
  assert cam['vRel'] == -6.0 and 'vyRel' in cam


def test_unconfirmed_unknown_class_and_out_of_range_get_nothing():
  clk, a = Clock(), CameraTrackAnnotator(Clock())
  a._clock = clk
  clk.t += 0.05
  one = [obj(30.0, 1.0), obj(30.0, 1.0, name='traffic light'), obj(300.0, 0.0)]
  assert a.annotate(one) == 0 and all('vyRel' not in o for o in one)        # a track is confirmed only after 3 hits


def test_tracker_ids_are_assigned_only_where_the_source_has_none_and_stay_stable():
  clk, a = Clock(), CameraTrackAnnotator(Clock())
  a._clock = clk
  ids = set()
  for i in range(30):
    clk.t += 0.05
    o = obj(40.0 - 0.4 * i, 2.0, tid=0)
    keep = obj(25.0, -2.0, name='truck', tid=77)
    a.annotate([o, keep])
    if 'vyRel' in o:
      ids.add(o['trackId'])
  assert len(ids) == 1 and next(iter(ids)) > 0 and keep['trackId'] == 77
