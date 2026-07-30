#!/usr/bin/env python3
"""Performance profiling for InferenceD backends.

Measures latency, throughput, and memory usage on dev PC (mock mode).
Provides baseline for edge hardware comparison.
"""

from __future__ import annotations

import time
import logging
import numpy as np
from typing import Any
from dataclasses import dataclass

from openpilot.system.inferenced import get_hal, BackendType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class PerfMetrics:
  """Performance metrics."""
  operation: str
  backend: str
  latency_ms: float
  throughput_ops_sec: float
  success_rate: float
  min_latency_ms: float
  max_latency_ms: float
  stddev_ms: float


class PerformanceProfiler:
  """Profile backend performance on dev PC."""

  def __init__(self):
    self.hal = get_hal()
    self.results = []

  def profile_backend(self, backend_type: BackendType, operation: str,
                     inputs: dict, iterations: int = 100) -> PerfMetrics | None:
    """Profile a single backend operation."""
    backend = self.hal.get_backend(backend_type)
    if backend is None:
      logger.warning(f"Backend {backend_type.name} not available")
      return None

    logger.info(f"Profiling {backend_type.name}: {operation}")

    latencies = []
    errors = 0

    for i in range(iterations):
      try:
        start = time.perf_counter()
        result = backend.infer(operation, inputs)
        latency = (time.perf_counter() - start) * 1000

        if result.success:
          latencies.append(latency)
        else:
          errors += 1
      except Exception as e:
        logger.error(f"  Error: {e}")
        errors += 1

    if not latencies:
      logger.warning(f"  No successful operations")
      return None

    avg_latency = float(np.mean(latencies))
    min_latency = float(np.min(latencies))
    max_latency = float(np.max(latencies))
    stddev = float(np.std(latencies))
    success_rate = (iterations - errors) / iterations * 100
    throughput = 1000.0 / avg_latency  # ops per second

    metrics = PerfMetrics(
        operation=operation,
        backend=backend_type.name,
        latency_ms=avg_latency,
        throughput_ops_sec=throughput,
        success_rate=success_rate,
        min_latency_ms=min_latency,
        max_latency_ms=max_latency,
        stddev_ms=stddev
    )

    logger.info(f"  Latency: {avg_latency:.3f}ms (±{stddev:.3f}ms) [{min_latency:.3f}-{max_latency:.3f}ms]")
    logger.info(f"  Throughput: {throughput:.1f} ops/sec")
    logger.info(f"  Success rate: {success_rate:.1f}%")

    return metrics

  def profile_rknn(self) -> list[PerfMetrics]:
    """Profile RKNN NPU backend."""
    logger.info("\n" + "="*60)
    logger.info("RKNN NPU Backend Profiling")
    logger.info("="*60 + "\n")

    results = []

    # Load models first
    from openpilot.system.inferenced.compute import ModelConfig
    backend = self.hal.get_backend(BackendType.NPU)
    if backend:
      backend.load_model(ModelConfig(name='test_model', path='/tmp/test.rknn'))

      # Small vision input
      metrics = self.profile_backend(
          BackendType.NPU,
          'test_model',
          {'input': np.random.randn(1, 224, 224, 3).astype(np.float32)},
          iterations=50
      )
      if metrics:
        results.append(metrics)

      # Policy input
      metrics = self.profile_backend(
          BackendType.NPU,
          'test_model',
          {'input': np.random.randn(1, 512).astype(np.float32)},
          iterations=100
      )
      if metrics:
        results.append(metrics)

    return results

  def profile_rga(self) -> list[PerfMetrics]:
    """Profile RGA 2D graphics backend."""
    logger.info("\n" + "="*60)
    logger.info("RGA 2D Graphics Backend Profiling")
    logger.info("="*60 + "\n")

    results = []

    # Color conversion
    metrics = self.profile_backend(
        BackendType.RGA,
        'cvtcolor',
        {
            'src_fmt': 'nv12',
            'dst_fmt': 'rgb',
            'src_y': np.zeros((480, 640), dtype=np.uint8),
            'src_uv': np.zeros((240, 320), dtype=np.uint8),
        },
        iterations=100
    )
    if metrics:
      results.append(metrics)

    # Resize
    metrics = self.profile_backend(
        BackendType.RGA,
        'resize',
        {
            'input': np.zeros((480, 640, 3), dtype=np.uint8),
            'width': 320,
            'height': 240,
        },
        iterations=100
    )
    if metrics:
      results.append(metrics)

    # Crop
    metrics = self.profile_backend(
        BackendType.RGA,
        'crop',
        {
            'input': np.zeros((480, 640, 3), dtype=np.uint8),
            'x': 0, 'y': 0,
            'width': 320, 'height': 240,
        },
        iterations=100
    )
    if metrics:
      results.append(metrics)

    return results

  def profile_mpp(self) -> list[PerfMetrics]:
    """Profile MPP codec backend."""
    logger.info("\n" + "="*60)
    logger.info("MPP Codec Backend Profiling")
    logger.info("="*60 + "\n")

    results = []

    # H.264 encode
    metrics = self.profile_backend(
        BackendType.MPP,
        'h264_encode',
        {
            'frame': np.zeros((720, 1280, 3), dtype=np.uint8),
            'width': 1280,
            'height': 720,
            'bitrate': 4000,
            'fps': 20,
        },
        iterations=100
    )
    if metrics:
      results.append(metrics)

    # H.264 decode
    metrics = self.profile_backend(
        BackendType.MPP,
        'h264_decode',
        {
            'data': b'\x00\x00\x00\x01\x67' + bytes([0x42]) * 64,
            'width': 1280,
            'height': 720,
        },
        iterations=100
    )
    if metrics:
      results.append(metrics)

    return results

  def profile_client_api(self) -> list[PerfMetrics]:
    """Profile InferenceClient API overhead."""
    logger.info("\n" + "="*60)
    logger.info("InferenceClient API Overhead")
    logger.info("="*60 + "\n")

    from openpilot.system.inferenced import InferenceClient

    results = []
    client = InferenceClient("perf_test")

    # Measure client initialization
    init_times = []
    for _ in range(20):
      start = time.perf_counter()
      _ = InferenceClient(f"perf_test_{_}")
      init_times.append((time.perf_counter() - start) * 1000)

    avg_init_time = np.mean(init_times)
    logger.info(f"Client initialization: {avg_init_time:.3f}ms avg")

    # Measure backend access
    try:
      npu = client.npu()
      logger.info(f"✓ NPU backend access successful")
    except RuntimeError:
      logger.info(f"⚠ NPU not available")

    try:
      rga = client.rga()
      logger.info(f"✓ RGA backend access successful")
    except RuntimeError:
      logger.info(f"⚠ RGA not available")

    client.release()
    return results

  def run_all_profiles(self) -> dict[str, list[PerfMetrics]]:
    """Run all performance profiles."""
    logger.info("\n" + "="*70)
    logger.info("InferenceD Performance Profiling - Dev PC (Mock Mode)")
    logger.info("="*70)

    all_results = {
        'rknn': self.profile_rknn(),
        'rga': self.profile_rga(),
        'mpp': self.profile_mpp(),
        'client': self.profile_client_api(),
    }

    return all_results

  def print_summary(self, all_results: dict[str, list[PerfMetrics]]):
    """Print performance summary."""
    logger.info("\n" + "="*70)
    logger.info("Performance Summary")
    logger.info("="*70 + "\n")

    # Summary table
    logger.info(f"{'Backend':<10} {'Operation':<20} {'Latency (ms)':<15} {'Throughput':<15} {'Success Rate'}")
    logger.info("-" * 75)

    for category, metrics_list in all_results.items():
      for metrics in metrics_list:
        logger.info(f"{metrics.backend:<10} {metrics.operation:<20} "
                   f"{metrics.latency_ms:<14.3f} {metrics.throughput_ops_sec:<14.1f} ops/s "
                   f"{metrics.success_rate:.1f}%")

    # Performance expectations
    logger.info("\n" + "="*70)
    logger.info("Expected Edge Hardware Performance (for comparison)")
    logger.info("="*70)
    logger.info("""
Vision Model (RKNN NPU):         ~10-20ms
Policy Model (RKNN NPU):         ~5ms
SGM Stereo 640x480 (GPU):        ~30ms
H.264 Decode 4K (Hardware):      ~16ms
Image Resize 1080→720 (RGA):     ~2ms

Dev PC (Mock Mode) - Overhead Only:
~0.1-1ms (includes IPC + mock compute)

Expected CPU Savings: ~87% total
""")

    logger.info("="*70 + "\n")


def main():
  """Main entry point."""
  profiler = PerformanceProfiler()

  try:
    results = profiler.run_all_profiles()
    profiler.print_summary(results)

    logger.info("✓ Performance profiling complete\n")
    return 0

  except Exception as e:
    logger.error(f"Fatal error: {e}", exc_info=True)
    return 1


if __name__ == '__main__':
  import sys
  sys.exit(main())
