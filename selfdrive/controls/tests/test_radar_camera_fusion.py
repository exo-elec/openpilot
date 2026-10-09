"""Side/rear YOLO and BLE Radar2D association tests."""

from types import SimpleNamespace

import pytest

from openpilot.selfdrive.controls.lib.radar_zones import RadarZoneMonitor, ZoneAlertLevel
from openpilot.selfdrive.controls.radar_corner_geometry import encode_corner_track_id


def _det(track_id: int, x: float, y: float, source: str = 'side_left'):
  return SimpleNamespace(
    trackId=track_id, className='car', confidence=0.8,
    x=x, y=y, vx=-9.0, sigmaX=2.5, sigmaY=1.5,
    cameraSource=source,
  )


def _radar(corner: int, track_id: int, x: float, y: float, v_rel: float):
  return SimpleNamespace(
    trackId=encode_corner_track_id(corner, track_id), dRel=x, yRel=y,
    vRel=v_rel, prob=0.9, laneZone=2 if y > 0.0 else 3,
  )


def _carstate(v_ego: float = 0.0, gear: str = 'drive'):
  return SimpleNamespace(
    leftBlindspot=False, rightBlindspot=False,
    vEgo=v_ego, gearShifter=gear,
  )


def test_ble_kinematics_enrich_matching_yolo_track():
  monitor = RadarZoneMonitor()
  side = SimpleNamespace(detections=[_det(17, -5.5, 2.8)])
  stereo = SimpleNamespace(objects=[_radar(2, 4, -5.0, 3.0, -4.0)])

  monitor.update(stereo, _carstate(), side, None, 10.0)

  assert len(monitor.last_fused_objects) == 1
  fused = monitor.last_fused_objects[0]
  assert fused['dRel'] == pytest.approx(-5.0)
  assert fused['vRel'] == pytest.approx(-4.0)
  assert fused['className'] == 'car'
  assert fused['cameraTrackId'] == 17
  assert fused['cameraAssociated']


def test_disconnected_ble_degrades_to_camera_caution_not_doppler_warning():
  monitor = RadarZoneMonitor()
  side = SimpleNamespace(detections=[_det(23, -6.0, -3.0, 'side_right')])

  _, right, _ = monitor.update(None, _carstate(), side, None, 20.0)

  assert len(monitor.last_fused_objects) == 1
  assert monitor.last_fused_objects[0]['source'] == 'side_camera'
  assert monitor.last_fused_objects[0]['vRel'] == 0.0
  assert right.detected
  assert right.alert_level == ZoneAlertLevel.CAUTION


def test_stale_camera_track_is_not_kept_after_disconnect_timeout():
  monitor = RadarZoneMonitor()
  side = SimpleNamespace(detections=[_det(9, -4.0, 3.0)])
  monitor.update(None, _carstate(), side, None, 1.0)

  monitor.update(None, _carstate(), None, None, 2.1)

  assert monitor.last_fused_objects == []


def test_rear_camera_matches_only_rear_ble_track():
  monitor = RadarZoneMonitor()
  rear = SimpleNamespace(detections=[_det(31, -8.0, 0.5, 'rear')])
  stereo = SimpleNamespace(objects=[
    _radar(0, 1, 4.0, 0.5, -2.0),
    _radar(2, 2, -8.5, 0.7, -3.0),
  ])

  monitor.update(stereo, _carstate(), None, rear, 5.0)

  associated = [o for o in monitor.last_fused_objects if o.get('cameraAssociated')]
  assert len(associated) == 1
  assert associated[0]['dRel'] == pytest.approx(-8.5)


def test_corner_radar_does_not_generate_forward_collision_warning():
  monitor = RadarZoneMonitor()
  stereo = SimpleNamespace(objects=[_radar(0, 8, 10.0, 0.5, -5.0)])

  monitor.update(stereo, _carstate(v_ego=8.0), None, None, 1.0)

  assert not monitor.fcw_state.detected
  assert monitor.fcw_state.alert_level == ZoneAlertLevel.OFF


def test_front_corners_cover_low_speed_bumper_blind_zone_without_fcw():
  monitor = RadarZoneMonitor()
  stereo = SimpleNamespace(objects=[
    _radar(0, 8, 2.5, 0.8, -0.5),
    _radar(1, 9, 2.7, -0.8, -0.4),
  ])

  monitor.update(stereo, _carstate(v_ego=1.0), None, None, 1.0)

  assert not monitor.fcw_state.detected
  assert monitor.near_front_state.alert_level == ZoneAlertLevel.WARNING
  assert monitor.alert_message() == 'Front obstacle very close'


def test_near_front_corner_warning_is_disabled_at_road_speed():
  monitor = RadarZoneMonitor()
  stereo = SimpleNamespace(objects=[
    _radar(0, 8, 2.5, 0.8, -5.0),
    _radar(1, 9, 2.7, -0.8, -5.0),
  ])

  monitor.update(stereo, _carstate(v_ego=10.0), None, None, 1.0)

  assert not monitor.near_front_state.detected


def test_rear_corner_radar_generates_rear_collision_warning():
  monitor = RadarZoneMonitor()
  stereo = SimpleNamespace(objects=[_radar(2, 8, -10.0, 0.5, -5.0)])

  monitor.update(stereo, _carstate(v_ego=8.0), None, None, 1.0)

  assert monitor.rcw_state.alert_level == ZoneAlertLevel.WARNING
  assert monitor.rcw_state.ttc_s == pytest.approx(2.0)
  assert monitor.alert_message() == 'Rear collision warning'


def test_front_cross_traffic_uses_track_motion_and_reports_origin_side():
  monitor = RadarZoneMonitor()
  first = SimpleNamespace(objects=[_radar(0, 12, 5.0, 4.0, -1.0)])
  second = SimpleNamespace(objects=[_radar(0, 12, 5.0, 3.5, -1.0)])
  carstate = _carstate(v_ego=1.0)

  monitor.update(first, carstate, None, None, 1.0)
  monitor.update(second, carstate, None, None, 1.1)

  assert monitor.fcta_state.detected
  assert monitor.fcta_state.side.value == 'left'
  assert monitor.fcta_state.alert_level == ZoneAlertLevel.WARNING


def test_rear_cross_traffic_requires_reverse():
  monitor = RadarZoneMonitor()
  first = SimpleNamespace(objects=[_radar(2, 14, -6.0, -4.0, -1.0)])
  second = SimpleNamespace(objects=[_radar(2, 14, -6.0, -3.5, -1.0)])

  monitor.update(first, _carstate(gear='drive'), None, None, 1.0)
  monitor.update(second, _carstate(gear='drive'), None, None, 1.1)
  assert not monitor.rcta_state.detected

  monitor = RadarZoneMonitor()
  monitor.update(first, _carstate(gear='reverse'), None, None, 1.0)
  monitor.update(second, _carstate(gear='reverse'), None, None, 1.1)
  assert monitor.rcta_state.detected
  assert monitor.rcta_state.side.value == 'right'
