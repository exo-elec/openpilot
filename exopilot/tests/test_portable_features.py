from exopilot.system.hardware.camera_capabilities import CameraRole, CameraCapabilities
from exopilot.selfdrive.ui.overlays import OverlaySide, select_overlay
from exopilot.selfdrive.controls.lib.route_curvature import RoutePoint, route_curvatures
from exopilot.selfdrive.monod.mono_detector import MonoDetector
from exopilot.selfdrive.tripd.trip_accumulator import TripAccumulator


class DetectorBackend:
  def infer(self, _frame):
    return [
      {"class_name": "car", "confidence": 0.9, "box_xyxy": (1, 2, 3, 4), "d_rel": 20, "y_rel": 3},
      {"class_name": "noise", "confidence": 0.1, "box_xyxy": (0, 0, 1, 1)},
    ]


def test_monod_requires_backend_and_keeps_uncalibrated_detection_non_metric():
  caps = CameraCapabilities.comma3()
  unavailable = MonoDetector(enabled=True).update(None, 1, 2, caps, calibration_valid=True)
  assert not unavailable.available
  result = MonoDetector(enabled=True, backend=DetectorBackend()).update(None, 1, 2, caps, calibration_valid=False)
  assert result.available and len(result.detections) == 1
  assert not result.detections[0].metric_valid
  assert result.detections[0].d_rel is None


def test_overlay_falls_back_and_route_curvature_is_optional():
  overlay = select_overlay(CameraCapabilities.comma3(), OverlaySide.LEFT)
  assert overlay.camera is CameraRole.WIDE_ROAD
  assert not overlay.native_stream and overlay.diagnostic_only

  points = (
    RoutePoint(13.0000, 100.0000),
    RoutePoint(13.0010, 100.0000),
    RoutePoint(13.0018, 100.0007),
    RoutePoint(13.0020, 100.0017),
  )
  assert route_curvatures(points)
  assert route_curvatures(points[:2]) == ()


def test_trip_accumulator():
  trip = TripAccumulator()
  first = trip.update(10.0, 1.0, True, False, 0.5)
  second = trip.update(10.0, 2.0, False, True, 0.5)
  assert first.distance_m == 5.0
  assert second.distance_m == 10.0
  assert second.engagement_ratio == 0.5
