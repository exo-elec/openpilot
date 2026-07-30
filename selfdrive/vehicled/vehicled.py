#!/usr/bin/env python3
"""vehicled - Vehicle daemon (wrapper for car/card.py)

This module wraps car/card.py for process naming consistency.
"""
from openpilot.selfdrive.vehicled.car.card import (
  Car,
  main,
  DT_CTRL,
  REPLAY,
  EventName,
)

# Alias for compatibility
Vehicle = Car

__all__ = ['Car', 'Vehicle', 'main', 'DT_CTRL', 'REPLAY', 'EventName']
