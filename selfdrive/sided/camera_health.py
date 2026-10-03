#!/usr/bin/env python3
"""Per-camera frame-alive health monitor for side/rear camera daemons.

Tracks the last time each enabled camera produced a frame and reports a fault
when a camera has been silent for longer than ``timeout_s``. A short startup
grace period avoids spurious faults while VisionIPC connects.
"""

from __future__ import annotations

import time


class CameraHealthTracker:
    """Detect disconnected/malfunctioning cameras from frame arrivals."""

    def __init__(self, timeout_s: float = 2.0, startup_grace_s: float = 2.0):
        self.timeout_s = timeout_s
        self.startup_grace_s = startup_grace_s
        self._start_t = time.monotonic()
        self._last_frame_t: dict[str, float] = {}

    def mark_frame(self, camera: str) -> None:
        """Record that ``camera`` produced a frame this iteration."""
        self._last_frame_t[camera] = time.monotonic()

    def check(self, enabled_cameras: list[str]) -> tuple[bool, str]:
        """Return (fault, reason) for any enabled camera that has gone silent."""
        now = time.monotonic()
        if now - self._start_t < self.startup_grace_s:
            return False, ""

        dead: list[str] = []
        for camera in enabled_cameras:
            last = self._last_frame_t.get(camera, 0.0)
            if now - last > self.timeout_s:
                dead.append(camera)

        if not dead:
            return False, ""
        return True, f"camera_disconnected:{','.join(dead)}"

    def reset(self) -> None:
        """Clear history (e.g. after ignition cycle)."""
        self._start_t = time.monotonic()
        self._last_frame_t.clear()
