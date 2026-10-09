"""tripd (relocated daemon): the implementation is shared with NGP10 in exopilot/runtime/tripd.py; this keeps EOP's EOPTrip* param names for the UI."""
from exopilot.runtime import tripd as _shared
from exopilot.runtime.tripd import *  # noqa: F401,F403
from exopilot.runtime.tripd import DailyStats, DriveDetector, DriveState, TripStats  # noqa: F401


class TripD(_shared.TripD):
  def __init__(self):
    super().__init__(keys=lambda suffix: "EOPTrip" + suffix)


def main():
  TripD().run()


if __name__ == "__main__":
  main()
