#!/usr/bin/env python3
"""Prove the vision ranging against an independent reference, offline, from a logged route.

The reference must NOT be the same camera model: on EOP10 it is stereo depth (`stereoObjects` with a depth
source other than `bboxCenter`) or a real radar lead (`radarState` lead with `.radar`). The detections are
EOP10's (or NGP10's) `monoDetections` WITH boxes (u, v, w, h normalised; EOP10's monod publishes them since this
change). Three estimates are compared with the reference for every detection:

  published : the `x` the source published (EOP10: class-height prior, no calibration)
  ngp       : NGP10's flat-ground range from the box bottom, horizon/vanishing point from `liveCalibration`
              (class-height fallback when the box is clipped or at the horizon), scale 1.0
  anchored  : same, with the per-class scale learned from the reference itself on earlier frames (the leads-anchored
              ranger; it only sees frames BEFORE the one being scored, so it is not graded on its own anchor)

Output is JSON with per-method error statistics (overall and by distance band and class) and acceptance gates.

  python3 -m nagaspilot.tools.validate_ranging <route> [<route> ...]
"""
import json
import math
import statistics
import sys

from nagaspilot.controls.ngp_ranging import LeadAnchoredRanger, Ranged, RoadCamera

IMG_W, IMG_H = 1928, 1208
BEARING_GATE_RAD = 0.05
RATIO_GATE = (0.5, 2.0)
BANDS = ((5, 20), (20, 40), (40, 80))
GATES = {'ngp_median_abs_rel_err': 0.08, 'ngp_p90_abs_rel_err': 0.20, 'min_samples': 30}
CLASS_NAMES = {'car', 'truck', 'bus', 'motorcycle', 'bicycle', 'person'}


def _pct(v, q):
  s = sorted(v)
  return s[min(len(s) - 1, int(q * len(s)))] if s else None


def _stats(errs: list[float]) -> dict:
  if not errs:
    return {'n': 0}
  a = [abs(e) for e in errs]
  return {'n': len(errs), 'bias': round(statistics.fmean(errs), 3), 'median_abs': round(statistics.median(a), 3),
          'p90_abs': round(_pct(a, 0.9), 3)}


def _reference(stereo_objs, radar_leads, depth_ok) -> list[tuple[float, float, str]]:
  """[(dRel, yRel_left, source)] from the independent sources only."""
  ref = [(float(o.dRel), float(o.yRel), 'stereo') for o in stereo_objs if depth_ok(o) and o.dRel > 1.0]
  ref += [(float(d), float(y), 'radar') for d, y in radar_leads if d > 1.0]
  return ref


def _match(x: float, y: float, ref):
  best = None
  for d, ry, src in ref:
    ratio = d / x if x > 0 else 0.0
    err = abs(math.atan2(y, x) - math.atan2(ry, d))
    if err <= BEARING_GATE_RAD and RATIO_GATE[0] <= ratio <= RATIO_GATE[1] and (best is None or err < best[0]):
      best = (err, d, src)
  return best


def analyze(msgs, view_from_calib_fn, depth_ok=lambda o: str(getattr(o, 'depthSource', 'unknown')) != 'bboxCenter',
            cam: RoadCamera | None = None) -> dict:
  """`msgs`: log messages. `view_from_calib_fn(roll, pitch, yaw) -> 3x3`: calibrated frame to camera view frame."""
  from nagaspilot.runtime.monod import vanishing_point
  import numpy as np

  cam = cam or RoadCamera()
  K = np.array([[cam.focal, 0, cam.cx], [0, cam.focal, cam.cy], [0, 0, 1.0]])
  ranger = LeadAnchoredRanger(cam)
  vfc = None
  stereo, radar = [], []
  errs = {'published': [], 'ngp': [], 'anchored': []}
  by_band: dict[str, dict[str, list[float]]] = {}
  by_class: dict[str, list[float]] = {}
  frames = no_box = unmatched = 0

  for m in msgs:
    w = m.which()
    if w == 'liveCalibration':
      rpy = list(m.liveCalibration.rpyCalib)
      if len(rpy) == 3:
        vfc = np.asarray(view_from_calib_fn(*rpy), dtype=float)
        h = list(m.liveCalibration.height)
        if h:
          ranger.cam = RoadCamera(cam.focal, cam.cx, cam.cy, float(h[0]))
    elif w == 'stereoObjects':
      stereo = list(m.stereoObjects.objects)
    elif w == 'radarState':
      radar = [(float(ld.dRel), float(ld.yRel)) for ld in (m.radarState.leadOne, m.radarState.leadTwo) if ld.status and getattr(ld, 'radar', False)]
    elif w == 'monoDetections' and vfc is not None:
      frames += 1
      vp_u, horizon_v = vanishing_point(vfc, K)
      ref = _reference(stereo, radar, depth_ok)
      scored = []
      for d in m.monoDetections.detections:
        if d.className not in CLASS_NAMES or d.confidence <= 0:
          continue
        if d.w <= 0 or d.h <= 0:
          no_box += 1
          continue
        cx, bottom, bh = d.u * IMG_W, (d.v + d.h / 2) * IMG_H, d.h * IMG_H
        est = ranger.range_box(d.className, cx, bottom, bh, horizon_v, vp_u, clipped_bottom=bottom >= IMG_H - 2)
        raw = Ranged(est.raw_x, est.y, est.source, est.raw_x) if est else None
        mt = _match(d.x, d.y, ref)
        if mt is None or raw is None:
          unmatched += 1
          continue
        truth = mt[1]
        k = ranger.scale(d.className)
        errs['published'].append((d.x - truth) / truth)
        errs['ngp'].append((raw.x - truth) / truth)
        errs['anchored'].append((raw.x * k - truth) / truth)
        band = next((f'{lo}-{hi}m' for lo, hi in BANDS if lo <= truth < hi), 'other')
        by_band.setdefault(band, {'published': [], 'ngp': [], 'anchored': []})
        by_band[band]['published'].append((d.x - truth) / truth)
        by_band[band]['ngp'].append((raw.x - truth) / truth)
        by_band[band]['anchored'].append((raw.x * k - truth) / truth)
        by_class.setdefault(d.className, []).append((raw.x - truth) / truth)
        scored.append((d.className, raw, truth))
      # learn the anchors from this frame AFTER scoring it (the next frames are graded with it)
      ranger.observe_leads([(n, r) for n, r, _ in scored], [(t, r.y) for _, r, t in scored])

  res = {
    'frames': frames, 'detections_without_box': no_box, 'unmatched': unmatched,
    'overall': {k: _stats(v) for k, v in errs.items()},
    'by_band': {b: {k: _stats(v) for k, v in d.items()} for b, d in sorted(by_band.items())},
    'ngp_by_class': {c: _stats(v) for c, v in sorted(by_class.items())},
    'learned_scale': {c: round(ranger.k_cls[c], 3) for c in ranger.k_cls},
    'caveat': 'a reference from the same camera model proves nothing: use stereo/radar logs',
  }
  n = res['overall']['ngp'].get('n', 0)
  ng, pub = res['overall']['ngp'], res['overall']['published']
  res['gates'] = {
    'enough_samples': n >= GATES['min_samples'],
    'ngp_median_ok': n > 0 and ng['median_abs'] <= GATES['ngp_median_abs_rel_err'],
    'ngp_p90_ok': n > 0 and ng['p90_abs'] <= GATES['ngp_p90_abs_rel_err'],
    'beats_published_p90': n > 0 and pub.get('n', 0) > 0 and ng['p90_abs'] <= pub['p90_abs'],
  }
  res['pass'] = all(res['gates'].values())
  return res


def main(argv: list[str]) -> int:
  from openpilot.common.transformations.camera import get_view_frame_from_calib_frame
  from openpilot.tools.lib.logreader import LogReader

  ok = True
  for route in argv:
    r = analyze(LogReader(route), lambda a, b, c: get_view_frame_from_calib_frame(a, b, c, 0.0)[:, :3])
    print(route)
    print(json.dumps(r, indent=2))
    ok &= r['pass']
  return 0 if ok else 1


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
