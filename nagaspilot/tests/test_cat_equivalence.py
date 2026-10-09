"""EOP10's CAT (adapter over the shared core) must behave exactly like the pre-refactor implementation."""
import json
import random
import time
from types import SimpleNamespace as NS

import pytest

from nagaspilot.tests import cat_reference as ref
from openpilot.selfdrive.controls.lib import cat as new


class _Params:
  def __init__(self, store):
    self.store = store

  def get(self, key):
    return self.store.get(key)

  def get_bool(self, key):
    return bool(self.store.get(key, False))

  def put_nonblocking(self, key, value):
    self.store[key] = value if isinstance(value, bytes) else str(value).encode()


class _Clock:
  t = 1000.0

  @classmethod
  def monotonic(cls):
    return cls.t



class _SM:
  def __init__(self, updated, cs, lp):
    self.updated = {"carState": updated, "liveParameters": updated}
    self.d = {"carState": cs, "liveParameters": lp}

  def __getitem__(self, k):
    return self.d[k]


def _lp(rng):
  ok = lambda: rng.random() > 0.08  # noqa: E731
  return NS(valid=ok(), posenetValid=ok(), sensorValid=ok(), steerRatioValid=ok(), stiffnessFactorValid=ok(),
            steerRatio=rng.uniform(5, 40), stiffnessFactor=rng.uniform(0.3, 2.0), angleOffsetDeg=rng.uniform(-3, 3))


def _cs(rng):
  return NS(vEgo=rng.uniform(0, 30), steeringAngleDeg=rng.uniform(-70, 70))


def _status_tuple(s):
  return (s.adaptive, s.confidence, s.steer_ratio, s.stiffness_factor, s.angle_offset_deg, s.base_steer_ratio, s.samples, s.note)


@pytest.mark.parametrize("fingerprint,seed_store,manual", [
  ("TESLA_MODEL_3", {}, False),
  ("OTHER_CAR", {}, False),
  ("OTHER_CAR", {"EOPCATManualSREnabled": True, "EOPCATManualSR": b"13.5"}, True),
  ("OTHER_CAR", {"EOPCATPersist": json.dumps({"carFingerprint": "OTHER_CAR", "steerRatio": 17.0, "stiffnessFactor": 1.2, "angleOffsetDeg": 0.4})}, False),
])
def test_old_and_new_cat_agree_on_random_drives(monkeypatch, fingerprint, seed_store, manual):
  monkeypatch.setattr(time, "monotonic", _Clock.monotonic)
  stores = ({"EOPCATEnabled": True, **seed_store}, {"EOPCATEnabled": True, **seed_store})
  stores[0].setdefault("EOPCATEnabled", True)
  monkeypatch.setattr(ref, "Params", lambda: _Params(stores[0]))
  monkeypatch.setattr(new, "Params", lambda: _Params(stores[1]))
  CP = NS(steerRatio=15.0, carFingerprint=fingerprint)
  old, cat = ref.CAT(CP), new.CAT(CP)
  rng = random.Random(7)
  saw_adaptive = saw_gated = False
  for i in range(6000):
    _Clock.t += rng.choice([0.01, 0.02, 0.05, 0.05, 0.05, 0.5])
    # mostly-good stretches so the filters actually become adaptive and persist
    cs = NS(vEgo=rng.uniform(8, 30), steeringAngleDeg=rng.uniform(-20, 20)) if (i // 400) % 2 == 0 else _cs(rng)
    lp = (NS(valid=True, posenetValid=True, sensorValid=True, steerRatioValid=True, stiffnessFactorValid=True,
             steerRatio=16.0, stiffnessFactor=1.1, angleOffsetDeg=0.3) if (i // 400) % 2 == 0 else _lp(rng))
    sm = _SM(rng.random() > 0.15, cs, lp)
    a, b = old.update(sm), cat.update(sm)
    assert _status_tuple(a) == _status_tuple(b), i
    saw_adaptive |= b.adaptive
    saw_gated |= b.note == "gated"
    assert old.get_adaptive_params() == cat.get_adaptive_params(), i
    if i == 3000:
      old.reset()
      cat.reset()
  assert saw_adaptive and (saw_gated or manual)  # the sequence exercised both regimes, so agreement is not vacuous
  assert ("EOPCATPersist" in stores[0]) == ("EOPCATPersist" in stores[1])
  if "EOPCATPersist" in stores[0] and not seed_store.get("EOPCATPersist"):
    a_, b_ = json.loads(stores[0]["EOPCATPersist"]), json.loads(stores[1]["EOPCATPersist"])
    assert {k: v for k, v in a_.items() if k != "ts"} == {k: v for k, v in b_.items() if k != "ts"}
