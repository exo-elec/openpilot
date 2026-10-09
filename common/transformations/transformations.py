"""Temporary stub for transformations Cython extension.
Delete this file before building with SCons — the real .so will replace it."""
import numpy as np


def euler2quat_single(e):
  cr, cp, cy = np.cos(e[2] * 0.5), np.cos(e[1] * 0.5), np.cos(e[0] * 0.5)
  sr, sp, sy = np.sin(e[2] * 0.5), np.sin(e[1] * 0.5), np.sin(e[0] * 0.5)
  return np.array([
    cr * cp * cy + sr * sp * sy,
    sr * cp * cy - cr * sp * sy,
    cr * sp * cy + sr * cp * sy,
    cr * cp * sy - sr * sp * cy,
  ])


def quat2euler_single(q):
  return np.array([
    np.arctan2(2.0 * (q[0] * q[1] + q[2] * q[3]), 1.0 - 2.0 * (q[1]**2 + q[2]**2)),
    np.arcsin(2.0 * (q[0] * q[2] - q[3] * q[1])),
    np.arctan2(2.0 * (q[0] * q[3] + q[1] * q[2]), 1.0 - 2.0 * (q[2]**2 + q[3]**2)),
  ])


def quat2rot_single(q):
  return euler2rot_single(quat2euler_single(q))


def rot2quat_single(r):
  return euler2quat_single(rot2euler_single(r))


def euler2rot_single(e):
  cr, sr = np.cos(e[2]), np.sin(e[2])
  cp, sp = np.cos(e[1]), np.sin(e[1])
  cy, sy = np.cos(e[0]), np.sin(e[0])
  return np.array([
    [cy * cp, sy * cp, -sp],
    [-sy * cr + cy * sp * sr, cy * cr + sy * sp * sr, cp * sr],
    [sy * sr + cy * sp * cr, -cy * sr + sy * sp * cr, cp * cr],
  ])


def rot2euler_single(r):
  return np.array([
    np.arctan2(r[1, 0], r[0, 0]),
    np.arcsin(-r[2, 0]),
    np.arctan2(r[2, 1], r[2, 2]),
  ])


def ecef_euler_from_ned_single(*args):
  raise NotImplementedError("Use compiled Cython extension — run SCons build first")


def ned_euler_from_ecef_single(*args):
  raise NotImplementedError("Use compiled Cython extension — run SCons build first")


def geodetic2ecef(*args):
  raise NotImplementedError("Use compiled Cython extension — run SCons build first")


def geodetic2ecef_single(*args):
  raise NotImplementedError("Use compiled Cython extension — run SCons build first")


def ecef2geodetic_single(*args):
  raise NotImplementedError("Use compiled Cython extension — run SCons build first")


def ecef2geodetic(*args):
  raise NotImplementedError("Use compiled Cython extension — run SCons build first")


class LocalCoord:
  """Stub for LocalCoord — requires compiled Cython extension."""
  def __init__(self, *args, **kwargs):
    raise NotImplementedError("LocalCoord requires compiled Cython extension — run SCons build first")

  def ecef2ned_single(self, *args):
    raise NotImplementedError("Use compiled Cython extension — run SCons build first")

  def ned2ecef_single(self, *args):
    raise NotImplementedError("Use compiled Cython extension — run SCons build first")

  def geodetic2ned_single(self, *args):
    raise NotImplementedError("Use compiled Cython extension — run SCons build first")

  def ned2geodetic_single(self, *args):
    raise NotImplementedError("Use compiled Cython extension — run SCons build first")
