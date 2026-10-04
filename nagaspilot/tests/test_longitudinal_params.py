from nagaspilot.runtime import longitudinal_params as lp


class _Params:
  def __init__(self, profile=b"sport", gap=True):
    self.profile, self.gap, self.reads = profile, gap, 0

  def get(self, key):
    self.reads += 1
    return self.profile

  def get_bool(self, key):
    self.reads += 1
    return self.gap


def _reset(monkeypatch, params):
  monkeypatch.setattr(lp, "_params", lambda: params)
  monkeypatch.setattr(lp, "_accel_profile_cache", {"ts": 0.0, "profile": "normal"})
  monkeypatch.setattr(lp, "_adaptive_gap_cache", {"ts": 0.0, "enabled": False})


def test_accel_profile_is_cached_and_unknown_values_fall_back(monkeypatch):
  params = _Params(profile=b"warp-speed")
  _reset(monkeypatch, params)
  assert lp.load_accel_profile() == "normal"
  assert lp.load_accel_profile() == "normal"
  assert params.reads == 1  # second call served from the 2 s cache


def test_known_profile_is_returned(monkeypatch):
  name = next(iter(lp.ACCELERATION_PROFILES))
  _reset(monkeypatch, _Params(profile=name.encode()))
  assert lp.load_accel_profile() == name


def test_adaptive_gap_flag_is_cached(monkeypatch):
  params = _Params(gap=True)
  _reset(monkeypatch, params)
  assert lp.load_adaptive_gap_enabled() is True
  assert lp.load_adaptive_gap_enabled() is True
  assert params.reads == 1
