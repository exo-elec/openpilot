#!/usr/bin/env python3
"""Monte-Carlo sweep over randomised cut-in and benign-traffic scenarios, with acceptance gates.

Uses the toy closed loop in sim_scenarios (point-mass ego, stand-in lead logic) with the protection layer and
the rule planner against the stand-in policy. Randomised: gap, closing speed, lateral speed and start,
class (car / motorcycle), range noise, detection dropouts. Negative cases (steady or receding neighbours at
random offsets) measure the false-trigger rate.

Gates (exit code 1 if any fails): in the cut-in set the layer, the rule planner and the integrated DPP system are never worse than the
baseline in >= 95 % of runs and have no more overlaps; in the benign set fewer than 2 % of runs trigger.
This is a logic and tuning-direction check, not a statement about the real car.

  python3 -m nagaspilot.tools.scenario_sweep [runs]
"""
import json
import random
import sys

from nagaspilot.tools.sim_scenarios import Actor, run

GATES = {'not_worse_fraction': 0.95, 'false_trigger_rate': 0.02}


def _cut_in(rng: random.Random) -> list[Actor]:
  name = rng.choice(['car', 'car', 'motorcycle'])
  side = rng.choice([-1, 1])
  gap = rng.uniform(12.0, 40.0)
  v = 25.0 + rng.uniform(-8.0, 2.0)
  width = 0.8 if name == 'motorcycle' else 1.8
  start_y = side * rng.uniform(2.6, 4.2)
  return [Actor(name, gap, start_y, v, width=width, cut_start=rng.uniform(0.5, 2.5),
                cut_vy=-side * rng.uniform(0.8, 2.0), cut_stop_y=0.0)]


def _benign(rng: random.Random) -> list[Actor]:
  side = rng.choice([-1, 1])
  name = rng.choice(['car', 'car', 'truck', 'motorcycle'])
  width = {'car': 1.8, 'truck': 2.5, 'motorcycle': 0.8}[name]
  y = side * rng.uniform(3.4, 4.6)                         # a neighbouring lane, never touching ours
  recede = rng.random() < 0.4
  a = Actor(name, rng.uniform(5.0, 40.0), y, 25.0 + rng.uniform(-2.0, 3.0), width=width)
  if recede:
    a.cut_start, a.cut_vy = 0.0, side * rng.uniform(0.3, 1.0)
  return [a]


def _worse(on: dict, base: dict) -> bool:
  g_on, g_base = on['min_gap_ahead_m'], base['min_gap_ahead_m']
  if g_base is None:
    return False
  return g_on is not None and g_on < g_base - 0.5


def sweep(runs: int = 60, seed: int = 7) -> dict:
  rng = random.Random(seed)
  cut = {k: {'not_worse': 0, 'overlap': 0, 'gap_gain': []} for k in ('layer', 'rule', 'dpp')}
  cut.update({'baseline_overlap': 0, 'runs': runs})
  for i in range(runs):
    noise, dropout = rng.choice([0.0, 0.03, 0.08]), rng.choice([0.0, 0.05, 0.2])
    make = _cut_in
    base = run(make(random.Random(i)), layer=False)
    cut['baseline_overlap'] += base['overlap_steps'] > 0
    for key, kw in (('layer', {'layer': True}), ('rule', {'controller': 'rule'}), ('dpp', {'controller': 'dpp'})):
      r = run(make(random.Random(i)), noise=noise, dropout=dropout, seed=i, **kw)
      cut[key]['not_worse'] += not _worse(r, base)
      cut[key]['overlap'] += r['overlap_steps'] > 0
      if r['min_gap_ahead_m'] is not None and base['min_gap_ahead_m'] is not None:
        cut[key]['gap_gain'].append(r['min_gap_ahead_m'] - base['min_gap_ahead_m'])
  triggers = {'layer': 0, 'rule': 0, 'dpp': 0}
  for i in range(runs):
    noise = rng.choice([0.0, 0.03, 0.08])
    for key, kw in (('layer', {'layer': True}), ('rule', {'controller': 'rule'}), ('dpp', {'controller': 'dpp'})):
      r = run(_benign(random.Random(1000 + i)), noise=noise, seed=i, **kw)
      triggers[key] += (r['min_speed_mps'] < 24.0) or (r['max_offset_m'] > 0.45)
  out = {'runs': runs, 'baseline_cut_in_overlap_runs': cut['baseline_overlap']}
  for key in ('layer', 'rule', 'dpp'):
    gains = sorted(cut[key]['gap_gain'])
    out[key] = {'cut_in_not_worse_fraction': round(cut[key]['not_worse'] / runs, 3), 'cut_in_overlap_runs': cut[key]['overlap'],
                'median_gap_gain_m': round(gains[len(gains) // 2], 1) if gains else None,
                'benign_false_trigger_rate': round(triggers[key] / runs, 3)}
  out['gates'] = {k: {'not_worse': out[k]['cut_in_not_worse_fraction'] >= GATES['not_worse_fraction'],
                      'no_more_overlaps': out[k]['cut_in_overlap_runs'] <= out['baseline_cut_in_overlap_runs'],
                      'false_triggers': out[k]['benign_false_trigger_rate'] < GATES['false_trigger_rate']} for k in ('layer', 'rule', 'dpp')}
  out['pass'] = all(all(v.values()) for v in out['gates'].values())
  return out


def main(argv: list[str]) -> int:
  res = sweep(int(argv[0]) if argv else 60)
  print(json.dumps(res, indent=2))
  return 0 if res['pass'] else 1


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
