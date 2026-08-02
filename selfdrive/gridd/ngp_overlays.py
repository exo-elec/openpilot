"""Capability-gated diagnostic overlay selection."""

from dataclasses import dataclass
from enum import IntEnum

from openpilot.selfdrive.gridd.ngp_capabilities import CameraRole, NGPCapabilities


class OverlaySide(IntEnum):
  LEFT = 0
  RIGHT = 1
  REAR = 2


@dataclass(frozen=True)
class OverlaySelection:
  side: OverlaySide
  camera: CameraRole
  native_stream: bool
  diagnostic_only: bool = True
  control_authority: bool = False


def select_overlay(capabilities: NGPCapabilities, side: OverlaySide) -> OverlaySelection:
  requested = {
    OverlaySide.LEFT: CameraRole.SIDE_LEFT,
    OverlaySide.RIGHT: CameraRole.SIDE_RIGHT,
    OverlaySide.REAR: CameraRole.REAR,
  }[side]
  if capabilities.has_camera(requested):
    return OverlaySelection(side, requested, True)
  fallback = CameraRole.WIDE_ROAD if capabilities.has_camera(CameraRole.WIDE_ROAD) else CameraRole.ROAD
  return OverlaySelection(side, fallback, False)
