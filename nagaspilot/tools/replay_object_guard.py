#!/usr/bin/env python3
"""Offline shadow-mode metrics for monod + the cut-in speed trim, from a logged route.

Feeds `monoDetections`, `carState` and `radarState` from a log through the same pure policy the
planner uses (nagaspilot/controls/ngp_cutin_speed.py) and reports what it WOULD have done, plus
detector health and ranging agreement. Nothing is actuated.

  python3 -m nagaspilot.tools.replay_object_guard <route> [<route> ...]

Caveat printed with the result: on a radarless device `radarState` leads come from the vision
model, so the ranging comparison is a consistency check, not ground truth.
"""
import json
import math
import statistics
import sys

from nagaspilot.controls.ngp_cutin_speed import CutInSpeed, Obj, PlannedPath
from nagaspilot.controls.ngp_path_selector import PathSelector, PObj
from nagaspilot.runtime.path_adapter import lane_room

BEARING_GATE_RAD = 0.04
RANGE_RATIO_GATE = (0.5, 2.0)


def _pct(values, q):
  if not values:
    return None
  s = sorted(values)
  return s[min(len(s) - 1, int(q * len(s)))]


def analyze(msgs) -> dict:
  """`msgs`: iterable of log messages (m.which(), m.logMonoTime, and the union member attribute)."""
  cutin = CutInSpeed()
  selector = PathSelector()
  room = (0.0, 0.0)
  sel_n = nudge_n = slow_n = 0
  pa_n = 0
  modes: dict[int, int] = {}
  cases: dict[str, int] = {}
  rule_valid_n = disagree_n = 0
  d_acc_min = d_acc_max = 0.0
  offsets: list[float] = []
  slow_factors: list[float] = []
  v_ego = 0.0
  leads: list[tuple[float, float]] = []
  path = None
  last_t = None
  t_first = t_last = None
  exec_times: list[float] = []
  frames = 0
  triggers: list[dict] = []
  was_active = False
  rel_err: list[float] = []
  first_seen: dict[int, float] = {}
  first_match: dict[int, float] = {}
  checked = matched = 0

  for m in msgs:
    which = m.which()
    t = m.logMonoTime * 1e-9
    if which == 'carState':
      v_ego = float(m.carState.vEgo)
    elif which == 'modelV2':
      pos = m.modelV2.position
      cand = PlannedPath(list(pos.x), [-float(v) for v in pos.y]) if len(pos.x) >= 2 else None   # model y-right -> left
      path = cand if cand is not None and cand.valid else None
      room = lane_room(m.modelV2)
    elif which == 'pathAdjust':
      pa = m.pathAdjust
      pa_n += 1
      modes[int(pa.dppMode)] = modes.get(int(pa.dppMode), 0) + 1
      cases[str(pa.dppCase)] = cases.get(str(pa.dppCase), 0) + 1
      if pa.ruleValid:
        rule_valid_n += 1
        d_acc_min, d_acc_max = min(d_acc_min, float(pa.disagreeAccel)), max(d_acc_max, float(pa.disagreeAccel))
        disagree_n += abs(float(pa.disagreeCurvature)) > 0.006 or float(pa.disagreeAccel) > 2.5
    elif which == 'radarState':
      leads = [(float(lead.dRel), float(lead.yRel)) for lead in (m.radarState.leadOne, m.radarState.leadTwo) if lead.status]
    elif which == 'monoDetections':
      md = m.monoDetections
      frames += 1
      t_first = t if t_first is None else t_first
      t_last = t
      exec_times.append(float(md.modelExecutionTime))
      dt = 0.2 if last_t is None else max(t - last_t, 1e-3)
      last_t = t
      objs = []
      for d in md.detections:
        objs.append(Obj(int(d.trackId), float(d.x), float(d.y), float(d.vx), float(d.vy), float(d.sigmaX), float(d.confidence), str(d.className)))
        first_seen.setdefault(int(d.trackId), t)
        if d.confidence > 0 and d.x > 1.0:
          checked += 1
          for dist, y in leads:
            ratio = dist / d.x
            if abs(math.atan2(d.y, d.x) - math.atan2(y, dist)) <= BEARING_GATE_RAD and RANGE_RATIO_GATE[0] <= ratio <= RANGE_RATIO_GATE[1]:
              matched += 1
              rel_err.append((d.x - dist) / dist)
              first_match.setdefault(int(d.trackId), t)
              break
      res = cutin.update(v_ego, objs, dt, enabled=True, fresh=True, path=path)
      if res.active and not was_active:
        triggers.append({'t': round(t - (t_first or t), 2), 'track_id': res.track_id, 'v_ego': round(v_ego, 1),
                         'target_speed': round(res.target_speed, 1), 'urgency_s': None if res.ttc is None else round(res.ttc, 2)})
      was_active = res.active
      sel = selector.update(v_ego, [PObj(o.track_id, o.name, o.x, o.y, o.vx, o.vy, o.conf) for o in objs], path, room[0], room[1])
      if sel.reason != 'off':
        sel_n += 1
        if sel.reason == 'nudge' or abs(sel.offset_m) > 0:
          nudge_n += 1
          offsets.append(abs(sel.offset_m))
        if sel.reason == 'slow':
          slow_n += 1
          slow_factors.append(sel.speed_factor)

  duration = (t_last - t_first) if (t_first is not None and t_last is not None) else 0.0
  lead_times = [first_match[i] - first_seen[i] for i in first_match]
  return {
    'monoDetections_frames': frames,
    'duration_s': round(duration, 1),
    'rate_hz': round(frames / duration, 2) if duration > 0 else None,
    'exec_time_s': {'mean': round(statistics.fmean(exec_times), 4) if exec_times else None,
                    'p95': _pct(exec_times, 0.95), 'max': max(exec_times) if exec_times else None},
    'cutin_would_trigger': len(triggers),
    'cutin_triggers_per_hour': round(len(triggers) / (duration / 3600.0), 1) if duration > 0 else None,
    'cutin_triggers': triggers[:50],
    'ranging_vs_leads': {'objects_checked': checked, 'matched': matched,
                         'median_rel_err': None if not rel_err else round(statistics.median(rel_err), 3),
                         'p95_abs_rel_err': None if not rel_err else round(_pct([abs(e) for e in rel_err], 0.95), 3)},
    'path_selector': {'frames': sel_n, 'nudge_frames': nudge_n, 'slow_frames': slow_n,
                      'nudge_fraction': round(nudge_n / sel_n, 3) if sel_n else None,
                      'max_offset_m': round(max(offsets), 2) if offsets else 0.0,
                      'p95_offset_m': round(_pct(offsets, 0.95), 2) if offsets else 0.0,
                      'min_speed_factor': round(min(slow_factors), 3) if slow_factors else 1.0},
    'rule_channel': {'pathAdjust_frames': pa_n, 'dpp_mode_frames': {str(k): v for k, v in sorted(modes.items())},
                     'dpp_cases': dict(sorted(cases.items())), 'rule_valid_fraction': round(rule_valid_n / pa_n, 3) if pa_n else None,
                     'disagreement_fraction': round(disagree_n / rule_valid_n, 3) if rule_valid_n else None,
                     'rule_minus_policy_accel_range': [round(d_acc_min, 2), round(d_acc_max, 2)]},
    'detector_lead_time_s': {'n': len(lead_times), 'median': None if not lead_times else round(statistics.median(lead_times), 2)},
    'caveat': 'radarState leads are vision leads on radarless devices: ranging_vs_leads is a consistency check, not ground truth',
  }


def main(argv: list[str]) -> None:
  from openpilot.tools.lib.logreader import LogReader
  for route in argv:
    print(route)
    print(json.dumps(analyze(LogReader(route)), indent=2))


if __name__ == "__main__":
  main(sys.argv[1:])
