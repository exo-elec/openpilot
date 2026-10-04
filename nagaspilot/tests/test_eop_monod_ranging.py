import numpy as np

from nagaspilot.runtime.eop_monod_ranging import GroundRanging, radar_leads

VIEW = np.array([[0.0, 1, 0], [0, 0, 1], [1, 0, 0]])
H, W = 1080, 1920
F = (W / 2.0) / np.tan(np.radians(40.0) / 2.0)             # EOP10's road lens: 40 deg HFOV


def box(dist, y=0.0, h_m=1.5, w_px=110.0, height=1.22):
  bottom = H / 2.0 + height * F / dist
  hpx = h_m * F / dist
  cx = W / 2.0 - y * F / dist
  return (cx - w_px / 2, bottom - hpx, cx + w_px / 2, bottom)


def dets(dist, y=0.0, prior_err=0.2):
  return [{'class': 'car', 'confidence': 0.9, 'distance_m': dist * (1 + prior_err), 'lateral_m': y, 'bbox': box(dist, y)}]


def ranging(**kw):
  g = GroundRanging(F, (H, W), 5.0, 120.0)
  g.set_calibration([0.0, 0.0, 0.0], kw.get('height', 1.22), lambda a, b, c: VIEW)
  return g


def test_replaces_the_class_prior_with_ground_ranging_and_keeps_the_prior_for_logging():
  g = ranging()
  d = dets(35.0, y=2.0, prior_err=0.25)
  assert g.apply(d) == 1
  assert abs(d[0]['distance_m'] - 35.0) < 1e-6 and abs(d[0]['lateral_m'] - 2.0) < 1e-6
  assert abs(d[0]['prior_distance_m'] - 35.0 * 1.25) < 1e-6 and d[0]['range_source'] == 'ground'


def test_untouched_without_calibration_unknown_class_no_box_or_out_of_range():
  d = dets(35.0)
  assert GroundRanging(F, (H, W), 5.0, 120.0).apply(d) == 0 and d[0]['distance_m'] == 35.0 * 1.2        # no calibration yet
  g = ranging()
  x = [{'class': 'traffic light', 'bbox': box(35.0), 'distance_m': 35.0}, {'class': 'car', 'distance_m': 20.0}, {'class': 'car', 'bbox': box(300.0), 'distance_m': 300.0}]
  assert g.apply(x) == 0 and x[1]['distance_m'] == 20.0 and x[2]['distance_m'] == 300.0


def test_radar_anchor_corrects_a_wrong_camera_height_and_clipped_box_uses_the_height_prior():
  g = ranging(height=1.22 * 0.85)                                      # calibration height 15 % low: ranges read 15 % short
  for _ in range(60):
    d = dets(40.0)
    g.apply(d, [(40.0, 0.0)])
  d = dets(40.0)
  g.apply(d, [(40.0, 0.0)])
  assert abs(d[0]['distance_m'] - 40.0) < 1.0
  clipped = [{'class': 'car', 'bbox': (900.0, 700.0, 1010.0, float(H)), 'distance_m': 99.0}]
  g2 = ranging()
  g2.apply(clipped)
  assert clipped[0]['range_source'] == 'height' and abs(clipped[0]['distance_m'] - 1.5 * F / 380.0) < 1e-6


def test_radar_leads_only_from_real_radar():
  from types import SimpleNamespace as NS
  rs = NS(leadOne=NS(status=True, radar=True, dRel=30.0, yRel=1.0), leadTwo=NS(status=True, radar=False, dRel=50.0, yRel=0.0))
  sm = type('S', (dict,), {})({'radarState': rs})
  sm.valid = {'radarState': True}
  assert radar_leads(sm) == [(30.0, 1.0)]
  sm.valid = {'radarState': False}
  assert radar_leads(sm) == []
