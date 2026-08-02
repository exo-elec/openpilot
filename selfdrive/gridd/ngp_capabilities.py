"""Camera/feature capability contract shared by NGP10 and EOP10.

This module intentionally contains no camera I/O or hardware assumptions.  It
lets application code expose GridD/SOC/overlay interfaces while comma 3 safely
falls back when only its road cameras are present.
"""

from dataclasses import dataclass
from enum import Enum


class CameraRole(str, Enum):
  ROAD = "road"
  WIDE_ROAD = "wide_road"
  DRIVER = "driver"
  SIDE_LEFT = "side_left"
  SIDE_RIGHT = "side_right"
  REAR = "rear"


class Feature(str, Enum):
  GRID = "gridd"
  SOC = "soc"
  SIDE_OVERLAY = "side_overlay"
  REAR_OVERLAY = "rear_overlay"
  MONOD = "monod"
  STEREO = "stereo"


@dataclass(frozen=True)
class NGP10Capabilities:
  """Declared streams and optional compute support for one device."""

  cameras: frozenset[CameraRole] = frozenset((CameraRole.ROAD, CameraRole.WIDE_ROAD))
  driver_camera: bool = False
  depth_backend: bool = False
  accelerator_backend: bool = False

  @classmethod
  def comma3(cls, driver_camera: bool = False) -> "NGP10Capabilities":
    return cls(driver_camera=driver_camera)

  def has_camera(self, role: CameraRole) -> bool:
    return role in self.cameras or (role is CameraRole.DRIVER and self.driver_camera)

  def supports(self, feature: Feature) -> bool:
    if feature is Feature.GRID:
      return self.has_camera(CameraRole.ROAD) and self.has_camera(CameraRole.WIDE_ROAD)
    if feature is Feature.SOC:
      return self.supports(Feature.GRID)
    if feature is Feature.SIDE_OVERLAY:
      return self.has_camera(CameraRole.SIDE_LEFT) and self.has_camera(CameraRole.SIDE_RIGHT)
    if feature is Feature.REAR_OVERLAY:
      return self.has_camera(CameraRole.REAR)
    if feature is Feature.MONOD:
      # MonoD is a single-camera model and may run in shadow mode on comma 3.
      return self.has_camera(CameraRole.ROAD)
    if feature is Feature.STEREO:
      # StereoD is never enabled by this two-road-camera contract.
      return False
    return False

  def available_features(self) -> frozenset[Feature]:
    return frozenset(feature for feature in Feature if self.supports(feature))
