#!/usr/bin/env python3
"""
Following Distance Profiles

Configurable time gap profiles for longitudinal control.
No NPU impact, parameter-based.
"""
import time
from openpilot.common.params import Params

TIME_GAPS = {
    "close": 0.8,    # 0.8 seconds - sporty
    "normal": 1.5,   # 1.5 seconds - default
    "far": 2.2,      # 2.2 seconds - relaxed
}

class FollowingDistanceProfiles:
    """
    Three-profile following distance controller.

    Profiles:
        - close: 0.8s (aggressive)
        - normal: 1.5s (balanced, default)
        - far: 2.2s (conservative)

    Parameter: EOPFollowingDistance (str)
    """

    def __init__(self):
        self.params = Params()
        self.profile = "normal"
        self._last_param_t = 0.0
        self.PARAM_REFRESH_S = 2.0
        self._read_param()

    def _read_param(self):
        """Read profile from params (cached, not every frame)."""
        now = time.monotonic()
        if now - self._last_param_t < self.PARAM_REFRESH_S:
            return
        self._last_param_t = now
        profile = self.params.get("EOPFollowingDistance")
        if profile is not None:
            profile = profile.decode('utf-8') if isinstance(profile, bytes) else profile
            if profile in TIME_GAPS:
                self.profile = profile

    def update(self):
        """Update profile from params."""
        self._read_param()
    
    def get_time_gap(self) -> float:
        """Get current time gap in seconds."""
        return TIME_GAPS.get(self.profile, 1.5)
    
    def get_profile(self) -> str:
        """Get current profile name."""
        return self.profile
