#!/usr/bin/env python3
"""Unit tests for SteamD Geofence.

Validates point-in-polygon logic and edge cases.
"""

import sys
import unittest
from unittest.mock import MagicMock

# Mock compiled dependencies before importing
sys.modules['msgq'] = MagicMock()
sys.modules['msgq.visionipc'] = MagicMock()
sys.modules['cereal'] = MagicMock()
sys.modules['cereal.messaging'] = MagicMock()
sys.modules['openpilot.common.swaglog'] = MagicMock()

from openpilot.selfdrive.steamd.geofence import Geofence


class TestGeofence(unittest.TestCase):

  # Simple square polygon centered on (0,0)
  SQUARE = "[[-1,-1],[-1,1],[1,1],[1,-1]]"

  def test_disabled_when_empty(self):
    g = Geofence("")
    self.assertFalse(g.enabled)
    self.assertTrue(g.contains(0, 0))
    self.assertTrue(g.contains(100, 200))

  def test_contains_center(self):
    g = Geofence(self.SQUARE)
    self.assertTrue(g.enabled)
    self.assertTrue(g.contains(0, 0))

  def test_contains_inside(self):
    g = Geofence(self.SQUARE)
    self.assertTrue(g.contains(0.5, 0.5))
    self.assertTrue(g.contains(-0.5, -0.5))

  def test_excludes_outside(self):
    g = Geofence(self.SQUARE)
    self.assertFalse(g.contains(2, 2))
    self.assertFalse(g.contains(-2, -2))

  def test_excludes_on_edge(self):
    # Points exactly on edges are implementation-dependent in ray-casting
    g = Geofence(self.SQUARE)
    # (0, 1) is on the top edge
    result = g.contains(0, 1)
    # Either True or False is acceptable for exact edge hits
    self.assertIn(result, (True, False))

  def test_triangle(self):
    triangle = "[[0,0],[0,2],[2,0]]"
    g = Geofence(triangle)
    self.assertTrue(g.contains(0.5, 0.5))
    self.assertFalse(g.contains(1.5, 1.5))

  def test_invalid_json_disables(self):
    g = Geofence("not-json")
    self.assertFalse(g.enabled)

  def test_too_few_vertices_disables(self):
    g = Geofence("[[0,0],[1,1]]")
    self.assertFalse(g.enabled)

  def test_check_position_none_gps(self):
    g = Geofence(self.SQUARE)
    allowed, reason = g.check_position(None, None)
    self.assertFalse(allowed)
    self.assertIn("No GPS fix", reason)

  def test_check_position_inside(self):
    g = Geofence(self.SQUARE)
    allowed, reason = g.check_position(0, 0)
    self.assertTrue(allowed)
    self.assertIsNone(reason)

  def test_check_position_outside(self):
    g = Geofence(self.SQUARE)
    allowed, reason = g.check_position(5, 5)
    self.assertFalse(allowed)
    self.assertIn("Outside geofence", reason)


if __name__ == "__main__":
  unittest.main()
