"""Device-side handlers for the locate service: the phone's GNSS fix and map horizon land here, ready for the localization code to read.

No cereal output yet (owner decision pending); `latest_fix` and `tracker` are the hand-over. Both are on the comma's clock.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from nagaspilot.localization import map_horizon, phone_fix
from nagaspilot.localization.crosscheck import Fix
from nagaspilot.plp.channel import Message
from nagaspilot.plp.session import PlpSession


class LocateSink:
  def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
    self.clock = clock
    self.offset = phone_fix.ClockOffset()
    self.latest_fix: Fix | None = None
    self.mount: phone_fix.Mount | None = None
    self.tracker = map_horizon.HorizonTracker()
    self.horizons_taken = 0

  def attach(self, session: PlpSession) -> None:
    session.on('locate.gnss_fix', self._gnss_fix)
    session.on('locate.map_horizon', self._map_horizon)

  def _gnss_fix(self, m: Message) -> Message | None:
    self.latest_fix, self.mount = phone_fix.parse(m.payload, self.clock(), self.offset)     # BadFix is a ValueError: the session logs it and carries on
    return None

  def _map_horizon(self, m: Message) -> Message | None:
    h = map_horizon.MapHorizon.parse(m.payload, self.clock())
    if self.tracker.offer(h):
      self.horizons_taken += 1
    return None
