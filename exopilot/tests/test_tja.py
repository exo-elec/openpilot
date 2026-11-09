from types import SimpleNamespace

from openpilot.selfdrive.controls.lib.dp_tja import TrafficJamAssist


def lead(d_rel=12.0, v_rel=0.0, model_prob=0.95, track_id=1, status=True):
  return SimpleNamespace(status=status, dRel=d_rel, vRel=v_rel, modelProb=model_prob,
                         radarTrackId=track_id)


def test_tja_only_runs_in_crawl_and_walk_with_a_lead():
  tja = TrafficJamAssist(0.05)
  assert not tja.update(6.0, lead()).active
  assert not tja.update(3.0, lead(status=False)).active
  assert tja.update(3.0, lead()).active


def test_stable_wide_gap_enables_controlled_gap_closing():
  tja = TrafficJamAssist(0.05)
  result = None
  for _ in range(25):
    result = tja.update(3.0, lead(d_rel=14.0))
  assert result is not None
  assert result.accel_scale == 1.0
  assert result.jerk_scale == 1.0


def test_cutin_or_rapid_closing_suppresses_positive_acceleration():
  tja = TrafficJamAssist(0.05)
  for _ in range(25):
    tja.update(3.0, lead(d_rel=14.0, track_id=1))

  cut_in = tja.update(3.0, lead(d_rel=8.0, track_id=2))
  assert cut_in.cut_in
  assert cut_in.accel_scale == 0.0

  closing = TrafficJamAssist(0.05).update(4.0, lead(d_rel=10.0, v_rel=-4.0))
  assert closing.accel_scale == 0.0
