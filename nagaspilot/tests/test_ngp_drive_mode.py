from nagaspilot.controls.ngp_drive_mode import CUSTOM, MODES, detect, settings_for
from nagaspilot.controls.longitudinal_policy import ACCELERATION_PROFILES
from nagaspilot.runtime.drive_mode import EOP_KEYS, NGP_KEYS, DriveModeApplier


class _Params:
  def __init__(self, store=None):
    self.store = dict(store or {})
    self.writes = []

  def get(self, key):
    v = self.store.get(key)
    return v.encode() if isinstance(v, str) else v

  def get_bool(self, key):
    return bool(self.store.get(key, False))

  def put(self, key, value):
    self.writes.append(key)
    self.store[key] = value

  def put_bool(self, key, value):
    self.writes.append(key)
    self.store[key] = value


def test_presets_are_coherent_and_use_real_profiles():
  assert set(MODES) == {"eco", "normal", "sport"}
  assert all(m.accel_profile in ACCELERATION_PROFILES for m in MODES.values())
  # slower acceleration pairs with the larger gap, faster with the smaller one: relaxed 2 > standard 1 > aggressive 0 in time gap
  assert MODES["eco"].personality > MODES["normal"].personality > MODES["sport"].personality
  assert not any(m.adaptive_gap for m in MODES.values())  # adaptive gap stays an explicit opt-in
  assert settings_for("custom") is None and settings_for("nope") is None


def test_personality_numbers_match_the_cereal_enum():
  import pytest
  e = getattr(pytest.importorskip("cereal").log, "LongitudinalPersonality", None)  # absent when another test stubbed cereal
  if e is None:
    pytest.skip("cereal is stubbed in this session")
  assert (e.aggressive, e.standard, e.relaxed) == (MODES["sport"].personality, MODES["normal"].personality, MODES["eco"].personality)


def test_detect_names_the_mode_or_custom():
  for name, m in MODES.items():
    assert detect(m.accel_profile, m.personality, m.adaptive_gap) == name
  assert detect("sport", 2, False) == CUSTOM  # sport acceleration with a relaxed gap is not a mode
  assert detect("normal", 1, True) == CUSTOM


def test_applier_writes_once_per_mode_change_and_never_for_custom():
  p = _Params({NGP_KEYS["mode"]: "sport"})
  a = DriveModeApplier(p)
  assert a.update(now=10.0) and p.store[NGP_KEYS["accel"]] == "sport" and p.store[NGP_KEYS["personality"]] == 0
  assert a.current() == "sport"
  n = len(p.writes)
  assert not a.update(now=12.0) and len(p.writes) == n  # same mode: nothing rewritten
  p.store[NGP_KEYS["personality"]] = 2  # the driver cycles the distance button
  assert not a.update(now=14.0) and a.current() == CUSTOM  # preset is left alone, reported as custom
  p.store[NGP_KEYS["mode"]] = "eco"
  assert a.update(now=16.0) and a.current() == "eco"
  p.store[NGP_KEYS["mode"]] = "custom"
  n = len(p.writes)
  assert not a.update(now=18.0) and len(p.writes) == n


def test_applier_polls_at_most_once_a_second_and_defaults_to_custom():
  p = _Params()
  a = DriveModeApplier(p)
  assert not a.update(now=5.0)  # no mode param: custom, nothing written
  p.store[NGP_KEYS["mode"]] = "eco"
  assert not a.update(now=5.5)  # inside the poll interval
  assert a.update(now=6.1)


def test_exopilot_uses_its_own_keys_through_the_same_applier():
  p = _Params({EOP_KEYS["mode"]: "eco"})
  a = DriveModeApplier(p, EOP_KEYS)
  assert a.update(now=3.0)
  assert p.store[EOP_KEYS["accel"]] == "eco" and p.store[EOP_KEYS["personality"]] == 2 and p.store[EOP_KEYS["gap"]] is False
  assert NGP_KEYS["accel"] not in p.store and a.current() == "eco"
