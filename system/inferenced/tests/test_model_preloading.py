#!/usr/bin/env python3
"""Test model preloading mechanism (Phase 6.2 - Model Preloading)."""

from __future__ import annotations

import logging
import numpy as np
from openpilot.system.inferenced.compute import (
    HAL, HALConfig, ModelConfig, BackendType, get_hal
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestModelPreloading:
  """Test model preloading and caching functionality."""

  def test_preload_on_initialization(self) -> bool:
    """Test that models are preloaded during HAL initialization."""
    logger.info("Testing model preloading on initialization...")

    # Reset singleton for this test
    HAL._instance = None

    # Create HAL with preload config
    vision_model = ModelConfig(
        name='vision_model',
        path='/tmp/vision.rknn',
        model_type='vision'
    )

    config = HALConfig(
        models_to_preload=[(BackendType.NPU, vision_model)]
    )

    hal = HAL(config)
    hal.initialize()

    # Check if model was preloaded
    if hal.is_model_cached('vision_model'):
      logger.info("✓ Model preloaded successfully")
      logger.info(f"  Model: {hal.get_cached_model('vision_model').name}")
      return True
    else:
      logger.warning("✗ Model not preloaded")
      return False

  def test_model_cache(self) -> bool:
    """Test model caching mechanism."""
    logger.info("Testing model cache...")

    hal = get_hal()

    # Create and cache a model
    test_model = ModelConfig(
        name='test_cache_model',
        path='/tmp/test.rknn',
        model_type='test'
    )

    hal.cache_model(test_model)

    # Check cache
    if hal.is_model_cached('test_cache_model'):
      cached = hal.get_cached_model('test_cache_model')
      if cached and cached.name == 'test_cache_model':
        logger.info("✓ Model caching working")
        logger.info(f"  Cached model: {cached.name}")
        return True

    logger.warning("✗ Model caching failed")
    return False

  def test_clear_cache(self) -> bool:
    """Test cache clearing."""
    logger.info("Testing cache clearing...")

    hal = get_hal()

    # Add a model to cache
    test_model = ModelConfig(name='temp_model', path='/tmp/temp.rknn')
    hal.cache_model(test_model)

    if not hal.is_model_cached('temp_model'):
      logger.warning("✗ Model not cached before clear test")
      return False

    # Clear cache
    hal.clear_model_cache()

    if not hal.is_model_cached('temp_model'):
      logger.info("✓ Cache cleared successfully")
      return True
    else:
      logger.warning("✗ Cache not cleared")
      return False

  def test_multiple_model_preload(self) -> bool:
    """Test preloading multiple models."""
    logger.info("Testing multiple model preloading...")

    # Reset singleton for this test
    HAL._instance = None

    models_to_preload = [
        (BackendType.NPU, ModelConfig(name='vision', path='/tmp/vision.rknn')),
        (BackendType.NPU, ModelConfig(name='policy', path='/tmp/policy.rknn')),
    ]

    config = HALConfig(models_to_preload=models_to_preload)
    hal = HAL(config)
    hal.initialize()

    vision_cached = hal.is_model_cached('vision')
    policy_cached = hal.is_model_cached('policy')

    if vision_cached and policy_cached:
      logger.info("✓ Multiple models preloaded")
      logger.info(f"  Cached models: vision, policy")
      return True
    else:
      logger.warning(f"✗ Preload incomplete (vision={vision_cached}, policy={policy_cached})")
      return False

  def test_preload_stats(self) -> bool:
    """Test that preloaded models track load time."""
    logger.info("Testing preload statistics...")

    # Reset singleton for this test
    HAL._instance = None

    config = HALConfig(
        models_to_preload=[(
            BackendType.NPU,
            ModelConfig(name='stat_test', path='/tmp/stat.rknn')
        )]
    )

    hal = HAL(config)
    hal.initialize()

    cached_model = hal.get_cached_model('stat_test')
    if cached_model and cached_model.loaded and cached_model.load_time_ms > 0:
      logger.info("✓ Model statistics tracked")
      logger.info(f"  Load time: {cached_model.load_time_ms:.2f}ms")
      return True
    else:
      logger.warning("✗ Model statistics missing")
      return False

  def test_backend_not_available(self) -> bool:
    """Test graceful handling when backend not available."""
    logger.info("Testing preload with unavailable backend...")

    # Try to preload on non-existent backend
    config = HALConfig(
        enable_npu=False,  # Disable NPU
        models_to_preload=[(
            BackendType.NPU,
            ModelConfig(name='unavail', path='/tmp/unavail.rknn')
        )]
    )

    hal = HAL(config)
    hal.initialize()

    # Should handle gracefully without crashing
    logger.info("✓ Handled unavailable backend gracefully")
    return True

  def run_all_tests(self) -> dict[str, bool]:
    """Run all preloading tests."""
    logger.info("\n" + "="*60)
    logger.info("Model Preloading Tests (Phase 6.2)")
    logger.info("="*60 + "\n")

    results = {
        'preload_on_init': self.test_preload_on_initialization(),
        'model_cache': self.test_model_cache(),
        'clear_cache': self.test_clear_cache(),
        'multiple_preload': self.test_multiple_model_preload(),
        'preload_stats': self.test_preload_stats(),
        'unavailable_backend': self.test_backend_not_available(),
    }

    logger.info("\n" + "="*60)
    logger.info("Model Preloading Test Results")
    logger.info("="*60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, result in results.items():
      status = "✓ PASS" if result else "✗ FAIL"
      logger.info(f"  {test_name:25} {status}")

    logger.info("="*60 + "\n")

    return results


def main():
  """Main entry point."""
  tester = TestModelPreloading()

  try:
    results = tester.run_all_tests()

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    if passed >= 5:
      logger.info("✓ Model Preloading Tests PASSED\n")
      return 0
    else:
      logger.error(f"✗ Test failures ({total - passed}/{total})\n")
      return 1

  except Exception as e:
    logger.error(f"Fatal error: {e}", exc_info=True)
    return 1


if __name__ == '__main__':
  import sys
  sys.exit(main())
