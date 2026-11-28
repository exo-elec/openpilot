"""Portable steering policy helpers shared by NGP and EOP runtimes."""


def reduce_steer(steer: float, steering_angle_deg: float, cs_angle_deg: float, resume_diff: float) -> tuple[float, float]:
  """Ramp steering authority back in after lateral control resumes."""
  end_time = 1.75
  if resume_diff >= end_time:
    return steer, steering_angle_deg

  # Higher rate means steeper curve; 0 is linear.
  rate = 0.003
  mul = min(1.0, (resume_diff / end_time) ** (1.0 - rate))
  return steer * mul, (steering_angle_deg - cs_angle_deg) * mul + cs_angle_deg


class SteeringResumeRamp:
  """Track lateral-active edges and apply the shared resume ramp."""

  def __init__(self):
    self._lat_active_prev = False
    self._steer_resumed = False
    self._last_resume_time = 0.0

  def update(self, lat_active: bool, steer: float, steering_angle_deg: float,
             cs_angle_deg: float, now: float) -> tuple[float, float]:
    if not lat_active:
      self._steer_resumed = False
    elif not self._lat_active_prev:
      self._steer_resumed = True
      self._last_resume_time = now

    if self._steer_resumed:
      resume_diff = now - self._last_resume_time
      if resume_diff < 2.0:
        steer, steering_angle_deg = reduce_steer(steer, steering_angle_deg, cs_angle_deg, resume_diff)
      else:
        self._steer_resumed = False

    self._lat_active_prev = lat_active
    return steer, steering_angle_deg
