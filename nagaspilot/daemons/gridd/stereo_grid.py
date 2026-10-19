"""GPU-only stereo endpoint mapping, with measured geometry and honest unknowns.

The Autoware boundary is raw/ground/obstacle separation before probabilistic
fusion. This conservative camera adaptation marks only observed endpoints;
it does not claim LiDAR ray clearing, ICP, or unobserved drivable space.
"""
from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np

from openpilot.nagaspilot.daemons.stereod.stereo_input import calibration_id, calibration_maps, measured_mount
from openpilot.system.inferenced.opencl_reproject import OpenCLReprojector


@dataclass
class StereoObservation:
  points: np.ndarray  # obstacle points only, vehicle-aligned right/down/forward
  occupied: np.ndarray
  ground: np.ndarray


class GPUGridMapper:
  """Bounded OpenCL projection/classification; no CPU geometry substitute."""
  def __init__(self, gpu, spec, transform, plane):
    self.gpu, self.spec = gpu, spec
    self.transform, self.plane = transform, plane
    self.projector = OpenCLReprojector(gpu)
    self.buffers = []
    self.shape = None
    try:
      program = gpu.build(Path(__file__).resolve().parents[3].joinpath('system/inferenced/kernels/stereo_grid.cl').read_text())
      self.classify = gpu.cl.Kernel(program, 'classify_stereo')
      self.raster = gpu.cl.Kernel(program, 'raster_stereo')
      self.extract = gpu.cl.Kernel(program, 'obstacle_cell_points')
    except Exception:
      self.projector.close()
      raise

  def close(self):
    with self.gpu.lock:
      self.gpu.queue.finish()
      for buffer in self.buffers:
        buffer.release()
      self.buffers = []
      self.shape = None
    self.projector.close()

  def observe(self, disparity, confidence, q):
    started = time.monotonic()
    h, w = disparity.shape
    n, spec, gpu = h*w, self.spec, self.gpu
    cell_count = spec.rows*spec.cols
    sizes = [12*n, 12*n, 4*n, 4*w, 4*cell_count, 64, 16, 4*cell_count, 12*cell_count]
    # Include the retained full-Q buffers in the combined working-set budget.
    gpu.check_allocations(sizes + [4*n, 4*n, 128, 12*n])
    optical = self.projector.reproject(disparity, q, confidence)
    points = np.empty((cell_count, 3), np.float32)
    cells = np.zeros((spec.rows, spec.cols), np.int32)
    representatives = np.full(cell_count, -1, np.int32)
    nearest = np.full(w, np.iinfo(np.int32).max, np.int32)
    with gpu.lock:
      if self.shape != (h, w):
        gpu.queue.finish()
        for buffer in self.buffers:
          buffer.release()
        self.buffers, self.shape = [], None
        for size in sizes:
          self.buffers.append(gpu.cl.Buffer(gpu.context, gpu.cl.mem_flags.READ_WRITE, size))
        self.shape = (h, w)
      ob, ab, lb, nb, cb, tb, pb, rb, points_buffer = self.buffers
      for buffer, data in ((ob, optical), (nb, nearest), (cb, cells), (tb, self.transform), (pb, self.plane), (rb, representatives)):
        gpu.cl.enqueue_copy(gpu.queue, buffer, np.ascontiguousarray(data), is_blocking=True)
      self.classify(gpu.queue, (n,), None, ob, tb, pb, ab, lb, nb, np.int32(w), np.int32(n))
      self.raster(gpu.queue, (n,), None, ob, ab, lb, nb, cb, rb, np.int32(w), np.int32(n),
                  np.float32(spec.resolution), np.float32(spec.origin_forward), np.float32(spec.origin_left),
                  np.int32(spec.rows), np.int32(spec.cols))
      self.extract(gpu.queue, (cell_count,), None, ab, rb, points_buffer, np.int32(cell_count))
      for buffer, data in ((points_buffer, points), (cb, cells)):
        gpu.cl.enqueue_copy(gpu.queue, data, buffer, is_blocking=True)
    if time.monotonic()-started > 0.05:
      raise TimeoutError('Stereo grid GPU deadline exceeded')
    occupied = (cells & 2) != 0
    return StereoObservation(points[np.isfinite(points).all(axis=1)], occupied, ((cells & 1) != 0) & ~occupied)


class StereoGridSource:
  """Lazily load measured calibration; drop failures and retry with backoff."""
  def __init__(self, load_calibration, client, spec, mount_path):
    self.load_calibration, self.client, self.spec, self.mount_path = load_calibration, client, spec, mount_path
    self.mapper = None
    self.next_attempt = 0.0
    self.calibration_id = ''
    self.shape = None
    self.q = None
    self.last_error = ''
    self.last_timestamp = 0
    self.geometry_key = None

  def close(self):
    mapper, self.mapper = self.mapper, None
    try:
      if mapper is not None:
        mapper.close()
    finally:
      self.client.release()

  def observe(self, msg):
    try:
      age = time.monotonic_ns()-msg.timestamp
      if msg.timestamp <= self.last_timestamp or not 0 <= age <= 200_000_000:
        raise ValueError('Missing, future or stale stereo capture')
      if self.mapper is None:
        if time.monotonic() < self.next_attempt:
          return None
        self.next_attempt = time.monotonic()+2.0
        cal = self.load_calibration()
        shape = np.shape(cal.left_map1)
        if len(shape) != 2:
          raise ValueError('Missing stereo factory map resolution')
        calibration_maps(cal, shape[1], shape[0])
        identity = calibration_id(cal)
        transform, plane = measured_mount(self.mount_path, identity)
        self.mapper = GPUGridMapper(self.client.gpu(), self.spec, transform, plane)
        self.calibration_id, self.shape, self.q = identity, shape, np.asarray(cal.Q)
        self.geometry_key = (identity, transform.tobytes(), plane.tobytes())
      if msg.calibrationId != self.calibration_id or (msg.height, msg.width) != self.shape:
        raise ValueError('Stereo depth calibration identity/resolution mismatch')
      disparity = np.frombuffer(msg.disparityMap, dtype=np.float32).reshape(self.shape)
      confidence = np.frombuffer(msg.confidenceMap, dtype=np.float32).reshape(self.shape)
      result = self.mapper.observe(disparity, confidence, self.q)
      if not 0 <= time.monotonic_ns()-msg.timestamp <= 200_000_000:
        raise TimeoutError('Stereo grid capture expired during GPU processing')
      self.last_error = ''
      self.last_timestamp = msg.timestamp
      if not np.any(result.occupied | result.ground):
        self.last_error = 'no_valid_stereo_endpoints'
        return None
      return result
    except Exception as exc:
      self.last_error = str(exc)
      self.next_attempt = time.monotonic()+2.0
      try:
        self.close()
      except Exception:
        pass
      return None
