"""TLSC - Traffic Light Speed Control (stop for a red or yellow light ahead at v = sqrt(2 * a * d)).

Moved from EOP10's `selfdrive/controls/lib/tlsc.py`, logic unchanged (golden-tested). Configuration is passed in; the input is
`lights = [(state, confidence, dRel), ...]` instead of reading a SubMaster, so any perception layer can feed it. Light-to-lane
association is NOT part of this core (the original had none): `runtime/map_speed.py` filters lights to the planned path first.
Pure: no cereal, no Params.
"""
from __future__ import annotations

import math
from typing import cast

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

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._v_target: float | None = None

    def update_from(self, lead_present: bool, v_ego: float, lights) -> float | None:
        """Compute speed target for active red/yellow traffic light.

        Returns v_target (m/s) or None when TLSC should not intervene.
        The caller should enforce v_target < v_cruise, same as VTSC/MTSC.
        """
        if not self.enabled:
            self._v_target = None
            return None

        # Skip when a lead vehicle is present — ACC handles that case
        if lead_present:
            self._v_target = None
            return None

        # Scan stereoObjects for the closest red or yellow traffic light
        best_dist: float | None = None
        for state, confidence, d in lights:
            if str(state) not in ('red', 'yellow'):
                continue
            if float(confidence) < TLSC_CONFIDENCE:
                continue
            d = float(d)
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
