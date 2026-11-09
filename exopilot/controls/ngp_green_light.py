"""Green-light notice: the car was held at a stop by the longitudinal planner and is now released.

Pure policy. Input is the planner's force-stop flag (`longitudinalPlan.ngpDlonForceStop`) and standstill; the notice fires on
the tick the force-stop clears while the car is still stopped. ExoPilot adds a spoken announcement on top.
"""


class NGPGreenLight:
  def __init__(self):
    self._was_force_stopped = False

  def update(self, force_stop: bool, standstill: bool) -> bool:
    released = self._was_force_stopped and not force_stop and standstill
    self._was_force_stopped = bool(force_stop)
    return released
