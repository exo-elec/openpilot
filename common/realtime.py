"""Utilities for reading real time clocks and keeping soft real time constraints."""
import gc
import os
import sys
import time

from setproctitle import getproctitle

from openpilot.common.util import MovingAverage
from openpilot.system.hardware import PC


# time step for each process
DT_CTRL = 0.01  # controlsd
DT_MDL = 0.05  # model
DT_HW = 0.5  # hardwared and manager
DT_DMON = 0.05  # driver monitoring

# 4 big + 4 little CPU cores — same topology on both supported platforms:
# RK3588 (4x A76 big + 4x A55 little) and RK3576 (4x A72 big + 4x A55 little).
# Core *indices* are identical across both; only the big-core microarchitecture
# differs, which doesn't affect affinity assignment. Not yet verified against
# real RK3576 hardware — see docs/eop/RK3576_02M_SUPPORT.md.
BIG_CORES = [0, 1, 2, 3]      # A76 (RK3588) / A72 (RK3576) - high performance
LITTLE_CORES = [4, 5, 6, 7]   # A55 - power efficient

# Core type constants for simple allocation
CORE_BIG = "big"       # Use big cores
CORE_LITTLE = "little" # Use little cores


def set_core_type(core_type: str) -> None:
  """Set CPU affinity to big or little cores.

  Args:
    core_type: Either CORE_BIG ("big") or CORE_LITTLE ("little")
  """
  if core_type == CORE_BIG:
    set_core_affinity(BIG_CORES)
  elif core_type == CORE_LITTLE:
    set_core_affinity(LITTLE_CORES)
  else:
    raise ValueError(f"Invalid core_type: {core_type}. Use CORE_BIG or CORE_LITTLE")


class Priority:
  # CORE 2
  # - modeld = 55
  # - v4l2d = 54
  CTRL_LOW = 51 # plannerd & radard

  # CORE 3
  # - socketd = 55
  CTRL_HIGH = 53


def set_core_affinity(cores: list[int]) -> None:
  if sys.platform == 'linux' and not PC:
    os.sched_setaffinity(0, cores)


def config_realtime_process(dt: float, priority: int) -> None:
  """Configure real-time process scheduling priority.

  EOP NOTE: Signature changed from upstream openpilot (cores: int|list[int], priority: int).
  Core affinity is now set separately via set_core_type() in core_config.py.
  All EOP callers pass a DT_* float as the first argument.

  Args:
    dt: Time step (unused; retained to match EOP call sites)
    priority: Real-time priority level (1-99, higher is more critical)
  """
  gc.disable()
  if sys.platform == 'linux' and not PC:
    os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(priority))


class Ratekeeper:
  def __init__(self, rate: float, print_delay_threshold: float | None = 0.0) -> None:
    """Rate in Hz for ratekeeping. print_delay_threshold must be nonnegative."""
    self._interval = 1. / rate
    self._print_delay_threshold = print_delay_threshold
    self._frame = 0
    self._remaining = 0.0
    self._process_name = getproctitle()
    self._last_monitor_time = -1.
    self._next_frame_time = -1.

    self.avg_dt = MovingAverage(100)
    self.avg_dt.add_value(self._interval)

  @property
  def frame(self) -> int:
    return self._frame

  @property
  def remaining(self) -> float:
    return self._remaining

  @property
  def lagging(self) -> bool:
    expected_dt = self._interval * (1 / 0.9)
    return self.avg_dt.get_average() > expected_dt

  # Maintain loop rate by calling this at the end of each loop
  def keep_time(self) -> bool:
    lagged = self.monitor_time()
    if self._remaining > 0:
      time.sleep(self._remaining)
    return lagged

  # Monitors the cumulative lag, but does not enforce a rate
  def monitor_time(self) -> bool:
    if self._last_monitor_time < 0:
      self._next_frame_time = time.monotonic() + self._interval
      self._last_monitor_time = time.monotonic()

    prev = self._last_monitor_time
    self._last_monitor_time = time.monotonic()
    self.avg_dt.add_value(self._last_monitor_time - prev)

    lagged = False
    remaining = self._next_frame_time - time.monotonic()
    self._next_frame_time += self._interval
    if self._print_delay_threshold is not None and remaining < -self._print_delay_threshold:
      print(f"{self._process_name} lagging by {-remaining * 1000:.2f} ms")
      lagged = True
    self._frame += 1
    self._remaining = remaining
    return lagged
