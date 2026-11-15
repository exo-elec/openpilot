"""EOP10's VTSC wiring (shared VTSC + Params-backed ComfortCalibration + learned speeds) vs EOP10's pre-refactor vtsc.py."""
import random
from types import SimpleNamespace as NS

import numpy as np
import pytest

from exopilot.selfdrive.controls.lib import vtsc as new
from exopilot.tests import vtsc_reference as ref

KEYS = {new.CurvatureBucket.MILD: 'TSCTargetLatAccelMild', new.CurvatureBucket.MEDIUM: 'TSCTargetLatAccelMedium',
        new.CurvatureBucket.SHARP: 'TSCTargetLatAccelSharp'}


def fake_db(lat, lon, kappa, enabled, min_speed_kph=15.0):
  return 12.0 if kappa > 0.004 else 0.0


@pytest.mark.parametrize("learn", [False, True])
def test_shared_vtsc_matches_the_old_eop10_vtsc(monkeypatch, learn):
  class P:
    def __init__(self):
      self.d = {'VTSCEnabled': True, 'CurveSpeedLearnEnabled': learn}

    def get_bool(self, k):
      return bool(self.d.get(k))

    def get(self, k):
      return self.d.get(k)

    def put_nonblocking(self, k, v):
      self.d[k] = float(v)

  monkeypatch.setattr(ref, "Params", P)
  monkeypatch.setattr(ref, "_common_get_learned_speed", fake_db)
  store = P()
  old = ref.VTSC()
  shared = new.VTSC(comfort=new.ComfortCalibration(read=lambda b: store.get(KEYS[b]), write=lambda b, v: store.put_nonblocking(KEYS[b], v)))
  rng = random.Random(9)
  used_learned = targets = 0
  for i in range(6000):
    if i % 40 == 0:
      base, v = rng.uniform(0, 0.25), rng.uniform(0, 35)
    m = NS(orientationRate=NS(z=np.array([base + rng.uniform(-.03, .03) for _ in range(33)])),
           velocity=NS(x=np.array([v + rng.uniform(-1, 1) for _ in range(33)])))
    t_old, s_old, l_old = old.update(v, m, 37.0, 127.0)
    r = shared.update(v, m, enabled=True, learned_speed=(lambda k: fake_db(0, 0, k, True)) if learn else None)
    assert s_old.name.upper() == r.state.name and l_old == r.using_learned, i
    assert (t_old is None) == (r.target_speed is None), i
    if t_old is not None:
      targets += 1
      assert abs(t_old - r.target_speed) < 1e-9, i
    used_learned += r.using_learned
  assert targets > 1000 and (used_learned > 1000) == learn
  saved = lambda d: {k: round(v, 9) for k, v in d.items() if k.startswith('TSC')}  # noqa: E731
  assert saved(old.params.d) == saved(store.d) and saved(store.d)  # same calibration written
