"""RCD port: reproduces EOP10's original rcd.py (eop_golden_rcd.json, recorded 2026-10-04 from the built clone) in legacy mode; the fix is separate."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

from nagaspilot.controls.eop_rcd import RCD, RoadCondition, classify_metrics
from nagaspilot.runtime.rcd import RCDRuntime

G = json.loads((Path(__file__).parent / 'eop_golden_rcd.json').read_text())


class SM(dict):
  def __init__(self, valid, **kw):
    super().__init__(kw)
    self.valid = valid


def sm_for(kind, v):
  if kind == 's':
    return SM({'surfaceStatus': True}, surfaceStatus=NS(hasSurfaceQuality=True, surfaceQuality=NS(score=v, texture='x')))
  if kind == 'i':
    return SM({'surfaceStatus': False}, surfaceStatus=NS(hasSurfaceQuality=True, surfaceQuality=NS(score=v, texture='x')))
  if kind == 'g':
    return SM({'monoSegments': True}, monoSegments=NS(segments=[NS(camera='road', hasRoad=v[0], hasEdge=v[1], hasDrivable=v[2])]))
  return SM({})


def run(legacy, seq):
  rt = RCDRuntime(lambda k: True, legacy_filter_bug=legacy)
  rows = []
  for kind, v in seq:
    st = rt.update(sm_for(kind, v))
    rows.append([st.condition.name, st.confidence, st.speed_limit_ms, st.is_active, st.reason])
  return rows


def test_legacy_mode_reproduces_eop10_exactly():
  for name, case in G['seq'].items():
    got = run(True, case['in'])
    for g, w in zip(got, case['out'], strict=True):
      assert g[0] == w[0] and g[3] == w[3] and g[4] == w[4] and abs(g[1] - w[1]) < 1e-9 and abs(g[2] - w[2]) < 1e-9, name


def test_disabled_reports_disabled():
  st = RCDRuntime(lambda k: False).update(sm_for('s', 0.9))
  assert [st.condition.name, st.confidence, st.speed_limit_ms, st.is_active, st.reason] == G['disabled']


def test_classifier_metrics_reproduce_eop10():
  for c in G['classify']:
    r = classify_metrics(*c['m'])
    assert [r.condition.name, r.confidence, r.description, r.speed_limit_ms] == c['out']


def test_fix_first_cap_is_the_limit_not_zero():
  rows = run(False, G['seq']['wet_active']['in'])
  active = [r[2] for r in rows if r[3]]
  assert active and all(abs(x - 12.0) < 1e-9 for x in active)          # EOP10 started at 0.29 m/s and took ~10 s to reach 12
  assert rows[-1][3] is False and rows[-1][2] == 0.0                   # released on a good road


def test_fix_reactivation_also_steps_to_the_limit():
  rows = run(False, G['seq']['reactivate_active']['in'])
  assert [r[2] for r in rows[-3:]] == [12.0] * 3


def test_fix_only_changes_the_filter_never_the_decisions():
  for case in G['seq'].values():
    old, new = run(True, case['in']), run(False, case['in'])
    assert [r[0] for r in old] == [r[0] for r in new] and [r[3] for r in old] == [r[3] for r in new]


def test_no_source_means_no_cap_and_core_is_pure():
  assert RCD().update(None).is_active is False
  st = RCDRuntime(lambda k: True).update(SM({}))
  assert not st.is_active and st.condition == RoadCondition.GOOD
