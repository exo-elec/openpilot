#!/usr/bin/env python3
"""End-to-end test daemon - verifies InferenceClient and HAL integration.

Run this to test complete inference pipeline on dev PC.
"""

from __future__ import annotations

import time
import logging
import numpy as np
from pathlib import Path

from openpilot.common.swaglog import cloudlog
from openpilot.system.inferenced import InferenceClient, BackendType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestDaemon:
  """Simple test daemon using InferenceClient."""

  def __init__(self, daemon_name: str = "test_daemon"):
    self.daemon_name = daemon_name
    self.client = InferenceClient(daemon_name)
    logger.info(f"TestDaemon initialized: {daemon_name}")

  def test_npu_inference(self) -> bool:
    """Test NPU inference."""
    logger.info("Testing NPU inference...")
    try:
      npu = self.client.npu()

      # Mock image input
      img = np.random.randn(1, 224, 224, 3).astype(np.float32)
      result = npu.infer('test_model', {'input': img})

      if result.success:
        logger.info(f"✓ NPU inference success: {result.inference_time_ms:.2f}ms")
        return True
      else:
        logger.error(f"✗ NPU inference failed: {result.error_message}")
        return False

    except RuntimeError as e:
      logger.warning(f"NPU not available: {e}")
      return False

  def test_acl_inference(self) -> bool:
    """Test ACL (GPU/CPU) inference."""
    logger.info("Testing ACL inference...")
    try:
      acl = self.client.acl()

      # Small input (should prefer CPU)
      small_input = np.ones((10,), dtype=np.float32)
      result_small = acl.infer('test_small', {'input': small_input})

      # Large input (should prefer GPU if available)
      large_input = np.ones((2000,), dtype=np.float32)
      result_large = acl.infer('test_large', {'input': large_input})

      if result_small.success and result_large.success:
        logger.info(f"✓ ACL inference success: small={result_small.inference_time_ms:.2f}ms, large={result_large.inference_time_ms:.2f}ms")
        return True
      else:
        logger.error(f"✗ ACL inference failed")
        return False

    except RuntimeError as e:
      logger.warning(f"ACL not available: {e}")
      return False

  def test_rga_operations(self) -> bool:
    """Test RGA image operations."""
    logger.info("Testing RGA image operations...")
    try:
      rga = self.client.rga()

      # Test cvtcolor
      y_data = np.zeros((480, 640), dtype=np.uint8)
      uv_data = np.zeros((240, 320), dtype=np.uint8)
      result = rga.infer('cvtcolor', {
          'src_fmt': 'nv12',
          'dst_fmt': 'rgb',
          'src_y': y_data,
          'src_uv': uv_data
      })

      if result.success:
        logger.info(f"✓ RGA cvtcolor success: {result.inference_time_ms:.2f}ms")
      else:
        logger.error(f"✗ RGA cvtcolor failed: {result.error_message}")
        return False

      # Test resize
      img = np.zeros((480, 640, 3), dtype=np.uint8)
      result = rga.infer('resize', {
          'input': img,
          'width': 320,
          'height': 240
      })

      if result.success:
        logger.info(f"✓ RGA resize success: {result.inference_time_ms:.2f}ms")
        return True
      else:
        logger.error(f"✗ RGA resize failed: {result.error_message}")
        return False

    except RuntimeError as e:
      logger.warning(f"RGA not available: {e}")
      return False

  def test_mpp_codec(self) -> bool:
    """Test MPP video codec operations."""
    logger.info("Testing MPP codec...")
    try:
      mpp = self.client.mpp()

      # Test H.264 encode
      frame = np.zeros((720, 1280, 3), dtype=np.uint8)
      result = mpp.infer('h264_encode', {
          'frame': frame,
          'width': 1280,
          'height': 720,
          'bitrate': 4000,
          'fps': 20
      })

      if result.success:
        logger.info(f"✓ MPP H.264 encode success: {result.inference_time_ms:.2f}ms")
      else:
        logger.error(f"✗ MPP encode failed: {result.error_message}")
        return False

      # Test H.264 decode
      encoded = b'\x00\x00\x00\x01\x67' + bytes([0x42]) * 64
      result = mpp.infer('h264_decode', {
          'data': encoded,
          'width': 1280,
          'height': 720
      })

      if result.success:
        logger.info(f"✓ MPP H.264 decode success: {result.inference_time_ms:.2f}ms")
        return True
      else:
        logger.error(f"✗ MPP decode failed: {result.error_message}")
        return False

    except RuntimeError as e:
      logger.warning(f"MPP not available: {e}")
      return False

  def test_best_compute(self) -> bool:
    """Test best_compute() convenience method."""
    logger.info("Testing best_compute()...")
    try:
      backend = self.client.best_compute()

      inputs = {'test': np.zeros((100,), dtype=np.float32)}
      result = backend.infer('test', inputs)

      if result.success:
        logger.info(f"✓ best_compute() success: backend={result.backend_type.name}, time={result.inference_time_ms:.2f}ms")
        return True
      else:
        logger.error(f"✗ best_compute() failed: {result.error_message}")
        return False

    except RuntimeError as e:
      logger.error(f"✗ No compute backends available: {e}")
      return False

  def test_backend_stats(self) -> bool:
    """Test backend statistics tracking."""
    logger.info("Testing backend statistics...")
    try:
      from openpilot.system.inferenced import get_hal

      hal = get_hal()
      backends = hal.get_available_backends()

      if not backends:
        logger.warning("No backends available for stats testing")
        return False

      all_valid = True
      for backend_type in backends:
        backend = hal.get_backend(backend_type)
        if backend is None:
          continue

        stats = backend.get_stats()
        logger.info(f"  {backend_type.name}: completed={stats.tasks_completed}, "
                   f"failed={stats.tasks_failed}, "
                   f"avg_time={stats.average_latency_ms:.2f}ms")

      return True

    except Exception as e:
      logger.error(f"✗ Stats test failed: {e}")
      return False

  def run_all_tests(self) -> dict[str, bool]:
    """Run all tests."""
    logger.info(f"\n{'='*60}")
    logger.info(f"Starting end-to-end tests for {self.daemon_name}")
    logger.info(f"{'='*60}\n")

    results = {
        'NPU': self.test_npu_inference(),
        'ACL': self.test_acl_inference(),
        'RGA': self.test_rga_operations(),
        'MPP': self.test_mpp_codec(),
        'BestCompute': self.test_best_compute(),
        'Stats': self.test_backend_stats(),
    }

    logger.info(f"\n{'='*60}")
    logger.info("Test Results:")
    logger.info(f"{'='*60}")

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, result in results.items():
      status = "✓ PASS" if result else "✗ FAIL"
      logger.info(f"  {test_name:20} {status}")

    logger.info(f"{'='*60}")
    logger.info(f"Results: {passed}/{total} tests passed")
    logger.info(f"{'='*60}\n")

    return results

  def release(self):
    """Release resources."""
    self.client.release()
    logger.info(f"TestDaemon released: {self.daemon_name}")


def main():
  """Main entry point."""
  daemon = TestDaemon("inference_test")

  try:
    results = daemon.run_all_tests()

    # Return exit code based on results
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    if passed >= 2:  # At least 2 tests should pass
      logger.info("\n✓ End-to-end tests PASSED\n")
      return 0
    else:
      logger.error(f"\n✗ Too many test failures ({total - passed}/{total})\n")
      return 1

  except Exception as e:
    logger.error(f"Daemon crashed: {e}", exc_info=True)
    return 1

  finally:
    daemon.release()


if __name__ == '__main__':
  import sys
  sys.exit(main())
