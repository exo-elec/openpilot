from openpilot.selfdrive.gridd.lazy_bev import NGPLazyBEV
from openpilot.selfdrive.gridd.ngp_capabilities import CameraRole, NGPCapabilities
from openpilot.selfdrive.gridd.ngp_overlays import OverlaySide, select_overlay
from openpilot.selfdrive.mapd.ngp_curvature import RoutePoint, route_curvatures
from openpilot.selfdrive.monod.ngp_monod import NGPMonoD
from openpilot.selfdrive.pathd.ngp_soc import NGPSOC, SOCInput
from openpilot.selfdrive.tripd.ngp_trip import NGPTripStats


class DetectorBackend:
  def infer(self, _frame):
    return [
      {"class_name": "car", "confidence": 0.9, "box_xyxy": (1, 2, 3, 4), "d_rel": 20, "y_rel": 3},
      {"class_name": "noise", "confidence": 0.1, "box_xyxy": (0, 0, 1, 1)},
    ]


def test_lazy_bev_requires_metric_observations():
  caps = NGPCapabilities.comma3()
  bev = NGPLazyBEV()
  assert not bev.update((), caps, metric_observations=False).available
  observation = type("Obs", (), {"d_rel": 20.0, "y_rel": 3.0, "probability": 0.9, "track_id": 4})()
  result = bev.update((observation,), caps, metric_observations=True)
  assert result.available and len(result.cells) == 1
  assert not result.control_authority


def test_monod_requires_backend_and_keeps_uncalibrated_detection_non_metric():
  caps = NGPCapabilities.comma3()
  unavailable = NGPMonoD(enabled=True).update(None, 1, 2, caps, calibration_valid=True)
  assert not unavailable.available
  result = NGPMonoD(enabled=True, backend=DetectorBackend()).update(None, 1, 2, caps, calibration_valid=False)
  assert result.available and len(result.detections) == 1
  assert not result.detections[0].metric_valid
  assert result.detections[0].d_rel is None


def test_soc_needs_sustained_highway_geometry_and_one_sided_threat():
  line = lambda y: tuple([y] * 10)  # noqa: E731
  sample = SOCInput(
    25.0, True, False,
    (line(-5.25), line(-1.75), line(1.75), line(5.25)),
    (0.9, 0.9, 0.9, 0.9),
    (0.1, 0.1, 0.1, 0.1),
  )
  soc = NGPSOC(confirmation_frames=2)
  assert not soc.update(sample).active_suggestion
  result = soc.update(sample)
  assert result.active_suggestion and result.offset_m == -0.2
  assert not result.control_authority


def test_overlay_falls_back_and_route_curvature_is_optional():
  overlay = select_overlay(NGPCapabilities.comma3(), OverlaySide.LEFT)
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
  trip = NGPTripStats()
  first = trip.update(10.0, 1.0, True, False, 0.5)
  second = trip.update(10.0, 2.0, False, True, 0.5)
  assert first.distance_m == 5.0
  assert second.distance_m == 10.0
  assert second.engagement_ratio == 0.5
