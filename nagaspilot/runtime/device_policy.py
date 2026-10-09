"""NGP device conveniences with stock defaults and explicit units."""

def integer(value, default, low, high):
  try:
    result = int(value)
  except (ValueError, TypeError):
    return default
  return max(low, min(high, result))


def custom_shutdown_due(minutes, offroad_seconds, ignition, in_car, disabled, started_seen):
  timeout = integer(minutes, -1, -1, 300)
  return timeout >= 0 and offroad_seconds >= max(300, timeout * 60) and not ignition and in_car and not disabled and started_seen


class RecordingDelay:
  def __init__(self):
    self.started_at = None

  def blocked(self, started, now, seconds):
    if not started:
      self.started_at = None
      return []
    if self.started_at is None:
      self.started_at = now
    delay = integer(seconds, 0, 0, 300)
    return ['loggerd', 'encoderd'] if now - self.started_at < delay else []


def audible_alert(alert, mode):
  # Only engagement chimes may be muted. Safety warnings always remain audible.
  return 'none' if integer(mode, 0, 0, 1) == 1 and alert in ('engage', 'disengage') else alert


def vehicle_override(value, available):
  value = value.decode() if isinstance(value, bytes) else value
  return value if value and value in available else None
