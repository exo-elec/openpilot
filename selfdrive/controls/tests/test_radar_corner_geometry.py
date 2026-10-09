"""BLE Radar2D corner-pose registry and transform tests."""

import numpy as np

from openpilot.selfdrive.controls.radar_corner_geometry import (
  CornerPose, corner_local_to_vehicle_frame, encode_corner_track_id,
  is_corner_track_id, load_corner_poses,
)


def _registry_yaml(confirmed: bool = True) -> str:
  corners = ('front_left', 'front_right', 'rear_left', 'rear_right')
  lines = ['corner_radars:']
  for index, name in enumerate(corners):
    lines += [
      f'  {name}:',
      f'    position: {{x_m: {1.0 + index}, y_m: {0.2 * index}, z_m: 0.6}}',
      '    rotation: {roll_deg: 1.0, pitch_deg: -2.0, yaw_deg: 30.0}',
      f'    confirmed: {str(confirmed).lower()}',
    ]
  return '\n'.join(lines)


def test_loads_full_confirmed_pose(tmp_path):
  path = tmp_path / 'sensor_calibration.yaml'
  path.write_text(_registry_yaml())
  poses = load_corner_poses(str(path))
  assert poses is not None
  assert poses[0] == CornerPose(1.0, 0.0, 0.6, 1.0, -2.0, 30.0, True)


def test_unconfirmed_pose_is_not_adas_geometry(tmp_path):
  path = tmp_path / 'sensor_calibration.yaml'
  path.write_text(_registry_yaml(confirmed=False))
  assert load_corner_poses(str(path)) is None
  assert load_corner_poses(str(path), require_confirmed=False) is not None


def test_one_uncalibrated_corner_does_not_disable_confirmed_corners(tmp_path):
  path = tmp_path / 'sensor_calibration.yaml'
  path.write_text(_registry_yaml().replace('    confirmed: true', '    confirmed: false', 1))
  poses = load_corner_poses(str(path))
  assert poses is not None
  assert set(poses) == {1, 2, 3}


def test_corner_track_ids_are_namespaced():
  left = encode_corner_track_id(0, 7)
  right = encode_corner_track_id(1, 7)
  assert left != right
  assert is_corner_track_id(left)
  assert is_corner_track_id(right)
  assert not is_corner_track_id(7)


def test_ble_2d_uses_shared_yaw_and_xy():
  pose = CornerPose(1.0, 2.0, 0.5, 4.0, -3.0, 90.0, True)
  assert np.allclose(corner_local_to_vehicle_frame(2.0, 0.0, pose), [1.0, 4.0])


def test_rear_pose_keeps_detection_behind_vehicle():
  pose = CornerPose(-2.0, 0.8, 0.5, 0.0, 0.0, 180.0, True)
  x_m, y_m = corner_local_to_vehicle_frame(3.0, 0.0, pose)
  assert x_m < 0.0
  np.testing.assert_allclose(y_m, 0.8, atol=1e-7)
