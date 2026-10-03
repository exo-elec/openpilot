#!/usr/bin/env python3
"""SurfaceD entrypoint when run as a module."""

from openpilot.selfdrive.surfaced.surfaced.surfaced import main

if __name__ == "__main__":
    exit(main())
