from nagaspilot.controls.ngp_policy_arbiter import DISAGREE_TIME_S, Mode, PolicyArbiter
from nagaspilot.controls.ngp_rule_planner import A_BRAKE_LIMIT, RuleCmd


def cmd(curv=0.0, accel=0.0, valid=True):
  return RuleCmd(valid, curv, accel, 25.0, 0.0, None, None, 'x')


def run(mode, rule, pc=0.0, pa=0.0, n=1, fresh=True, drv=False, dt=0.05, arb=None):
  arb = arb or PolicyArbiter()
  b = None
  for _ in range(n):
    b = arb.update(mode, pc, pa, rule, fresh, drv, dt)
  return b, arb


def test_off_and_shadow_never_change_the_policy_but_measure_disagreement():
  for m in (Mode.OFF, Mode.SHADOW):
    b, _ = run(m, cmd(0.01, -3.0), pc=0.0, pa=0.5)
    assert b.curvature == 0.0 and b.accel == 0.5 and b.source == 'policy'
  b, _ = run(Mode.SHADOW, cmd(0.01, -3.0), pc=0.002, pa=0.5)
  assert abs(b.d_curv - 0.008) < 1e-9 and abs(b.d_accel + 3.5) < 1e-9


def test_supervise_only_brakes_more():
  b, _ = run(Mode.SUPERVISE, cmd(0.02, -2.0), pc=0.001, pa=0.3)
  assert b.accel == -2.0 and b.curvature == 0.001 and b.source == 'min'
  b, _ = run(Mode.SUPERVISE, cmd(0.02, 1.0), pc=0.001, pa=0.3)
  assert b.accel == 0.3 and b.source == 'policy'
  b, _ = run(Mode.SUPERVISE, cmd(0.0, -9.0), pa=0.0)
  assert b.accel == A_BRAKE_LIMIT


def test_primary_long_adds_a_slewed_correction_and_leaves_curvature_alone():
  b, arb = run(Mode.PRIMARY_LONG, cmd(0.0, -2.0), pc=0.003, pa=0.0)
  assert b.curvature == 0.003 and abs(b.accel + 2.5 * 0.05) < 1e-9 and b.source == 'rule'
  b, _ = run(Mode.PRIMARY_LONG, cmd(0.0, -2.0), pc=0.003, pa=0.0, n=60, arb=arb)
  assert b.accel <= -1.9
  # the policy's own dynamics pass through: when the policy accel moves, the output moves with it plus the correction
  b2, _ = run(Mode.PRIMARY_LONG, cmd(0.0, -2.0), pc=0.003, pa=0.5, n=1, arb=arb)
  assert b2.accel > b.accel and b2.accel < 0.5


def test_primary_lat_adds_a_slewed_correction_and_leaves_accel_alone():
  b, arb = run(Mode.PRIMARY_LAT, cmd(0.01, 0.0), pc=0.0, pa=0.4)
  assert b.accel == 0.4 and abs(b.curvature - 0.004 * 0.05) < 1e-9
  b, _ = run(Mode.PRIMARY_LAT, cmd(0.003, 0.0), pc=0.0, pa=0.4, n=100, arb=arb)
  assert abs(b.curvature - 0.003) < 1e-6
  b, _ = run(Mode.PRIMARY_LAT, cmd(0.003, 0.0), pc=0.005, pa=0.4, n=100, arb=arb)     # policy moved a little: output still converges to the rule
  assert abs(b.curvature - 0.003) < 1e-6


def test_fallbacks_driver_stale_invalid_and_bad_mode():
  assert run(Mode.PRIMARY_BOTH, cmd(0.01, -2.0), drv=True)[0].fallback == 'driver'
  assert run(Mode.PRIMARY_BOTH, cmd(0.01, -2.0), fresh=False)[0].fallback == 'rule invalid or stale'
  assert run(Mode.PRIMARY_BOTH, cmd(valid=False))[0].fallback == 'rule invalid or stale'
  assert run(Mode.PRIMARY_BOTH, None)[0].source == 'policy'
  assert run(99, cmd(0.01, -2.0), pa=0.1)[0].accel == 0.1          # unknown mode -> OFF


def test_sustained_disagreement_hands_back_to_the_policy_keeping_the_safer_accel():
  arb = PolicyArbiter()
  n = int(DISAGREE_TIME_S / 0.05) + 2
  b, arb = run(Mode.PRIMARY_BOTH, cmd(0.02, -3.0), pc=0.0, pa=0.5, n=n, arb=arb)
  assert b.fallback == 'disagree' and b.accel == -3.0 and abs(b.curvature) <= 0.004      # more conservative accel wins; the curvature correction decays, no step
  b, arb = run(Mode.PRIMARY_BOTH, cmd(0.02, -3.0), pc=0.0, pa=0.5, n=100, arb=arb)
  assert b.curvature == 0.0
  b, _ = run(Mode.PRIMARY_BOTH, cmd(0.02, 3.0), pc=0.0, pa=0.5, n=1, arb=arb)
  assert b.fallback == 'disagree' and b.accel == 0.5                               # rule would accelerate: policy accel kept
