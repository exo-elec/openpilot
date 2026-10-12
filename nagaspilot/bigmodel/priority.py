"""Take a helper thread off modeld's realtime scheduling (Linux): a thread started after config_realtime_process(7, 54)
inherits SCHED_FIFO and the single-core affinity, and an equal-priority thread that wakes takes the core until it blocks."""
import os


def background_thread() -> None:
  try:
    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
    os.sched_setaffinity(0, range(os.cpu_count() or 1))
  except (AttributeError, OSError, ValueError):
    pass   # not Linux, or not permitted: nothing was inherited that needs undoing
