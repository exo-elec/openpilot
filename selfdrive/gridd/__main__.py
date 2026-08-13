#!/usr/bin/env python3
"""GridD entrypoint when run as a module."""

from openpilot.selfdrive.gridd.gridd.gridd import main

if __name__ == "__main__":
    exit(main())
