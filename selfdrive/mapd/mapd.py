"""mapd: moved to nagaspilot/mapd/mapd.py (shared with NGP10); this keeps EOP10's `EOPMapdEnabled` switch."""
from nagaspilot.mapd import mapd as _shared
from nagaspilot.mapd.mapd import *  # noqa: F401,F403


class MapD(_shared.MapD):
  def __init__(self):
    super().__init__(enabled_key="EOPMapdEnabled")


def main():
  MapD().run()


if __name__ == "__main__":
  main()
