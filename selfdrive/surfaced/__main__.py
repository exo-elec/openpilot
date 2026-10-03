#!/usr/bin/env python3
"""Run SurfaceD as a module."""
from openpilot.nagaspilot.daemons.surfaced.surfaced import main

if __name__ == "__main__":
  raise SystemExit(main())
