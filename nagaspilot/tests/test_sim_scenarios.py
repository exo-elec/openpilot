from nagaspilot.tools.sim_scenarios import run, scenarios

S = scenarios()


def pair(name, **kw):
  return run(S[name](), layer=False, **kw), run(S[name](), layer=True, **kw)


def test_cut_ins_get_more_gap_and_ttc_and_no_overlap():
  for name in ('car_cut_in_from_right', 'bike_cut_in_from_left'):
    off, on = pair(name)
    assert on['min_gap_ahead_m'] > off['min_gap_ahead_m'] + 1.0
    assert on['min_ttc_s'] > off['min_ttc_s']
    assert on['overlap_steps'] == 0 and on['min_speed_mps'] >= 12.0      # slows, never to a crawl


def test_still_better_with_range_noise():
  for name in ('car_cut_in_from_right', 'bike_cut_in_from_left'):
    off, on = pair(name, noise=0.05)
    assert on['min_gap_ahead_m'] >= off['min_gap_ahead_m'] and on['overlap_steps'] <= off['overlap_steps']


def test_no_action_when_nothing_threatens_us():
  for name in ('adjacent_lane_car_steady', 'car_moving_away'):
    off, on = pair(name)
    assert on['min_speed_mps'] == 25.0 and on['max_offset_m'] == 0.0


def test_alongside_traffic_gets_a_small_bounded_nudge_without_slowing():
  for name in ('truck_alongside_right', 'bike_filtering_alongside'):
    _, on = pair(name)
    assert 0.0 < on['max_offset_m'] <= 0.6 and on['min_speed_mps'] >= 24.0 and on['overlap_steps'] == 0


def test_rule_planner_as_the_primary_controller_matches_the_protection_layer_on_cut_ins():
  for name in ('car_cut_in_from_right', 'bike_cut_in_from_left'):
    base = run(S[name](), layer=False)
    rule = run(S[name](), controller='rule')
    assert rule['min_gap_ahead_m'] > base['min_gap_ahead_m'] + 1.0 and rule['overlap_steps'] == 0
    assert rule['min_speed_mps'] >= 12.0


def test_rule_planner_stays_quiet_and_bounded_with_nothing_to_do_and_with_noise():
  for name in ('adjacent_lane_car_steady', 'car_moving_away'):
    r = run(S[name](), controller='rule')
    assert r['min_speed_mps'] == 25.0 and r['max_offset_m'] == 0.0
  for name in ('truck_alongside_right', 'bike_filtering_alongside'):
    r = run(S[name](), controller='rule', noise=0.05)
    assert 0.0 < r['max_offset_m'] <= 0.6 and r['overlap_steps'] == 0
