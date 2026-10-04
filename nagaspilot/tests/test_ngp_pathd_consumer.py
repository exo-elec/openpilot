from nagaspilot.controls.ngp_pathd_consumer import MAX_OFFSET_M, MIN_SPEED_FLOOR, PathAdjustFollower, speed_cap


def run(f, cmd, n, fresh=True, allowed=True, dt=0.05):
  v = 0.0
  for _ in range(n):
    v = f.update(cmd, fresh, allowed, dt)
  return v


def test_offset_ramps_slowly_and_is_clamped():
  f = PathAdjustFollower()
  assert abs(f.update(0.5, True, True, 0.05) - 0.0075) < 1e-9       # 0.15 m/s * 0.05 s
  v = run(f, 5.0, 200)
  assert abs(v - MAX_OFFSET_M) < 1e-9                                # clamped command
  assert f.curvature_delta() > 0                                     # left offset -> left (positive) curvature


def test_release_when_driver_or_lane_change_or_stale():
  for kw in ({'allowed': False}, {'fresh': False}):
    f = PathAdjustFollower()
    run(f, 0.5, 100)
    v = run(f, 0.5, 100, **kw)
    assert abs(v) < 1e-9                                             # ramped back to zero, never a step
  f = PathAdjustFollower()
  run(f, 0.5, 100)
  assert 0.0 < f.update(0.5, True, False, 0.05) < 0.5               # released by one ramp step only


def test_nan_command_is_ignored_and_negative_is_right():
  f = PathAdjustFollower()
  assert run(f, float('nan'), 10) == 0.0
  assert run(f, -0.4, 100) < 0 and f.curvature_delta() < 0


def test_speed_cap_only_lowers_with_floor():
  assert speed_cap(25.0, 1.0, True) is None and speed_cap(25.0, 0.9, False) is None and speed_cap(5.0, 0.8, True) is None
  assert abs(speed_cap(25.0, 0.9, True) - 22.5) < 1e-9
  assert speed_cap(9.0, 0.5, True) == MIN_SPEED_FLOOR
  assert speed_cap(25.0, float('nan'), True) is None
  assert speed_cap(25.0, 0.9, True) <= 25.0
