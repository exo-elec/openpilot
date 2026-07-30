"""
TRIPD - Trip Statistics Daemon

Tracks lifetime, trip-level, and daily statistics including:
- Distance traveled
- Onroad and engaged time
- Drive counts
- Engagement ratio
"""

from openpilot.selfdrive.tripd.tripd import TripD, main

__all__ = ['TripD', 'main']
