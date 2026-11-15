"""
TRIPD - Trip Statistics Daemon

Tracks lifetime, trip-level, and daily statistics including:
- Distance traveled
- Onroad and engaged time
- Drive counts
- Engagement ratio
"""

from exopilot.selfdrive.tripd.keys import TripD, main

__all__ = ['TripD', 'main']
