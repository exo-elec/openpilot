#!/usr/bin/env python3
"""Camera geometry types for multi-camera systems."""

from dataclasses import dataclass
from enum import Enum


class CameraPosition(Enum):
    """Camera mounting positions."""
    ROAD = "road"           # Front main camera
    WIDE_ROAD = "wide_road" # Wide angle front
    DRIVER = "driver"       # Driver monitoring
    STEREO_LEFT = "stereo_left"
    STEREO_RIGHT = "stereo_right"
    TELE_ROAD = "tele_road"


@dataclass
class CameraGeometry:
    """Camera geometric configuration."""
    position: CameraPosition
    width: int
    height: int
    focal_length: float
    
    # Mounting position relative to vehicle center (meters)
    x_offset: float = 0.0  # Forward
    y_offset: float = 0.0  # Left
    z_offset: float = 0.0  # Up
    
    # Orientation (radians)
    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0
