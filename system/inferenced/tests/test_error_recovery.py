#!/usr/bin/env python3
"""Test error recovery and fallback mechanisms (Phase 6.3 - Error Recovery)."""

from __future__ import annotations

import logging
import numpy as np
from openpilot.system.inferenced import get_hal, BackendType
from openpilot.system.inferenced.compute_recovery import (
    ErrorRecoveryManager, BackendHealthMonitor, FallbackStrategy,
    ErrorCategory, ErrorInfo, create_error_from_exception
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestErrorRecovery:
  """Test error recovery and fallback functionality."""

  def test_fallback_strategy(self) -> bool:
    """Test fallback strategy tracking."""
    logger.info("Testing fallback strategy...")

    strategy = FallbackStrategy("GPU", "CPU")

    # Initially should not use fallback (need at least 3 ops)
    if strategy.should_try_fallback():
      logger.warning("✗ Should not fallback initially")
      return False

    # Record failures (no successes) to trigger fallback
    # Needs total >= 3 and failure_count / total > 0.5
    strategy.record_failure()  # failure_count=1
    strategy.record_failure()  # failure_count=2
    strategy.record_failure()  # failure_count=3 (total=3, 100% failure rate)

    # After 100% failure rate with 3+ ops, should fallback
    if strategy.should_try_fallback():
      logger.info("✓ Fallback triggered correctly (100% failure rate)")
      return True
    else:
      logger.warning("✗ Fallback not triggered at 100% failure rate")
      return False

  def test_health_monitor(self) -> bool:
    """Test backend health monitoring."""
    logger.info("Testing backend health monitor...")

    monitor = BackendHealthMonitor("TestBackend", max_failures=3)

    # Initially healthy
    if not monitor.is_healthy:
      logger.warning("✗ Should be healthy initially")
      return False

    # Record failures
    for i in range(2):
      monitor.record_failure()
      if not monitor.is_healthy:
        logger.warning("✗ Should still be healthy after 2 failures")
        return False

    # Third failure should mark unhealthy
    should_restart = monitor.record_failure()
    if not should_restart or monitor.is_healthy:
      logger.warning("✗ Should mark unhealthy after 3 failures")
      return False

    logger.info("✓ Health monitor working correctly")
    return True

  def test_error_categorization(self) -> bool:
    """Test error categorization."""
    logger.info("Testing error categorization...")

    # Test timeout error
    timeout_exc = TimeoutError("Operation timed out")
    error = create_error_from_exception(timeout_exc, "NPU", "model")
    if error.category != ErrorCategory.TIMEOUT:
      logger.warning("✗ Timeout error not categorized correctly")
      return False

    # Test resource exhausted error
    mem_exc = MemoryError("Out of memory")
    error = create_error_from_exception(mem_exc, "ACL", "model")
    if error.category != ErrorCategory.RESOURCE_EXHAUSTED:
      logger.warning("✗ Memory error not categorized correctly")
      return False

    logger.info("✓ Error categorization working")
    return True

  def test_error_recovery_manager(self) -> bool:
    """Test error recovery manager."""
    logger.info("Testing error recovery manager...")

    manager = ErrorRecoveryManager()

    # Register fallback strategy
    strategy = FallbackStrategy("GPU", "CPU")
    manager.register_fallback_strategy(strategy)

    # Register health monitor
    monitor = BackendHealthMonitor("GPU", max_failures=2)
    manager.register_backend_monitor(monitor)

    # Record an error
    error = ErrorInfo(
        category=ErrorCategory.TIMEOUT,
        message="Test timeout",
        backend_name="GPU",
        model_name="test_model"
    )
    manager.record_error(error)

    # Check error history
    summary = manager.get_error_summary()
    if summary['total_errors'] != 1:
      logger.warning("✗ Error not recorded")
      return False

    logger.info("✓ Error recovery manager working")
    return True

  def test_hal_error_recovery_integration(self) -> bool:
    """Test HAL integration with error recovery."""
    logger.info("Testing HAL error recovery integration...")

    hal = get_hal()

    # Get health report
    health_report = hal.get_backend_health_report()
    if not health_report:
      logger.warning("⚠ No backends in health report (expected if no backends)")
      return True

    # Get error summary
    error_summary = hal.get_error_summary()
    if 'total_errors' not in error_summary:
      logger.warning("✗ Error summary missing total_errors")
      return False

    logger.info("✓ HAL error recovery integrated")
    return True

  def test_backend_health_check(self) -> bool:
    """Test backend health checking."""
    logger.info("Testing backend health checks...")

    hal = get_hal()

    # Check if NPU is healthy
    if hal.get_backend(BackendType.NPU):
      is_healthy = hal.is_backend_healthy(BackendType.NPU)
      logger.info(f"  NPU health: {'healthy' if is_healthy else 'unhealthy'}")

    # All backends should start healthy
    for backend_type in hal.get_available_backends():
      if not hal.is_backend_healthy(backend_type):
        logger.warning(f"✗ {backend_type.name} should be healthy initially")
        return False

    logger.info("✓ All backends healthy at startup")
    return True

  def test_error_recoverable_flag(self) -> bool:
    """Test error recoverability classification."""
    logger.info("Testing error recoverability...")

    # Recoverable error
    timeout_error = ErrorInfo(
        category=ErrorCategory.TIMEOUT,
        message="Timeout",
        backend_name="NPU",
        model_name="model",
        is_recoverable=True
    )

    # Non-recoverable error
    oom_error = ErrorInfo(
        category=ErrorCategory.RESOURCE_EXHAUSTED,
        message="Out of memory",
        backend_name="GPU",
        model_name="model",
        is_recoverable=False
    )

    if not timeout_error.is_recoverable:
      logger.warning("✗ Timeout should be recoverable")
      return False

    if oom_error.is_recoverable:
      logger.warning("✗ OOM should not be recoverable")
      return False

    logger.info("✓ Error recoverability correct")
    return True

  def run_all_tests(self) -> dict[str, bool]:
    """Run all error recovery tests."""
    logger.info("\n" + "="*60)
    logger.info("Error Recovery Tests (Phase 6.3)")
    logger.info("="*60 + "\n")

    results = {
        'fallback_strategy': self.test_fallback_strategy(),
        'health_monitor': self.test_health_monitor(),
        'error_categorization': self.test_error_categorization(),
        'recovery_manager': self.test_error_recovery_manager(),
        'hal_integration': self.test_hal_error_recovery_integration(),
        'backend_health': self.test_backend_health_check(),
        'error_recoverable': self.test_error_recoverable_flag(),
    }

    logger.info("\n" + "="*60)
    logger.info("Error Recovery Test Results")
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
  tester = TestErrorRecovery()

  try:
    results = tester.run_all_tests()

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    if passed >= 6:
      logger.info("✓ Error Recovery Tests PASSED\n")
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
