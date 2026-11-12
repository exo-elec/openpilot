import json
from types import SimpleNamespace as NS

import exopilot.runtime.cat as runtime


class FakeParams:
  def __init__(self, values=None):
    self.values, self.put = dict(values or {}), {}

  def get_bool(self, key):
    return bool(self.values.get(key, False))

  def get(self, key):
    return self.values.get(key)

  def put_nonblocking(self, key, value):
    self.put[key] = value


def make(values, fingerprint="HONDA_CIVIC", steer_ratio=15.0, monkeypatch=None):
  params = FakeParams(values)
  monkeypatch.setattr(runtime, "Params", lambda: params)
  return runtime.CATRuntime(NS(steerRatio=steer_ratio, carFingerprint=fingerprint)), params


class SM(dict):
  def __init__(self, updated=True, v=20.0, sr=16.0):
    super().__init__(carState=NS(vEgo=v, steeringAngleDeg=2.0), liveParameters=NS(steerRatio=sr, stiffnessFactor=1.0, angleOffsetDeg=0.5,
                                                                                    valid=True, sensorValid=True, posenetValid=True,
                                                                                    steerRatioValid=True, stiffnessFactorValid=True))
    self.updated = {"carState": updated, "liveParameters": updated}


def test_disabled_never_adapts(monkeypatch):
  cat, _ = make({}, monkeypatch=monkeypatch)
  for _ in range(100):
    assert not cat.update(SM()).adaptive


def test_unset_manual_ratio_is_the_cars_own_not_the_clamp_floor(monkeypatch):
  cat, _ = make({"CATEnabled": True, "CATManualSREnabled": True, "CATManualSR": 0.0}, monkeypatch=monkeypatch)
  status = cat.update(SM())
  assert status.adaptive and status.steer_ratio == 15.0


def test_manual_ratio_is_used_and_clamped(monkeypatch):
  cat, _ = make({"CATEnabled": True, "CATManualSREnabled": True, "CATManualSR": 17.5}, monkeypatch=monkeypatch)
  assert cat.update(SM()).steer_ratio == 17.5
  cat, _ = make({"CATEnabled": True, "CATManualSREnabled": True, "CATManualSR": 99.0}, monkeypatch=monkeypatch)
  assert cat.update(SM()).steer_ratio == 30.0                    # 2x the car's ratio


def test_seed_is_used_only_for_the_same_car(monkeypatch):
  seed = json.dumps({"carFingerprint": "HONDA_CIVIC", "steerRatio": 16.5, "stiffnessFactor": 1.1, "angleOffsetDeg": 0.3})
  cat, _ = make({"CATPersist": seed}, monkeypatch=monkeypatch)
  assert cat._load_persisted_seed() == (16.5, 1.1, 0.3)
  cat, _ = make({"CATPersist": seed}, fingerprint="TOYOTA_RAV4", monkeypatch=monkeypatch)
  assert cat._load_persisted_seed() == (15.0, 1.0, 0.0)
