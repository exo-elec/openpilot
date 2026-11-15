"""One VTSC for every platform: plain on a comma 3 (NGP10), with a comfort store and learned speeds on ExoPilot (EOP10)."""
from types import SimpleNamespace as NS

from exopilot.selfdrive.controls.lib.vtsc import (BUCKET_COMFORT, ComfortCalibration, CurvatureBucket, VTSC, VTSCState,
                                                  bucket_for_curvature)


def curve(rate=0.15, v=20.0):
  return NS(orientationRate=NS(z=[rate] * 33), velocity=NS(x=[v] * 33))


def enter(vtsc, **kw):
  vtsc.update(20.0, curve(0.0), **kw)
  return vtsc.update(20.0, curve(), **kw)


def test_buckets_follow_curve_sharpness():
  assert bucket_for_curvature(0.005) is CurvatureBucket.MILD
  assert bucket_for_curvature(0.02) is CurvatureBucket.MEDIUM
  assert bucket_for_curvature(0.05) is CurvatureBucket.SHARP


def test_comma3_mode_needs_no_store_and_reports_vision_target():
  r = enter(VTSC())
  assert r.state is VTSCState.ENTERING and r.target_speed is not None and not r.using_learned
  assert abs(r.target_speed - (BUCKET_COMFORT[bucket_for_curvature(r.curvature)] / r.curvature) ** 0.5) < 1e-9


def test_stored_comfort_changes_the_target():
  low = enter(VTSC(comfort=ComfortCalibration(read=lambda b: 1.2))).target_speed
  high = enter(VTSC(comfort=ComfortCalibration(read=lambda b: 2.5))).target_speed
  assert low < high


def test_learned_speed_wins_only_while_entering_or_turning():
  vtsc = VTSC()
  assert vtsc.update(20.0, curve(0.0), learned_speed=lambda k: 9.0).target_speed is None  # ENABLED: no target at all
  r = vtsc.update(20.0, curve(), learned_speed=lambda k: 9.0)
  assert r.target_speed == 9.0 and r.using_learned
  r = VTSC()
  enter(r)
  assert not r.update(20.0, curve(), learned_speed=lambda k: 0.0).using_learned  # 0 = no learned speed: vision target


def test_calibration_learns_and_persists_after_enough_samples():
  writes, t = [], [0.0]
  cal = ComfortCalibration(read=lambda b: None, write=lambda b, v: writes.append((b, v)), clock=lambda: t[0])
  for _ in range(ComfortCalibration.MIN_SAMPLES - 1):
    t[0] += 1.0
    cal.observe(2.4, 0.02)
  assert writes == []
  t[0] += 31.0
  cal.observe(2.4, 0.02)
  assert writes and writes[0][0] is CurvatureBucket.MEDIUM
  assert BUCKET_COMFORT[CurvatureBucket.MEDIUM] < cal.comfort(0.02) <= ComfortCalibration.MAX_LAT_ACC


def test_bad_stored_values_fall_back_to_defaults():
  cal = ComfortCalibration(read=lambda b: "not a number")
  assert cal.comfort(0.05) == BUCKET_COMFORT[CurvatureBucket.SHARP]
