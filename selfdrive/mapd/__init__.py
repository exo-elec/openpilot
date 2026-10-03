"""
MAPD - Map Daemon

Provides OSM-based road geometry, speed limits, and curvature data
for MTSC (Map Turn Speed Control) and MSLC (Map Speed Limit Control).
"""

from openpilot.selfdrive.mapd.mapd import MapD, main

__all__ = ['MapD', 'main']
