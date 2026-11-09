"""BRSC (Bumpy Road Speed Controller) — speed/accel policy driven by vertical IMU
acceleration.

Detects road roughness (potholes, expansion joints, washboard pavement) from the
vertical accelerometer axis and asks the caller to reduce cruise speed and/or the
positive acceleration limit while it is rough. Like ngp_tja / ngp_road_condition,
this is a pure policy: it never touches messaging or Params, and it may only
reduce speed and acceleration, never raise them above what the caller already
allows.

Real-world bump encounters are short (a single expansion joint or pothole is over
in well under a second; even a rough gravel/washboard patch is rarely more than a
few seconds), so the state machine is built around three real driving cases:
  - isolated events (a railroad crossing, one pothole) must NOT trigger a sustained
    slowdown -- an attack window filters these out unless roughness keeps recurring.
  - sustained roughness (broken pavement, washboard) is the actual target -- RMS of
    the high-passed vertical accel stays elevated for the whole patch, and the hold
    timer is kept topped up for as long as that lasts.
  - once the road smooths out, speed/accel is recovered gradually over a few
    seconds (never a step), so the car doesn't lurch forward the instant the last
    bump is behind it.
"""

import math
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class BRSCResult:
  active: bool             # True from first engagement through the end of release
  roughness_rms: float     # m/s^2 RMS of high-passed vertical accel over the analysis window
  speed_factor: float      # multiply the caller's target speed by this (<=1.0)
  accel_max: float         # cap on positive accel, m/s^2 (== accel_max_full when inactive)
  hold_remaining: float    # seconds of hold left before release begins
  control_authority: bool = True


class NGPBRSC:
  """Windowed-RMS vertical-acceleration roughness detector with retriggerable hold+decay."""

  # Tuning is expressed in physical units (seconds, m/s^2) so the same policy works
  # whether the caller samples at 20 Hz (planner) or 100 Hz (a dedicated daemon).
  BASELINE_TC_S = 2.0          # slow EMA tracks gravity/road-grade offset
  # WINDOW_S must stay shorter than ATTACK_S: the RMS window is how long a single
  # sample's energy can "linger" and look rough after the fact. If the window were
  # wider than the attack duration, one isolated spike (a rail crossing, a single
  # pothole) could satisfy the attack timer on its own just by sitting in the
  # window -- defeating the point of debouncing isolated events. Keeping the window
  # shorter forces genuinely repeated/sustained roughness to reach ATTACK_S.
  WINDOW_S = 0.2                 # RMS analysis window
  ATTACK_S = 0.3                 # sustained roughness required before engaging
  RMS_MILD = 1.0                 # m/s^2 RMS -- roughness floor (onset of "rough")
  RMS_SEVERE = 2.5               # m/s^2 RMS -- severity saturates here
  SPEED_FACTOR_FLOOR = 0.75      # never cut more than 25% off the target speed
  ACCEL_MAX_FLOOR_FRACTION = 0.45  # never cap positive accel below this fraction of full
  HOLD_BASE_S = 2.0              # minimum hold once engaged / kept topped up while rough
  HOLD_PER_RETRIGGER_S = 0.5     # extra hold added when a new rough interval starts mid-hold
  HOLD_CAP_S = 8.0               # accumulated hold never exceeds this
  RELEASE_RATE = 0.5             # recovery rate toward 1.0, in factor-units per second

  def __init__(self):
    self._baseline = 0.0
    self._baseline_initialized = False
    self._window = deque()  # (timestamp, high_passed_sample)
    self._t = 0.0
    self._attack_timer = 0.0
    self._hold_timer = 0.0
    self._prev_triggered = False
    self._speed_factor = 1.0
    self._accel_scale = 1.0

  def reset(self):
    self.__init__()

  def update(self, az: float, dt: float, accel_max_full: float = 2.0) -> BRSCResult:
    """Feed one vertical-accelerometer sample (m/s^2, gravity included) and dt (s)."""
    dt = max(dt, 1e-3)
    self._t += dt

    # Slow EMA baseline removes gravity + road grade; what's left is dynamic motion.
    alpha = 1.0 - math.exp(-dt / self.BASELINE_TC_S)
    if not self._baseline_initialized:
      self._baseline = az
      self._baseline_initialized = True
    else:
      self._baseline += alpha * (az - self._baseline)
    sample = az - self._baseline

    self._window.append((self._t, sample))
    while self._window and self._t - self._window[0][0] > self.WINDOW_S:
      self._window.popleft()
    rms = math.sqrt(sum(s * s for _, s in self._window) / len(self._window)) if self._window else 0.0

    rough_now = rms >= self.RMS_MILD
    self._attack_timer = self._attack_timer + dt if rough_now else 0.0
    triggered = self._attack_timer >= self.ATTACK_S

    if triggered:
      if not self._prev_triggered:
        # Rising edge: either a fresh episode, or a new rough interval inside an
        # already-decaying hold (recurring bumps) -- accumulate a bit extra for the
        # latter instead of resetting to the base, capped so a long rough stretch
        # can't compound into an unbounded slowdown.
        bonus = self.HOLD_PER_RETRIGGER_S if self._hold_timer > 0.0 else 0.0
        self._hold_timer = min(self.HOLD_CAP_S, max(self._hold_timer, self.HOLD_BASE_S) + bonus)
      else:
        # Continuously rough: keep the hold topped up so we don't release mid-patch.
        self._hold_timer = min(self.HOLD_CAP_S, max(self._hold_timer, self.HOLD_BASE_S))
    self._prev_triggered = triggered

    holding = self._hold_timer > 0.0
    if holding:
      severity = min(max((rms - self.RMS_MILD) / (self.RMS_SEVERE - self.RMS_MILD), 0.0), 1.0)
      target_speed_factor = 1.0 - severity * (1.0 - self.SPEED_FACTOR_FLOOR)
      target_accel_scale = 1.0 - severity * (1.0 - self.ACCEL_MAX_FLOOR_FRACTION)
      # Only allowed to deepen while holding -- never relax mid-episode, even if this
      # instant's RMS momentarily dips below a prior peak within the same patch.
      self._speed_factor = min(self._speed_factor, target_speed_factor)
      self._accel_scale = min(self._accel_scale, target_accel_scale)
      self._hold_timer = max(0.0, self._hold_timer - dt)
    else:
      self._speed_factor = min(1.0, self._speed_factor + self.RELEASE_RATE * dt)
      self._accel_scale = min(1.0, self._accel_scale + self.RELEASE_RATE * dt)

    active = holding or self._speed_factor < 0.999 or self._accel_scale < 0.999

    return BRSCResult(
      active=active,
      roughness_rms=rms,
      speed_factor=self._speed_factor,
      accel_max=self._accel_scale * accel_max_full,
      hold_remaining=self._hold_timer,
    )
