import math

from openpilot.selfdrive.controls.lib.radar4d_points import VehiclePoint
from openpilot.selfdrive.controls.radar4d import Radar4DD


class _PM:
  def __init__(self):
    self.sent = []

  def send(self, name, msg):
    self.sent.append((name, msg))


def test_publish_fills_radar4d_points():
  d = Radar4DD.__new__(Radar4DD)   # skip __init__: no sockets, no hal
  d.pm = _PM()
  pts = [VehiclePoint(corner=0, range_m=4.2, azimuth_deg=30.0, elevation_deg=2.0, v_rel=-1.5,
                      snr_db=18.0, is_static=False, x_m=3.6, y_m=2.1),
         VehiclePoint(corner=1, range_m=6.0, azimuth_deg=-40.0, elevation_deg=0.0, v_rel=0.0,
                      snr_db=9.0, is_static=True, x_m=4.6, y_m=-3.9)]
  d._publish(pts, fresh=True)
  (name, msg), = d.pm.sent
  assert name == 'radar4d' and msg.valid
  out = msg.radar4d.points
  assert len(out) == 2
  assert math.isclose(out[0].rangM, 4.2, rel_tol=1e-6) and math.isclose(out[0].azimuth, 30.0, rel_tol=1e-6)
  assert math.isclose(out[0].vRel, -1.5, rel_tol=1e-6) and out[0].dynProp == 1 and not out[0].isStatic
  assert out[1].isStatic and out[1].dynProp == 0
  assert out[0].trackId == 0 and out[0].existenceProb == 0.0 and math.isnan(out[0].aRel)


def test_publish_empty_is_invalid_when_no_corner_is_fresh():
  d = Radar4DD.__new__(Radar4DD)
  d.pm = _PM()
  d._publish([], fresh=False)
  (_, msg), = d.pm.sent
  assert not msg.valid and len(msg.radar4d.points) == 0


def test_ble_tracks_publish_under_surround_and_flatten_only_in_compatibility_view():
  import cereal.messaging as messaging
  from types import SimpleNamespace as NS
  from openpilot.nagaspilot.daemons.radar4d.surround_tracks import surround_tracks
  d = Radar4DD.__new__(Radar4DD)
  d.pm = _PM()
  raw = messaging.new_message('radarCornerTracks').radarCornerTracks
  raw.init('returns', 4)
  for side, entry in enumerate(raw.returns):
    entry.side = side
  raw.objects = [dict(trackId=7, corner=0, rangM=10, azimuthDeg=0, elevationDeg=60, vRel=-2, snrDb=20)]
  tracks = surround_tracks(raw.objects, {0: (0, 0, 0)})
  d._publish([], True, tracks=tracks)
  _, message = d.pm.sent.pop()
  assert len(message.radar4d.objects) == 1
  assert message.radar4d.objects[0].elevation == 60
  assert message.radar4d.objects[0].pointCount == 0
  assert list(message.radar4d.bleCorners) == [0]
  class SM(dict):
    valid = {'carState': True}
    recv_time = {'carState': 1.0}
  d.sm = SM(carState=NS(leftBlindspot=True, rightBlindspot=False))
  d._publish_planar(raw.as_reader(), 1.0)
  name, message = d.pm.sent.pop()
  assert name == 'radar2d' and message.valid
  assert math.isclose(message.radar2d.objects[0].rangM, 5, rel_tol=1e-6)
  assert message.radar2d.objects[0].elevationDeg == 0
  assert raw.objects[0].elevationDeg == 60
  assert message.radar2d.returns[0].present


def test_missing_or_stale_inputs_publish_no_synthetic_objects():
  d = Radar4DD.__new__(Radar4DD)
  d.pm = _PM()
  class SM(dict):
    valid = {'carState': False}
    recv_time = {'carState': 0.0}
  d.sm = SM()
  d._publish_planar(None, 1.0)
  _, message = d.pm.sent.pop()
  assert not message.valid
  assert not message.radar2d.objects
  assert not any(entry.present for entry in message.radar2d.returns)
