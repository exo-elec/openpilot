from openpilot.selfdrive.gridd.ngp_capabilities import CameraRole, Feature, NGPCapabilities


def test_comma3_road_features_and_safe_fallbacks():
  caps = NGPCapabilities.comma3(driver_camera=False)
  assert caps.supports(Feature.GRID)
  assert caps.supports(Feature.SOC)
  assert not caps.supports(Feature.SIDE_OVERLAY)
  assert not caps.supports(Feature.REAR_OVERLAY)
  assert caps.supports(Feature.MONOD)
  assert not caps.supports(Feature.STEREO)


def test_driver_camera_is_monitoring_only():
  caps = NGPCapabilities.comma3(driver_camera=True)
  assert caps.has_camera(CameraRole.DRIVER)
  assert caps.supports(Feature.GRID)
  assert not caps.supports(Feature.SIDE_OVERLAY)


def test_eop_side_rear_capability_requires_streams():
  caps = NGPCapabilities(
    cameras=frozenset(CameraRole), driver_camera=True,
    depth_backend=True, accelerator_backend=True,
  )
  assert caps.supports(Feature.SIDE_OVERLAY)
  assert caps.supports(Feature.REAR_OVERLAY)
  assert caps.supports(Feature.MONOD)
  assert not caps.supports(Feature.STEREO)
