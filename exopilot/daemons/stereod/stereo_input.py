"""Stereo input checks independent of hardware and messaging imports."""
import hashlib

import numpy as np


def calibration_id(cal) -> str:
  """Fingerprint the rectified optical frame, metric geometry and image maps."""
  digest = hashlib.sha256()
  for name in ('Q', 'T', 'R1', 'left_map1', 'left_map2', 'right_map1', 'right_map2'):
    value = np.ascontiguousarray(getattr(cal, name), dtype='<f8')
    if not value.size or not np.isfinite(value).all():
      raise ValueError(f'Missing/non-finite factory calibration field: {name}')
    if name == 'R1' and (value.shape != (3, 3) or
                         not np.allclose(value @ value.T, np.eye(3), atol=1e-4) or
                         not np.isclose(np.linalg.det(value), 1, atol=1e-4)):
      raise ValueError('Rectified frame requires a measured proper R1 rotation')
    digest.update(name.encode('ascii'))
    digest.update(str(value.shape).encode('ascii'))
    digest.update(value.tobytes())
  return digest.hexdigest()


def luma_plane(data, width: int, height: int, stride: int) -> np.ndarray:
  """Read padded NV12 Y rows without treating padding as image pixels."""
  if width < 1 or height < 1 or stride < width:
    raise ValueError('Invalid NV12 dimensions/stride')
  plane = np.frombuffer(data, dtype=np.uint8)
  if plane.size < stride * height:
    raise ValueError('Truncated NV12 luma plane')
  return plane[:stride*height].reshape(height, stride)[:, :width]


def capture_timestamp(left: int, right: int, now: int, previous: int = 0) -> int:
  """Require fresh paired monotonic captures; never replace capture time with publish time."""
  if min(left, right) <= 0 or max(left, right) > now:
    raise ValueError('Missing/future stereo capture timestamp')
  if abs(left-right) > 5_000_000:
    raise ValueError('Stereo capture skew exceeds 5 ms')
  stamp = min(left, right)
  if now-stamp > 150_000_000 or stamp <= previous:
    raise ValueError('Stale or duplicate stereo capture')
  return stamp


def calibration_maps(cal, width: int, height: int):
  """Require measured maps at this exact image size and metric, forward-positive Q."""
  if cal is None:
    raise ValueError('Factory stereo calibration is missing')
  maps = []
  for name in ('left_map1', 'left_map2', 'right_map1', 'right_map2'):
    value = np.asarray(getattr(cal, name, None))
    if value.shape != (height, width) or not np.issubdtype(value.dtype, np.floating) or not np.isfinite(value).all():
      raise ValueError(f'{name} must be a finite float map at capture resolution')
    maps.append(value.astype(np.float32))
  q = np.asarray(cal.Q, dtype=np.float64)
  translation = np.asarray(cal.T, dtype=np.float64)
  if q.shape != (4, 4) or not np.isfinite(q).all() or np.linalg.matrix_rank(q) != 4:
    raise ValueError('Invalid stereo Q matrix')
  baseline = np.linalg.norm(translation)
  if translation.size != 3 or not np.isfinite(translation).all() or not 0.005 <= baseline <= 2.0:
    raise ValueError('Stereo translation must be measured in meters')
  if q[3, 2] == 0 or not np.isclose(abs(q[0, 0]/q[3, 2]), baseline, rtol=0.01, atol=1e-6):
    raise ValueError('Stereo Q scale disagrees with the measured metric baseline')
  probe = q @ np.array([width/2, height/2, 16.0, 1.0])
  if probe[3] == 0 or not 0 < probe[2]/probe[3] < 100_000:
    raise ValueError('Stereo Q must give forward-positive metric depth')
  # Map footprint support is distinct from matching confidence.
  support = []
  for mx, my in ((maps[0], maps[1]), (maps[2], maps[3])):
    footprint = (mx >= 0) & (mx < width-1) & (my >= 0) & (my < height-1)
    # A census descriptor uses all 25 remapped pixels, not only its center.
    valid = np.zeros_like(footprint)
    if height >= 5 and width >= 5:
      windows = np.lib.stride_tricks.sliding_window_view(footprint, (5, 5))
      valid[2:-2, 2:-2] = windows.all(axis=(-1, -2))
    support.append(valid)
  return (*maps, q, *support)


def measured_mount(path, expected_id):
  """Application-owned stereo_extrinsics.npz, rectified optical -> footprint FLU.

  A ground plane is a unit, upward normal and offset in footprint coordinates.
  No mounting defaults are accepted. Binding to factory calibration prevents
  applying physical-left-camera extrinsics to a different rectified frame.
  """
  with np.load(path, allow_pickle=False) as data:
    if str(data['calibration_id'].item()) != expected_id:
      raise ValueError('Stereo mounting belongs to different factory calibration')
    transform = np.asarray(data['rectified_optical_to_footprint'], dtype=np.float32)
    plane = np.asarray(data['ground_plane'], dtype=np.float32)
  if transform.shape != (4, 4) or not np.isfinite(transform).all():
    raise ValueError('Expected a finite measured 4x4 stereo mounting')
  rotation = transform[:3, :3]
  if (not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-5) or
      not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-4) or
      not np.isclose(np.linalg.det(rotation), 1, atol=1e-4)):
    raise ValueError('Stereo mounting must be a rigid right-handed transform')
  if not 0.2 <= transform[2, 3] <= 3.0:
    raise ValueError('Measured stereo camera height is outside the mounting envelope')
  if (plane.shape != (4,) or not np.isfinite(plane).all() or
      not np.isclose(np.linalg.norm(plane[:3]), 1, atol=1e-4) or plane[2] < 0.96 or abs(plane[3]) > 0.2):
    raise ValueError('Expected an upward local ground plane in base_footprint')
  return transform, plane
