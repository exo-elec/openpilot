from nagaspilot.controls.ngp_arbiter import MAX_OFFSET_M, Proposal, arbitrate


def test_speed_is_the_minimum_and_never_raised():
  r = arbitrate([Proposal('a', speed_cap=22.0), Proposal('b', speed_cap=20.0), Proposal('c')], 0.5, 0.5)
  assert r.speed_cap == 20.0
  assert arbitrate([Proposal('a')], 0.5, 0.5).speed_cap is None
  assert arbitrate([Proposal('a', speed_cap=float('nan'))], 0.5, 0.5).speed_cap is None


def test_same_side_offsets_do_not_add_largest_wins():
  r = arbitrate([Proposal('soc', offset_m=0.2), Proposal('pathd', offset_m=0.4)], 0.5, 0.5)
  assert r.offset_m == 0.4 and r.sources == ('pathd',) and not r.conflict
  r = arbitrate([Proposal('soc', offset_m=-0.2), Proposal('pathd', offset_m=-0.1)], 0.5, 0.5)
  assert r.offset_m == -0.2 and r.sources == ('soc',)


def test_opposite_offsets_conflict_to_zero_and_keep_the_speed_cap():
  r = arbitrate([Proposal('soc', offset_m=0.3, speed_cap=24.0), Proposal('red', offset_m=-0.3, speed_cap=22.0)], 0.5, 0.5)
  assert r.conflict and r.offset_m == 0.0 and r.speed_cap == 22.0 and set(r.sources) == {'soc', 'red'}


def test_bounded_by_room_and_global_cap():
  assert arbitrate([Proposal('a', offset_m=0.5)], 0.2, 0.5).offset_m == 0.2
  assert arbitrate([Proposal('a', offset_m=-0.5)], 0.5, 0.1).offset_m == -0.1
  assert arbitrate([Proposal('a', offset_m=5.0)], 5.0, 5.0).offset_m == MAX_OFFSET_M
  assert arbitrate([Proposal('a', offset_m=0.5)], 0.0, 0.0).offset_m == 0.0
  assert arbitrate([Proposal('a', offset_m=0.5)], -1.0, -1.0).offset_m == 0.0


def test_non_finite_offsets_ignored_and_empty():
  r = arbitrate([Proposal('a', offset_m=float('nan')), Proposal('b', offset_m=0.1)], 0.5, 0.5)
  assert r.offset_m == 0.1
  r = arbitrate([], 0.5, 0.5)
  assert r.offset_m == 0.0 and r.speed_cap is None and not r.conflict
