#!/usr/bin/env python3
"""Power monitoring / auto-shutdown logic for EOP10.

Lightweight port of the v0.8.13 fork's simplified shutdown timer:
  - After the system goes offroad, start a configurable timer.
  - When the timer expires and ignition is off, set DoShutdown.
  - DisablePowerDown blocks the shutdown; ForcePowerDown triggers it immediately.

This is application-layer policy. Voltage integration / PMIC details live in
ExoPilot HAL; manager consumes the binary `deviceState.started` signal from
`stated` and the `EOPIgnitionOn` param from `socketd`.
"""

from __future__ import annotations

import time

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

# Default offroad timeout in minutes. 0 disables auto-shutdown.
DEFAULT_POWER_SAVER_ENTRY_MIN = 30

# Minimum time after boot before an auto-shutdown is allowed (fork used 10 min).
MIN_ON_TIME_S = 10 * 60


class PowerMonitoring:
  """Track offroad state and request shutdown after a timeout."""

  def __init__(self) -> None:
    self.params = Params()
    self.offroad_timestamp: float | None = None
    self._last_started: bool | None = None
    self._boot_time = time.monotonic()
    self._shutdown_requested = False

  def _get_timeout_s(self) -> float:
    """Read PowerSaverEntryDuration in minutes; 0 means disabled."""
    raw = self.params.get("PowerSaverEntryDuration")
    if raw is None:
      return DEFAULT_POWER_SAVER_ENTRY_MIN * 60.0
    try:
      minutes = int(raw)
    except ValueError:
      minutes = DEFAULT_POWER_SAVER_ENTRY_MIN
    return max(0, minutes) * 60.0

  def update(self, started: bool, ignition: bool) -> bool:
    """Call every manager iteration. Returns True if DoShutdown was set."""
    if self._shutdown_requested:
      return True

    # Track transition to offroad
    if self._last_started is not None and self._last_started and not started:
      self.offroad_timestamp = time.monotonic()
      cloudlog.info("power_monitoring: offroad transition, starting shutdown timer")
    elif started:
      self.offroad_timestamp = None

    self._last_started = started

    # Forced shutdown wins over everything
    if self.params.get_bool("ForcePowerDown"):
      cloudlog.warning("power_monitoring: ForcePowerDown set, requesting shutdown")
      self._request_shutdown()
      return True

    # Disabled / ignition on / no offroad timestamp -> no shutdown
    timeout_s = self._get_timeout_s()
    if timeout_s <= 0 or ignition or self.offroad_timestamp is None:
      return False

    if self.params.get_bool("DisablePowerDown"):
      return False

    elapsed = time.monotonic() - self.offroad_timestamp
    if elapsed < timeout_s:
      return False

    # Minimum uptime guard
    if (time.monotonic() - self._boot_time) < MIN_ON_TIME_S:
      return False

    cloudlog.warning(f"power_monitoring: offroad for {elapsed/60:.1f} min, requesting shutdown")
    self._request_shutdown()
    return True

  def _request_shutdown(self) -> None:
    self.params.put_bool("DoShutdown", True)
    self._shutdown_requested = True


def main() -> None:
  """Standalone test loop (not a daemon; intended to be driven by manager)."""
  pm = PowerMonitoring()
  params = Params()
  while True:
    started = params.get_bool("IsOnroad")
    ignition = params.get_bool("EOPIgnitionOn")
    pm.update(started, ignition)
    time.sleep(1.0)


if __name__ == "__main__":
  main()
