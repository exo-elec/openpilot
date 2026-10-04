from types import SimpleNamespace as NS

import numpy as np

from nagaspilot.tools.validate_ranging import IMG_H, IMG_W, analyze

F, CX, CY = 2648.0, 964.0, 604.0
VIEW = np.array([[0.0, 1, 0], [0, 0, 1], [1, 0, 0]])


class Msg:
  def __init__(self, which, **fields):
    self._w = which
    setattr(self, which, NS(**fields))

  def which(self):
    return self._w


def det(dist, y=0.0, name='car', prior_err=0.0, h_m=1.5, tid=1):
  bottom = CY + 1.22 * F / dist
  hpx = h_m * F / dist
  cx = CX - y * F / dist                      # left-positive y -> pixel left of the vanishing column
  return NS(className=name, confidence=0.9, x=dist * (1 + prior_err), y=y, trackId=tid,
            u=cx / IMG_W, v=(bottom - hpx / 2) / IMG_H, w=120 / IMG_W, h=hpx / IMG_H)


def log(dists, prior_err=0.15, cal_height=1.22, ref_source='stereoTriangulation', with_box=True):
  msgs = [Msg('liveCalibration', rpyCalib=[0.0, 0.0, 0.0], height=[cal_height])]
  for i, d in enumerate(dists):
    dd = det(d, y=2.0, prior_err=prior_err)
    if not with_box:
      dd.w = dd.h = 0.0
    msgs.append(Msg('stereoObjects', objects=[NS(dRel=d, yRel=2.0, depthSource=ref_source)]))
    msgs.append(Msg('monoDetections', detections=[dd]))
  return msgs


def run(msgs, **kw):
  return analyze(msgs, lambda a, b, c: VIEW, **kw)


def test_ngp_ranging_beats_the_published_prior_against_stereo_and_gates_pass():
  r = run(log([15.0 + 0.5 * i for i in range(80)]))
  assert r['overall']['ngp']['median_abs'] < 0.01 and abs(r['overall']['published']['median_abs'] - 0.15) < 0.01
  assert r['gates']['beats_published_p90'] and r['pass']
  assert set(r['by_band']) == {'5-20m', '20-40m', '40-80m'} or set(r['by_band']) <= {'5-20m', '20-40m', '40-80m'}
  assert r['ngp_by_class']['car']['n'] > 70


def test_anchoring_learns_a_wrong_camera_height_from_the_reference():
  r = run(log([20.0 + 0.4 * i for i in range(100)], cal_height=1.22 * 0.85))
  assert r['overall']['ngp']['median_abs'] > 0.1                      # unanchored geometry is 15 % short
  assert r['overall']['anchored']['median_abs'] < 0.03                # anchored on earlier frames of the reference
  assert 1.1 < r['learned_scale']['car'] < 1.25
  assert not r['pass']                                                # the unanchored estimate fails its gate: the gate is real


def test_camera_model_reference_is_excluded_and_boxless_detections_counted():
  r = run(log([30.0] * 40, ref_source='bboxCenter'))
  assert r['overall']['ngp'] == {'n': 0} and not r['pass'] and not r['gates']['enough_samples']
  r = run(log([30.0] * 40, with_box=False))
  assert r['detections_without_box'] == 40 and r['overall']['ngp'] == {'n': 0}


def test_radar_lead_is_a_valid_reference_and_vision_only_leads_are_not():
  msgs = [Msg('liveCalibration', rpyCalib=[0.0, 0.0, 0.0], height=[1.22])]
  for _ in range(40):
    msgs.append(Msg('radarState', leadOne=NS(status=True, radar=True, dRel=30.0, yRel=2.0), leadTwo=NS(status=False, radar=False, dRel=0.0, yRel=0.0)))
    msgs.append(Msg('monoDetections', detections=[det(30.0, y=2.0, prior_err=0.1)]))
  assert run(msgs)['overall']['ngp']['n'] == 40
  msgs = [Msg('liveCalibration', rpyCalib=[0.0, 0.0, 0.0], height=[1.22])]
  for _ in range(40):
    msgs.append(Msg('radarState', leadOne=NS(status=True, radar=False, dRel=30.0, yRel=2.0), leadTwo=NS(status=False, radar=False, dRel=0.0, yRel=0.0)))
    msgs.append(Msg('monoDetections', detections=[det(30.0, y=2.0)]))
  assert run(msgs)['overall']['ngp'] == {'n': 0}                       # a vision lead is not independent
