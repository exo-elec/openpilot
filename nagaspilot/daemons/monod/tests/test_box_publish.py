"""monod publishes the detector's box (u, v, w, h) so logs can validate ranging against stereo."""
from openpilot.nagaspilot.daemons.monod.monod import MonoD


def test_box_fields_normalised_and_guards():
  f = MonoD._box_fields({'bbox': (100.0, 200.0, 300.0, 400.0), 'frame_hw': (720, 1280)})
  assert f is not None
  u, v, w, h = f
  assert abs(u - 200.0 / 1280) < 1e-9 and abs(v - 300.0 / 720) < 1e-9 and abs(w - 200.0 / 1280) < 1e-9 and abs(h - 200.0 / 720) < 1e-9
  assert MonoD._box_fields(None) is None
  assert MonoD._box_fields({'bbox': (1, 2, 3, 4)}) is None                       # no frame size
  assert MonoD._box_fields({'bbox': (10, 10, 5, 20), 'frame_hw': (720, 1280)}) is None   # empty box
  assert MonoD._box_fields({'bbox': (1, 2, 3, 4), 'frame_hw': (0, 0)}) is None


def test_ground_ranging_hook_off_by_default_and_applies_when_on():
    from types import SimpleNamespace as NS

    import numpy as np

    from nagaspilot.runtime.eop_monod_ranging import GroundRanging

    m = MonoD.__new__(MonoD)
    m.ground = None
    assert m._apply_ground_ranging([{'class': 'car'}]) == 0
    h, w = 1080, 1920
    focal = (w / 2.0) / np.tan(np.radians(40.0) / 2.0)
    m.ground = GroundRanging(focal, (h, w), 5.0, 120.0)
    m.ground.set_calibration([0.0, 0.0, 0.0], 1.22, lambda a, b, c: np.array([[0.0, 1, 0], [0, 0, 1], [1, 0, 0]]))
    m.sm = type('S', (dict,), {})({'liveCalibration': NS(rpyCalib=[0.0, 0.0, 0.0], height=[1.22])})
    m.sm.valid, m.sm.updated = {'liveCalibration': False, 'radarState': False}, {'liveCalibration': False}
    bottom = h / 2 + 1.22 * focal / 30.0
    det = [{'class': 'car', 'distance_m': 40.0, 'lateral_m': 0.0, 'bbox': (900.0, bottom - 1.5 * focal / 30.0, 1010.0, bottom)}]
    assert m._apply_ground_ranging(det) == 1 and abs(det[0]['distance_m'] - 30.0) < 1e-6 and det[0]['prior_distance_m'] == 40.0
