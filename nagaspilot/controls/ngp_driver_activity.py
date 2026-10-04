"""Driver-activity monitoring for devices without a driver camera (replaces camera-based driver monitoring).

The goal is only that the driver does not fall asleep. Awareness starts full and drains while openpilot is engaged and the
car is moving fast enough, at a rate that depends on the speed band; any driver input refills it at once: hands on the wheel
(the car's own `steeringPressed`) or a press of the brake or gas pedal. Stages are proportional to awareness:
soft alert at 50 %, prompt at 25 %, critical at 0. At critical the monitor asks for a gentle forced deceleration; it never
disengages by itself.

Only a driver input refills awareness. openpilot moves the wheel itself, so steering angle or torque is not evidence of a driver;
the monitor takes a single boolean, `driver_engaged`, which the caller builds from the driver pedal and wheel-press flags.

Policies are tables of (band upper edge m/s, seconds from full to empty); the last band has no upper edge. Below the first
edge there is no drain. Bands switch with a small hysteresis so speed noise at an edge does not flip the rate.
"""
from dataclasses import dataclass

# strict: no drain below 11 m/s; 60 s in 11-22 m/s; 30 s in 22-33 m/s; 15 s above 33 m/s (the 15/30/60 s long-form timing)
POLICIES = {
  "strict": ((11.0, None), (22.0, 60.0), (33.0, 30.0), (float("inf"), 15.0)),
}
DEFAULT_POLICY = "strict"
HYSTERESIS_MPS = 0.5

SOFT_AT, PROMPT_AT = 0.5, 0.25
MIN_AWARENESS = -0.1  # below zero marks the critical stage (awarenessStatus < 0 makes controlsd decelerate)

OK, SOFT, PROMPT, CRITICAL = "ok", "soft", "prompt", "critical"
EVENT_FOR_STAGE = {SOFT: "preDriverUnresponsive", PROMPT: "promptDriverUnresponsive", CRITICAL: "driverUnresponsive"}


@dataclass(frozen=True)
class MonitorStatus:
  awareness: float
  stage: str
  band: int
  event: str | None
  force_decel: bool


class DriverActivityMonitor:
  def __init__(self, policy: str = DEFAULT_POLICY, dt: float = 0.05):
    self.table = POLICIES.get(policy, POLICIES[DEFAULT_POLICY])
    self.dt = float(dt)
    self.awareness = 1.0
    self.band = 0

  def _update_band(self, v_ego: float) -> int:
    band = self.band
    while band + 1 < len(self.table) and v_ego >= self.table[band][0] + HYSTERESIS_MPS:
      band += 1
    while band > 0 and v_ego < self.table[band - 1][0] - HYSTERESIS_MPS:
      band -= 1
    return band

  def update(self, v_ego: float, engaged: bool, standstill: bool, driver_engaged: bool) -> MonitorStatus:
    self.band = self._update_band(v_ego)
    if not engaged or driver_engaged:
      self.awareness = 1.0
    elif not standstill:
      seconds = self.table[self.band][1]
      if seconds is not None:  # None: band below the first edge, no drain
        self.awareness = max(self.awareness - self.dt / seconds, MIN_AWARENESS)

    if self.awareness <= 0.0:
      stage = CRITICAL
    elif self.awareness <= PROMPT_AT:
      stage = PROMPT
    elif self.awareness <= SOFT_AT:
      stage = SOFT
    else:
      stage = OK
    return MonitorStatus(self.awareness, stage, self.band, EVENT_FOR_STAGE.get(stage), stage == CRITICAL)
