# Coordination Daemon - ECEF position fusion with road constraints
"""
coordinationd - Coordination Daemon for openpilot.

Coordinates odometry + GPS + OSM/SGM constraints into fused ECEF position.
"""

from exopilot.selfdrive.coordinationd.osm_localizer import (
    OSMLocalizerModule,
    MapMatchResult,
    RoadConstraint,
)

from exopilot.selfdrive.coordinationd.sgm_localizer import (
    SGMLocalizerModule,
    SGMMapTile,
    PointCloudFrame,
    ICPMatchResult,
)

from exopilot.selfdrive.coordinationd.fusion import (
    FusionEngine,
    PositionEstimate,
    GNSSMeasurement,
)

__all__ = [
    # OSM Localizer
    'OSMLocalizerModule',
    'MapMatchResult',
    'RoadConstraint',
    # SGM Localizer
    'SGMLocalizerModule',
    'SGMMapTile',
    'PointCloudFrame',
    'ICPMatchResult',
    # Fusion
    'FusionEngine',
    'PositionEstimate',
    'GNSSMeasurement',
]
