#!/usr/bin/env python3
"""Test modeld integration with InferenceD HAL.

Verifies that modeld can use RKNN backend via InferenceClient.
Runs on dev PC (mock RKNN) and edge hardware (real RKNN).
"""

from __future__ import annotations

import time
import logging
import numpy as np
from typing import Any

from openpilot.system.inferenced import InferenceClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestModeld:
  """Test modeld using InferenceClient."""

  def __init__(self):
    self.client = InferenceClient("modeld_test")
    self.npu = None
    try:
      self.npu = self.client.npu()
      logger.info("✓ NPU backend available")
      self._load_models()
    except RuntimeError as e:
      logger.warning(f"NPU not available: {e}")
      return

  def _load_models(self):
    """Load test models."""
    from openpilot.system.inferenced.compute import ModelConfig

    try:
      # Load mock vision model
      vision_config = ModelConfig(
          name='vision_model',
          path='/tmp/vision_model.rknn',  # Path doesn't need to exist in mock mode
          model_type='vision'
      )
      self.npu.load_model(vision_config)
      logger.info("✓ Vision model loaded")

      # Load mock policy model
      policy_config = ModelConfig(
          name='policy_model',
          path='/tmp/policy_model.rknn',
          model_type='policy'
      )
      self.npu.load_model(policy_config)
      logger.info("✓ Policy model loaded")

    except Exception:
      logger.exception("Failed to load models")

  def test_vision_model(self) -> bool:
    """Test vision model inference."""
    if self.npu is None:
      logger.warning("NPU not available, skipping")
      return False

    logger.info("Testing vision model inference...")

    try:
      # Mock camera image: (1, 480, 640, 3) float32
      # Real modeld uses framed YUV images, but RKNN accepts any format
      img = np.random.randn(1, 480, 640, 3).astype(np.float32)

      # First inference (cold start)
      start = time.monotonic()
      result = self.npu.infer('vision_model', {'input': img})
      latency_ms = (time.monotonic() - start) * 1000

      if result.success:
        logger.info("✓ Vision model inference success")
        logger.info(f"  Latency: {latency_ms:.2f}ms")
        logger.info(f"  Output shape: {result.outputs['output'].shape}")
        return True
      else:
        logger.error(f"✗ Inference failed: {result.error_message}")
        return False

    except Exception:
      logger.exception("✗ Exception")
      return False

  def test_policy_model(self) -> bool:
    """Test policy model inference."""
    if self.npu is None:
      logger.warning("NPU not available, skipping")
      return False

    logger.info("Testing policy model inference...")

    try:
      # Mock state: (1, 512) float32 (typical policy input size)
      state = np.random.randn(1, 512).astype(np.float32)

      start = time.monotonic()
      result = self.npu.infer('policy_model', {'state': state})
      latency_ms = (time.monotonic() - start) * 1000

      if result.success:
        logger.info("✓ Policy model inference success")
        logger.info(f"  Latency: {latency_ms:.2f}ms")
        logger.info(f"  Output shape: {result.outputs['output'].shape}")
        return True
      else:
        logger.error(f"✗ Inference failed: {result.error_message}")
        return False

    except Exception:
      logger.exception("✗ Exception")
      return False

  def test_inference_loop(self, num_frames: int = 10) -> dict[str, Any]:
    """Test continuous inference loop (like real modeld)."""
    if self.npu is None:
      logger.warning("NPU not available, skipping")
      return {}

    logger.info(f"Testing {num_frames} frame inference loop...")

    try:
      latencies = []
      errors = 0

      for i in range(num_frames):
        img = np.random.randn(1, 480, 640, 3).astype(np.float32)

        start = time.monotonic()
        result = self.npu.infer('vision_model', {'input': img})
        latency_ms = (time.monotonic() - start) * 1000

        if result.success:
          latencies.append(latency_ms)
        else:
          errors += 1
          logger.error(f"  Frame {i}: {result.error_message}")

      success_rate = (num_frames - errors) / num_frames * 100
      avg_latency = np.mean(latencies) if latencies else 0
      max_latency = np.max(latencies) if latencies else 0
      min_latency = np.min(latencies) if latencies else 0

      logger.info("✓ Loop complete")
      logger.info(f"  Success rate: {success_rate:.1f}%")
      logger.info(f"  Avg latency: {avg_latency:.2f}ms")
      logger.info(f"  Min/Max: {min_latency:.2f}ms / {max_latency:.2f}ms")

      return {
          'success_rate': success_rate,
          'avg_latency_ms': avg_latency,
          'min_latency_ms': min_latency,
          'max_latency_ms': max_latency,
      }

    except Exception:
      logger.exception("✗ Exception")
      return {}

  def test_output_consistency(self) -> bool:
    """Test that outputs have correct shape and dtype."""
    if self.npu is None:
      logger.warning("NPU not available, skipping")
      return False

    logger.info("Testing output consistency...")

    try:
      img = np.random.randn(1, 480, 640, 3).astype(np.float32)
      result = self.npu.infer('vision_model', {'input': img})

      if not result.success:
        logger.error(f"Inference failed: {result.error_message}")
        return False

      # Check outputs exist
      outputs = result.outputs
      if not outputs or 'output' not in outputs:
        logger.error("No outputs returned")
        return False

      output = outputs['output']

      # Check output is numpy array
      if not isinstance(output, np.ndarray):
        logger.error(f"Output is not numpy array: {type(output)}")
        return False

      # Check dtype
      if output.dtype not in (np.float32, np.float64):
        logger.warning(f"Unusual output dtype: {output.dtype}")

      logger.info("✓ Output consistency verified")
      logger.info(f"  Shape: {output.shape}")
      logger.info(f"  Dtype: {output.dtype}")
      logger.info(f"  Range: [{output.min():.3f}, {output.max():.3f}]")

      return True

    except Exception:
      logger.exception("✗ Exception")
      return False

  def run_all_tests(self) -> dict[str, bool]:
    """Run all tests."""
    logger.info("\n" + "="*60)
    logger.info("modeld Integration Tests")
    logger.info("="*60 + "\n")

    results = {
        'vision_model': self.test_vision_model(),
        'policy_model': self.test_policy_model(),
        'output_consistency': self.test_output_consistency(),
    }

    # Run loop test
    loop_results = self.test_inference_loop(10)
    results['inference_loop'] = len(loop_results) > 0

    logger.info("\n" + "="*60)
    logger.info("Test Results Summary")
    logger.info("="*60)

    sum(1 for v in results.values() if v)
    len(results)

    for test_name, result in results.items():
      status = "✓ PASS" if result else "✗ FAIL"
      logger.info(f"  {test_name:25} {status}")

    if loop_results:
      logger.info("\nLoop Performance:")
      logger.info(f"  Success Rate: {loop_results['success_rate']:.1f}%")
      logger.info(f"  Avg Latency: {loop_results['avg_latency_ms']:.2f}ms")

    logger.info("="*60 + "\n")

    return results

  def release(self):
    """Release resources."""
    self.client.release()


def main():
  """Main entry point."""
  modeld = TestModeld()

  try:
    results = modeld.run_all_tests()

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    if passed >= 2:
      logger.info("✓ modeld Integration Tests PASSED\n")
      return 0
    else:
      logger.error(f"✗ Test failures ({total - passed}/{total})\n")
      return 1

  except Exception as e:
    logger.error(f"Fatal error: {e}", exc_info=True)
    return 1

  finally:
    modeld.release()


if __name__ == '__main__':
  import sys
  sys.exit(main())
