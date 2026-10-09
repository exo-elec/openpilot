"""pathd's cores now live in nagaspilot/controls; pathd's behaviour must be what it was (EOP10 outputs recorded before the move)."""
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS

from openpilot.nagaspilot.daemons.pathd import pathd as P
from openpilot.nagaspilot.daemons.pathd.lat_nudge import LatNudge
from openpilot.nagaspilot.daemons.pathd.lon_nudge import LonNudge
from openpilot.nagaspilot.daemons.pathd.predict import predict

G = json.loads((Path(__file__).resolve().parents[4] / 'nagaspilot/tests/eop_golden.json').read_text())


def test_modules_are_the_shared_ones_and_reproduce_the_recorded_outputs():
  ln = LatNudge()
  ln.enabled = True
  for step in G['lat']['seq']:
    tk = [NS(dRel=d, yRel=y) for d, y in step['tracks']]
    assert all(abs(a - b) < 1e-9 for a, b in zip(ln.update(G['lat']['left'], G['lat']['right'], tk, G['lat']['v']), step['out'], strict=True))
  lo = LonNudge()
  lo.enabled = True
  for step in G['lon']:
    d, occ, bike, tk, v = step['args']
    assert abs(lo.update(d, occ, bike, [NS(dRel=a, yRel=b, vRel=c) for a, b, c in tk], v, 0.0) - step['out']) < 1e-9
  cl = [NS(track_id=t, x=x, z=z, vx=vx, vz=vz) for t, x, z, vx, vz in G['predict']['in']]
  assert [p.threat_level for p in predict(cl, G['predict']['v_ego'])] == [o[6] for o in G['predict']['out']]


def test_compute_speed_reduction_keeps_its_old_behaviour_by_default_and_the_fix_is_opt_in():
  P.set_scale_fix(False)
  for case in G['speed_reduction']:
    v = float('nan') if case['v'] is None else case['v']
    got = P.compute_speed_reduction([NS(trackId=t, dRel=d, yRel=y, vRel=vr, prob=p) for t, d, y, vr, p in case['objs']], None, v)
    assert (got == float('inf')) if case['inf'] else (got == case['out'] and got == 0.0)
  P.set_scale_fix(True)
  try:
    got = P.compute_speed_reduction([NS(trackId=1, dRel=30.0, yRel=0.5, vRel=-6.0, prob=0.9)], None, 25.0)
    assert math.isfinite(got) and got < -3.0
  finally:
    P.set_scale_fix(False)
