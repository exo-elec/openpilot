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
