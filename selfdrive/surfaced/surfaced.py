"""Compatibility import for the relocated SurfaceD daemon."""
from openpilot.nagaspilot.daemons.surfaced.surfaced import *  # noqa: F401,F403

if __name__ == "__main__":
  from openpilot.nagaspilot.daemons.surfaced.surfaced import main
  raise SystemExit(main())
