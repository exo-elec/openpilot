from nagaspilot.tools.scenario_sweep import sweep
from nagaspilot.tools.sim_scenarios import run, scenarios

S = scenarios()


def test_sweep_gates_pass_and_each_system_never_adds_overlaps():
  r = sweep(24, seed=3)
  assert r['pass'], r['gates']
  for k in ('layer', 'rule', 'dpp'):
    assert r[k]['cut_in_overlap_runs'] <= r['baseline_cut_in_overlap_runs']
    assert r[k]['benign_false_trigger_rate'] < 0.02


def test_total_perception_outage_makes_the_integrated_system_equal_to_the_policy():
  for name in ('car_cut_in_from_right', 'truck_alongside_right', 'adjacent_lane_car_steady'):
    base = run(S[name](), layer=False)
    out = run(S[name](), controller='dpp', dropout=1.0)
    assert out['min_speed_mps'] == base['min_speed_mps'] and out['overlap_steps'] == base['overlap_steps'] and out['max_offset_m'] == 0.0
    assert max(out['modes']) <= 1 and out['modes'].get(0, 0) >= 200, out['modes']   # SHADOW only for the first 0.3 s, then OFF: perception unhealthy


def test_short_outages_do_not_make_the_mode_flap():
  for seed in range(5):
    r = run(S['car_cut_in_from_right'](), controller='dpp', dropout=0.1, seed=seed)
    assert r['mode_changes'] <= 8 and r['overlap_steps'] <= run(S['car_cut_in_from_right'](), layer=False)['overlap_steps']


def test_integrated_system_improves_cut_in_gap_and_stays_quiet_on_benign_traffic():
  for name in ('car_cut_in_from_right', 'bike_cut_in_from_left'):
    base, dpp = run(S[name](), layer=False), run(S[name](), controller='dpp')
    assert dpp['min_gap_ahead_m'] > base['min_gap_ahead_m'] + 1.5 and dpp['min_ttc_s'] > base['min_ttc_s'] and dpp['overlap_steps'] == 0
  for name in ('adjacent_lane_car_steady', 'car_moving_away'):
    assert run(S[name](), controller='dpp')['min_speed_mps'] == 25.0


def test_eop_ports_do_not_regress_and_the_sweep_exposes_that_they_add_little_for_cut_ins():
  r = sweep(24, seed=3)
  for k in ('eop_legacy', 'eop'):
    assert r[k]['cut_in_overlap_runs'] <= r['baseline_cut_in_overlap_runs'] and r[k]['benign_false_trigger_rate'] < 0.02
  assert r['layer']['cut_in_overlap_runs'] < r['eop']['cut_in_overlap_runs']       # EOP10's reactive proposers do not predict cut-ins
