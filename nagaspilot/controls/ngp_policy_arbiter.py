"""Authority between the learned policy and the rule-based channel (pathd), per mode.

  OFF           policy only
  SHADOW        policy only; the rule channel runs and its disagreement with the policy is measured
  SUPERVISE     accel = min(policy, rule)  (can only brake more); curvature stays the policy's
  PRIMARY_LONG  accel moves toward the rule channel's by a slewed correction; curvature from the policy
  PRIMARY_LAT   curvature moves toward the rule channel's by a slewed correction; accel from the policy
  PRIMARY_BOTH  both from the rule channel

Always fall back to the policy when the rule channel is missing, invalid or stale, or the driver is in
charge. In the PRIMARY modes a sustained disagreement (curvature or accel) hands control back to the
policy for a hold time, with the longitudinal side keeping the more conservative of the two.
Pure: no cereal, no Params. Frames: curvature left positive.
"""
import math
from dataclasses import dataclass
from enum import IntEnum

from nagaspilot.controls.ngp_rule_planner import A_BRAKE_LIMIT, RuleCmd


class Mode(IntEnum):
  OFF = 0
  SHADOW = 1
  SUPERVISE = 2
  PRIMARY_LONG = 3
  PRIMARY_LAT = 4
  PRIMARY_BOTH = 5


MAX_JERK = 2.5                 # m/s^3 slew of the accel correction when the rule channel is in charge
MAX_CURV_RATE = 0.004          # 1/m per second slew of the curvature correction
DISAGREE_CURV = 0.004          # 1/m
DISAGREE_ACCEL = 2.0           # m/s^2
DISAGREE_TIME_S = 1.0
FALLBACK_HOLD_S = 3.0


@dataclass(frozen=True)
class Blend:
  curvature: float
  accel: float
  source: str                  # policy | rule | min
  d_curv: float                # rule - policy
  d_accel: float
  fallback: str | None         # why the policy was used instead of the requested mode


class PolicyArbiter:
  def __init__(self):
    self.delta_curv = 0.0       # slewed correction added to the policy curvature
    self.delta_accel = 0.0
    self._dis_t = 0.0
    self._hold = 0.0

  def reset(self) -> None:
    self.__init__()

  def update(self, mode: int, policy_curv: float, policy_accel: float, rule: RuleCmd | None, fresh: bool,
             driver_override: bool, dt: float) -> Blend:
    mode = Mode(mode) if mode in set(int(m) for m in Mode) else Mode.OFF
    ok = rule is not None and rule.valid and fresh and all(math.isfinite(v) for v in (rule.curvature, rule.accel))
    d_curv = rule.curvature - policy_curv if ok else 0.0
    d_acc = rule.accel - policy_accel if ok else 0.0

    def policy(reason: str | None) -> Blend:
      self.delta_curv, self.delta_accel = 0.0, 0.0
      return Blend(policy_curv, policy_accel, 'policy', d_curv, d_acc, reason)

    if mode in (Mode.OFF, Mode.SHADOW):
      self._dis_t = self._hold = 0.0
      return policy(None)
    if driver_override:
      self._dis_t = self._hold = 0.0
      return policy('driver')
    if not ok:
      self._dis_t = 0.0
      return policy('rule invalid or stale')

    if mode == Mode.SUPERVISE:
      accel = min(policy_accel, max(rule.accel, A_BRAKE_LIMIT))
      self.delta_curv, self.delta_accel = 0.0, accel - policy_accel
      return Blend(policy_curv, accel, 'min' if accel < policy_accel else 'policy', d_curv, d_acc, None)

    # PRIMARY modes: watch the disagreement
    if abs(d_curv) > DISAGREE_CURV or abs(d_acc) > DISAGREE_ACCEL:
      self._dis_t += dt
    else:
      self._dis_t = max(self._dis_t - dt, 0.0)
    if self._dis_t >= DISAGREE_TIME_S:
      self._hold = FALLBACK_HOLD_S
      self._dis_t = 0.0
    if self._hold > 0.0:
      self._hold = max(self._hold - dt, 0.0)
      self.delta_curv = self._slew(self.delta_curv, 0.0, MAX_CURV_RATE * dt)
      accel = min(policy_accel, rule.accel) if mode in (Mode.PRIMARY_LONG, Mode.PRIMARY_BOTH) else policy_accel
      self.delta_accel = accel - policy_accel
      return Blend(policy_curv + self.delta_curv, accel, 'min' if accel < policy_accel else 'policy', d_curv, d_acc, 'disagree')

    # The rule channel is applied as a slewed *correction* on top of the policy: the policy's own dynamics pass
    # through, authority builds smoothly, and the output converges to the rule command at steady state.
    use_long = mode in (Mode.PRIMARY_LONG, Mode.PRIMARY_BOTH)
    use_lat = mode in (Mode.PRIMARY_LAT, Mode.PRIMARY_BOTH)
    self.delta_accel = self._slew(self.delta_accel, d_acc if use_long else 0.0, MAX_JERK * dt)
    self.delta_curv = self._slew(self.delta_curv, d_curv if use_lat else 0.0, MAX_CURV_RATE * dt)
    accel = min(max(policy_accel + self.delta_accel, A_BRAKE_LIMIT), 2.0)
    curv = policy_curv + self.delta_curv
    return Blend(curv, accel, 'rule' if (use_long or use_lat) else 'policy', d_curv, d_acc, None)

  @staticmethod
  def _slew(value: float, target: float, step: float) -> float:
    return value + min(max(target - value, -step), step)
