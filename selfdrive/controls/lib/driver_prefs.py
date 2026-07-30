#!/usr/bin/env python3
"""
Driver Preferences Controller

User-configurable driving preferences:
- Speed limit offset (+X kph over limit)
- Following distance (close/normal/far)
- Surface obstacle awareness (shock detection)

No NPU impact - pure parameter/logic enhancements.
"""
from __future__ import annotations

import logging

import cereal.messaging as messaging
import time
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.following_distance import TIME_GAPS

logger = logging.getLogger(__name__)


class DriverPrefs:
    """
    Driver preference settings controller.
    
    Integrates with longitudinal planner to provide:
    - Speed limit offsets
    - Following distance profiles
    
    Note: Surface obstacle speed limits are handled by SQSC controller
    (see selfdrive/controls/lib/sqsc.py)
    """
    
    def __init__(self):
        self.params = Params()
        
        # Cached parameters
        self.speed_offset_kph = 0
        self.following_distance = "normal"
        
        self._last_param_t = 0.0
        self.PARAM_REFRESH_S = 2.0

        self._read_params()

        logger.info("DriverPrefs initialized")

    def _read_params(self):
        """Read parameters from storage (cached, not every frame)."""
        now = time.monotonic()
        if now - self._last_param_t < self.PARAM_REFRESH_S:
            return
        self._last_param_t = now
        try:
            offset = self.params.get("EOPSpeedLimitOffset")
            if offset is not None:
                self.speed_offset_kph = int(offset)
        except (ValueError, TypeError):
            self.speed_offset_kph = 0

        try:
            profile = self.params.get("EOPFollowingDistance")
            if profile is not None:
                profile = profile.decode() if isinstance(profile, bytes) else profile
                if profile in TIME_GAPS:
                    self.following_distance = profile
        except Exception:
            self.following_distance = "normal"

    def update(self, sm: messaging.SubMaster):
        """Update state from messages."""
        self._read_params()
    
    def get_speed_with_offset(self, speed_limit_kph: float) -> float:
        """Apply user offset to speed limit."""
        return speed_limit_kph + self.speed_offset_kph
    
    def get_time_gap(self) -> float:
        """Get desired following distance time gap."""
        return TIME_GAPS.get(self.following_distance, 1.5)
