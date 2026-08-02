"""Default-off single-camera detector adapter for original comma 3.

The backend may report 2D detections and independently calibrated metric
positions. NGP10 never estimates metric depth from bounding-box size alone.
"""

from dataclasses import dataclass
from typing import Protocol

from openpilot.selfdrive.gridd.ngp_capabilities import CameraRole, Feature, NGP10Capabilities


@dataclass(frozen=True)
class MonoDetection:
  detection_id: int
  class_name: str
  confidence: float
  box_xyxy: tuple[float, float, float, float]
  d_rel: float | None = None
  y_rel: float | None = None
  metric_valid: bool = False


@dataclass(frozen=True)
class MonoDResult:
  frame_id: int
  timestamp_sof: int
  camera: CameraRole
  detections: tuple[MonoDetection, ...]
  available: bool
  reason: str
  control_authority: bool = False


class MonoDBackend(Protocol):
  def infer(self, frame) -> list[dict]: ...


class NGP10MonoD:
  def __init__(self, enabled: bool = False, backend: MonoDBackend | None = None,
               min_confidence: float = 0.5):
    self.enabled = enabled
    self.backend = backend
    self.min_confidence = max(0.0, min(1.0, float(min_confidence)))

  def update(self, frame, frame_id: int, timestamp_sof: int,
             capabilities: NGP10Capabilities, calibration_valid: bool) -> MonoDResult:
    if not self.enabled:
      return MonoDResult(frame_id, timestamp_sof, CameraRole.ROAD, (), False, "disabled")
    if not capabilities.supports(Feature.MONOD):
      return MonoDResult(frame_id, timestamp_sof, CameraRole.ROAD, (), False, "road_camera_unavailable")
    if self.backend is None:
      return MonoDResult(frame_id, timestamp_sof, CameraRole.ROAD, (), False, "backend_unavailable")

    detections = []
    for index, raw in enumerate(self.backend.infer(frame) or ()):
      confidence = float(raw.get("confidence", 0.0))
      box = tuple(float(value) for value in raw.get("box_xyxy", ()))
      if confidence < self.min_confidence or len(box) != 4:
        continue
      has_metric = calibration_valid and raw.get("d_rel") is not None and raw.get("y_rel") is not None
      detections.append(MonoDetection(
        detection_id=int(raw.get("detection_id", index)),
        class_name=str(raw.get("class_name", "unknown")),
        confidence=confidence,
        box_xyxy=box,
        d_rel=float(raw["d_rel"]) if has_metric else None,
        y_rel=float(raw["y_rel"]) if has_metric else None,
        metric_valid=has_metric,
      ))
    return MonoDResult(frame_id, timestamp_sof, CameraRole.ROAD, tuple(detections), True, "shadow")
