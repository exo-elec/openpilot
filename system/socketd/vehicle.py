"""Socketd-owned vehicle adapter.

The vehicle loop now runs inside socketd so SocketCAN transport, OpenDBC Tesla
parsing, BrownPanda safety, and CAN TX share one process boundary.  The import
keeps the existing adapter implementation reusable while the remaining
vehicled package is migrated module-by-module.
"""

from openpilot.selfdrive.vehicled.car.card import Car, main

__all__ = ["Car", "main"]
