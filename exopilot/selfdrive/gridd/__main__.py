#!/usr/bin/env python3
"""GridD entrypoint when run as a module."""

from exopilot.selfdrive.gridd.occupancy import main

if __name__ == "__main__":
    exit(main())
