"""TLSC — Traffic Light Speed Control.

Subscribes to stereoObjects (published by gridd at 20 Hz) to find red or
yellow traffic lights in the vehicle's path and compute a target speed that
brings the vehicle to a comfortable stop before the light.

Reference:
  Detection source: gridd YOLO class 9 (COCO "traffic light") + HSV
                    color classifier → stereoObjects cereal message.
  Speed calculation: constant-deceleration formula v = sqrt(2*a*d),
                     same pattern as VTSC/MTSC.

Activation conditions (all must be true):
  - EOPTLSCEnabled == 1
  - No lead vehicle present (radarState.leadOne.status == False)
  - A traffic light with state red or yellow at dRel in [MIN_DIST, MAX_DIST]
  - trafficLightConfidence >= CONFIDENCE_THRESHOLD

Integration: longitudinal_planner.py, same pattern as VTSC/MTSC.
"""
from __future__ import annotations

import math
import time
from typing import cast

from openpilot.common.params import Params

# Tuning
TLSC_DECEL      = 1.5    # m/s²  comfortable stop deceleration
TLSC_MIN_DIST   = 3.0    # m     below this, too close to engage
TLSC_MAX_DIST   = 80.0   # m     beyond this, light too far to act
TLSC_CONFIDENCE = 0.06   # frac  minimum trafficLightConfidence from classifier


class TLSC:
    """Traffic Light Speed Control.

    Call update() once per modelV2 cycle (20 Hz).
    Returns v_target (m/s) or None when inactive.
    """

    PARAM_REFRESH_S = 5.0

    def __init__(self) -> None:
        self._params = Params()
        self.enabled = self._params.get_bool("EOPTLSCEnabled")
        self._v_target: float | None = None
        self._last_param_t = 0.0

    def update(self, sm) -> float | None:
        """Compute speed target for active red/yellow traffic light.

        Returns v_target (m/s) or None when TLSC should not intervene.
        The caller should enforce v_target < v_cruise, same as VTSC/MTSC.
        """
        now = time.monotonic()
        if now - self._last_param_t >= self.PARAM_REFRESH_S:
            self._last_param_t = now
            self.enabled = self._params.get_bool("EOPTLSCEnabled")

        if not self.enabled:
            self._v_target = None
            return None

        # Skip when a lead vehicle is present — ACC handles that case
        if sm['radarState'].leadOne.status:
            self._v_target = None
            return None

        v_ego = sm['carState'].vEgo

        # Scan stereoObjects for the closest red or yellow traffic light
        best_dist: float | None = None
        for obj in sm['stereoObjects'].objects:
            state = str(obj.trafficLightState)
            if state not in ('red', 'yellow'):
                continue
            if float(obj.trafficLightConfidence) < TLSC_CONFIDENCE:
                continue
            d = float(obj.dRel)
            if d < TLSC_MIN_DIST or d > TLSC_MAX_DIST:
                continue
            if best_dist is None or d < best_dist:
                best_dist = d

        if best_dist is None:
            self._v_target = None
            return None

        # v = sqrt(2 * a * d): the speed at which the car, decelerating at
        # TLSC_DECEL, will reach 0 exactly at best_dist.
        v_target = math.sqrt(2.0 * TLSC_DECEL * best_dist)
        v_target = max(0.0, min(v_target, v_ego))
        self._v_target = v_target
        return cast(float | None, v_target)

    @property
    def active(self) -> bool:
        return self._v_target is not None
