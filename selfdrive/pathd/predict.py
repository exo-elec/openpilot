"""
predict.py — constant-velocity trajectory prediction.

Input:  list[TrackedCluster] from track.py
Output: list[PredictedCluster] with future positions

Simple constant-velocity model over a 3-second horizon at 0.5s steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openpilot.selfdrive.pathd.track import TrackedCluster

HORIZON_S   = 3.0   # prediction horizon (seconds)
STEP_S      = 0.5   # timestep


@dataclass
class PredictedCluster:
    track_id: int
    current_x: float     # lateral (m)
    current_z: float     # forward (m)
    future_x: list[float]  # predicted lateral positions
    future_z: list[float]  # predicted forward positions
    time_to_collision: float  # seconds until z crosses 0 (negative = behind)
    threat_level: int        # 0=none, 1=caution, 2=warning, 3=critical


def predict(tracks: list[TrackedCluster], v_ego: float = 0.0) -> list[PredictedCluster]:
    """
    Project each tracked cluster forward using constant-velocity model.
    v_ego: ego forward velocity (m/s) for relative kinematics.
    """
    results = []
    steps = [i * STEP_S for i in range(1, int(HORIZON_S / STEP_S) + 1)]

    for t in tracks:
        fx = [t.x + t.vx * s for s in steps]
        fz = [t.z + (t.vz - v_ego) * s for s in steps]  # relative to ego

        # Time to collision: when fz crosses 0 (object reaches ego)
        if t.vz - v_ego < -0.5:  # closing
            ttc = -t.z / (t.vz - v_ego)
        else:
            ttc = 999.0

        # Threat level based on TTC and lateral position
        if ttc < 1.5 and abs(t.x) < 2.0:
            threat = 3
        elif ttc < 3.0 and abs(t.x) < 2.5:
            threat = 2
        elif ttc < 5.0 and abs(t.x) < 3.0:
            threat = 1
        else:
            threat = 0

        results.append(PredictedCluster(
            track_id=t.track_id,
            current_x=t.x, current_z=t.z,
            future_x=fx, future_z=fz,
            time_to_collision=ttc,
            threat_level=threat,
        ))

    return results
