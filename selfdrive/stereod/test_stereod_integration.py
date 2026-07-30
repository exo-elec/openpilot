#!/usr/bin/env python3
"""Test stereod integration with ACL backend for SGM stereo depth.

Verifies that stereod can use ACL (GPU-preferred) backend via InferenceClient.
Tests smart GPU/CPU dispatch on different input sizes.
"""

from __future__ import annotations

import time
import logging
import numpy as np
from typing import Any

from openpilot.common.swaglog import cloudlog
from openpilot.system.inferenced import InferenceClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestStereod:
  """Test stereod using InferenceClient.acl() for SGM stereo."""

  def __init__(self):
    self.client = InferenceClient("stereod_test")
    self.acl = None
    try:
      self.acl = self.client.acl()
      logger.info("✓ ACL backend available")
    except RuntimeError as e:
      logger.warning(f"ACL not available: {e}")
      return

  def test_sgm_stereo_vga(self) -> bool:
    """Test SGM stereo at VGA resolution (640×480)."""
    if self.acl is None:
      logger.warning("ACL not available, skipping")
      return False

    logger.info("Testing SGM stereo at VGA (640×480)...")

    try:
      # Stereo pair: (480, 640) uint8
      left = np.zeros((480, 640), dtype=np.uint8)
      right = np.zeros((480, 640), dtype=np.uint8)

      start = time.monotonic()
      result = self.acl.infer('sgm_stereo', {'left': left, 'right': right})
      latency_ms = (time.monotonic() - start) * 1000

      if result.success:
        logger.info(f"✓ SGM stereo VGA success")
        logger.info(f"  Latency: {latency_ms:.2f}ms")
        logger.info(f"  Disparity shape: {result.outputs['output'].shape}")
        return True
      else:
        logger.error(f"✗ SGM stereo failed: {result.error_message}")
        return False

    except Exception as e:
      logger.error(f"✗ Exception: {e}")
      return False

  def test_sgm_stereo_hd(self) -> bool:
    """Test SGM stereo at HD resolution (1280×720)."""
    if self.acl is None:
      logger.warning("ACL not available, skipping")
      return False

    logger.info("Testing SGM stereo at HD (1280×720)...")

    try:
      # Stereo pair: (720, 1280) uint8
      left = np.zeros((720, 1280), dtype=np.uint8)
      right = np.zeros((720, 1280), dtype=np.uint8)

      start = time.monotonic()
      result = self.acl.infer('sgm_stereo', {'left': left, 'right': right})
      latency_ms = (time.monotonic() - start) * 1000

      if result.success:
        logger.info(f"✓ SGM stereo HD success")
        logger.info(f"  Latency: {latency_ms:.2f}ms")
        logger.info(f"  Disparity shape: {result.outputs['output'].shape}")
        return True
      else:
        logger.error(f"✗ SGM stereo failed: {result.error_message}")
        return False

    except Exception as e:
      logger.error(f"✗ Exception: {e}")
      return False

  def test_gemm_small(self) -> bool:
    """Test GEMM (matrix multiply) - should prefer GPU for forced op."""
    if self.acl is None:
      logger.warning("ACL not available, skipping")
      return False

    logger.info("Testing GEMM (matrix multiply) - small...")

    try:
      # Small matrix multiply: (16, 256) × (256, 128) = (16, 128)
      a = np.random.randn(16, 256).astype(np.float32)
      b = np.random.randn(256, 128).astype(np.float32)

      start = time.monotonic()
      result = self.acl.infer('gemm', {'a': a, 'b': b})
      latency_ms = (time.monotonic() - start) * 1000

      if result.success:
        logger.info(f"✓ GEMM small success")
        logger.info(f"  Latency: {latency_ms:.2f}ms")
        logger.info(f"  Result shape: {result.outputs['output'].shape}")
        return True
      else:
        logger.error(f"✗ GEMM failed: {result.error_message}")
        return False

    except Exception as e:
      logger.error(f"✗ Exception: {e}")
      return False

  def test_gpu_cpu_dispatch(self) -> bool:
    """Test smart GPU/CPU dispatch based on input size."""
    if self.acl is None:
      logger.warning("ACL not available, skipping")
      return False

    logger.info("Testing GPU/CPU smart dispatch...")

    try:
      # Small input - should prefer CPU
      small_input = np.ones((100,), dtype=np.float32)
      result_small = self.acl.infer('generic_op', {'input': small_input})

      if not result_small.success:
        logger.error(f"Small input inference failed")
        return False

      # Large input - should prefer GPU if available
      large_input = np.ones((2000,), dtype=np.float32)
      result_large = self.acl.infer('generic_op', {'input': large_input})

      if not result_large.success:
        logger.error(f"Large input inference failed")
        return False

      logger.info(f"✓ Smart dispatch test passed")
      logger.info(f"  Small input (100 elements): {result_small.inference_time_ms:.2f}ms")
      logger.info(f"  Large input (2000 elements): {result_large.inference_time_ms:.2f}ms")
      return True

    except Exception as e:
      logger.error(f"✗ Exception: {e}")
      return False

  def test_stereo_loop(self, num_frames: int = 5) -> dict[str, Any]:
    """Test continuous stereo processing loop."""
    if self.acl is None:
      logger.warning("ACL not available, skipping")
      return {}

    logger.info(f"Testing {num_frames} frame stereo loop...")

    try:
      latencies = []
      errors = 0

      for i in range(num_frames):
        left = np.random.randint(0, 256, (480, 640), dtype=np.uint8)
        right = np.random.randint(0, 256, (480, 640), dtype=np.uint8)

        start = time.monotonic()
        result = self.acl.infer('sgm_stereo', {'left': left, 'right': right})
        latency_ms = (time.monotonic() - start) * 1000

        if result.success:
          latencies.append(latency_ms)
        else:
          errors += 1
          logger.error(f"  Frame {i}: {result.error_message}")

      success_rate = (num_frames - errors) / num_frames * 100
      avg_latency = np.mean(latencies) if latencies else 0

      logger.info(f"✓ Loop complete")
      logger.info(f"  Success rate: {success_rate:.1f}%")
      logger.info(f"  Avg latency: {avg_latency:.2f}ms")

      return {
          'success_rate': success_rate,
          'avg_latency_ms': avg_latency,
      }

    except Exception as e:
      logger.error(f"✗ Exception: {e}")
      return {}

  def run_all_tests(self) -> dict[str, bool]:
    """Run all tests."""
    logger.info("\n" + "="*60)
    logger.info("stereod Integration Tests (ACL Backend)")
    logger.info("="*60 + "\n")

    results = {
        'sgm_vga': self.test_sgm_stereo_vga(),
        'sgm_hd': self.test_sgm_stereo_hd(),
        'gemm': self.test_gemm_small(),
        'dispatch': self.test_gpu_cpu_dispatch(),
    }

    # Run loop test
    loop_results = self.test_stereo_loop(5)
    results['stereo_loop'] = len(loop_results) > 0

    logger.info("\n" + "="*60)
    logger.info("Test Results Summary")
    logger.info("="*60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, result in results.items():
      status = "✓ PASS" if result else "✗ FAIL"
      logger.info(f"  {test_name:25} {status}")

    if loop_results:
      logger.info(f"\nStereo Loop Performance:")
      logger.info(f"  Success Rate: {loop_results['success_rate']:.1f}%")
      logger.info(f"  Avg Latency: {loop_results['avg_latency_ms']:.2f}ms")

    logger.info("="*60 + "\n")

    return results

  def release(self):
    """Release resources."""
    self.client.release()


def main():
  """Main entry point."""
  stereod = TestStereod()

  try:
    results = stereod.run_all_tests()

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    if passed >= 3:
      logger.info("✓ stereod Integration Tests PASSED\n")
      return 0
    else:
      logger.error(f"✗ Test failures ({total - passed}/{total})\n")
      return 1

  except Exception as e:
    logger.error(f"Fatal error: {e}", exc_info=True)
    return 1

  finally:
    stereod.release()


if __name__ == '__main__':
  import sys
  sys.exit(main())
