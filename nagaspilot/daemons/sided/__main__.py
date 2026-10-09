#!/usr/bin/env python3
"""SideD entrypoint when run as a module."""

from openpilot.nagaspilot.daemons.sided.sided.sided import main

if __name__ == "__main__":
    exit(main())
