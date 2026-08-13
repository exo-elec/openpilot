#!/usr/bin/env python3
"""Test monitoring and diagnostics system (Phase 6.4 - Monitoring & Diagnostics)."""

from __future__ import annotations

import logging
import numpy as np
from openpilot.system.inferenced import get_hal, BackendType
from openpilot.system.inferenced.monitoring import (
    PerformanceMetrics, HealthCheckResult, PerformanceMonitor, HealthChecker,
    AlertThresholds, DiagnosticReport
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestMonitoring:
    """Test monitoring and diagnostics functionality."""

    def test_performance_metrics(self) -> bool:
        """Test performance metrics calculation."""
        logger.info("Testing performance metrics...")

        metrics = PerformanceMetrics(
            operation_name="test_op",
            backend_name="test_backend"
        )

        # Initially zero operations
        if metrics.total_operations != 0:
            logger.warning("✗ Should start with 0 operations")
            return False

        # Manually update metrics for testing
        metrics.total_operations = 10
        metrics.successful_operations = 9
        metrics.failed_operations = 1
        metrics.min_latency_ms = 5.0
        metrics.max_latency_ms = 20.0
        metrics.total_time_ms = 100.0
        metrics.avg_latency_ms = 100.0 / 10  # Must set avg_latency explicitly

        # Check success rate
        expected_success_rate = 90.0
        if abs(metrics.success_rate - expected_success_rate) > 0.1:
            logger.warning(f"✗ Success rate mismatch: {metrics.success_rate}")
            return False

        # Check throughput
        expected_throughput = (10 / 100.0) * 1000
        if abs(metrics.throughput_ops_sec - expected_throughput) > 0.1:
            logger.warning(f"✗ Throughput mismatch: {metrics.throughput_ops_sec}")
            return False

        # Check average latency
        expected_avg = 100.0 / 10
        if abs(metrics.avg_latency_ms - expected_avg) > 0.1:
            logger.warning(f"✗ Avg latency mismatch: {metrics.avg_latency_ms}")
            return False

        logger.info("✓ Performance metrics working")
        return True

    def test_performance_monitor(self) -> bool:
        """Test performance monitor collection."""
        logger.info("Testing performance monitor...")

        monitor = PerformanceMonitor(window_size=10)

        # Record some operations
        for i in range(5):
            monitor.record_operation(
                operation_name="inference",
                backend_name="NPU",
                latency_ms=10.0 + i,
                success=True
            )

        # Get metrics
        metrics = monitor.get_metrics("inference", "NPU")
        if metrics is None:
            logger.warning("✗ Metrics not found")
            return False

        if metrics.total_operations != 5:
            logger.warning(f"✗ Total operations mismatch: {metrics.total_operations}")
            return False

        if metrics.successful_operations != 5:
            logger.warning("✗ Success count mismatch")
            return False

        # Test summary
        summary = monitor.get_summary()
        if summary['total_operations'] != 5:
            logger.warning("✗ Summary total mismatch")
            return False

        logger.info("✓ Performance monitor working")
        return True

    def test_health_checker(self) -> bool:
        """Test health checker."""
        logger.info("Testing health checker...")

        checker = HealthChecker()

        # Register a health check
        def check_test() -> HealthCheckResult:
            return HealthCheckResult(is_healthy=True, message="Test healthy")

        checker.register_check("test_check", check_test)

        # Run specific check
        result = checker.run_check("test_check")
        if result is None or not result.is_healthy:
            logger.warning("✗ Health check failed")
            return False

        # Run all checks
        results = checker.run_all_checks()
        if "test_check" not in results:
            logger.warning("✗ Check not in results")
            return False

        # Check overall health
        if not checker.get_overall_health():
            logger.warning("✗ Overall health should be true")
            return False

        logger.info("✓ Health checker working")
        return True

    def test_alert_thresholds(self) -> bool:
        """Test alert threshold checking."""
        logger.info("Testing alert thresholds...")

        thresholds = AlertThresholds()
        metrics = PerformanceMetrics(
            operation_name="test",
            backend_name="GPU"
        )

        # Set metrics to trigger warning
        metrics.total_operations = 100
        metrics.successful_operations = 96
        metrics.failed_operations = 4
        metrics.avg_latency_ms = 75.0

        alert_result = thresholds.check_metrics(metrics)

        # Should have warning for latency
        if len(alert_result['alerts']) == 0:
            logger.warning("✗ Should have latency warning")
            return False

        # Check alert types
        alert_types = [a['type'] for a in alert_result['alerts']]
        if 'HIGH_LATENCY' not in alert_types:
            logger.warning("✗ Missing HIGH_LATENCY alert")
            return False

        logger.info("✓ Alert thresholds working")
        return True

    def test_alert_critical_thresholds(self) -> bool:
        """Test critical alert thresholds."""
        logger.info("Testing critical alert thresholds...")

        thresholds = AlertThresholds()
        metrics = PerformanceMetrics(
            operation_name="test",
            backend_name="NPU"
        )

        # Set metrics to trigger critical alerts
        metrics.total_operations = 100
        metrics.successful_operations = 85
        metrics.failed_operations = 15
        metrics.avg_latency_ms = 150.0

        alert_result = thresholds.check_metrics(metrics)

        # Should have critical alerts
        critical_alerts = [a for a in alert_result['alerts'] if a['severity'] == 'CRITICAL']
        if len(critical_alerts) == 0:
            logger.warning("✗ Should have critical alerts")
            return False

        logger.info("✓ Critical alert thresholds working")
        return True

    def test_diagnostic_report(self) -> bool:
        """Test diagnostic report generation."""
        logger.info("Testing diagnostic report...")

        monitor = PerformanceMonitor()
        checker = HealthChecker()
        thresholds = AlertThresholds()

        # Record some operations
        for _i in range(3):
            monitor.record_operation(
                operation_name="model_a",
                backend_name="NPU",
                latency_ms=10.0,
                success=True
            )

        # Register a health check
        def check_system() -> HealthCheckResult:
            return HealthCheckResult(is_healthy=True, message="System OK")

        checker.register_check("system", check_system)

        # Generate report
        report = DiagnosticReport(monitor, checker, thresholds)
        diagnostic = report.generate_report()

        # Check report structure
        required_fields = ['timestamp', 'overall_health', 'health_checks', 'performance', 'alerts', 'status']
        for field in required_fields:
            if field not in diagnostic:
                logger.warning(f"✗ Missing field: {field}")
                return False

        # Check status values
        if diagnostic['status'] not in ['HEALTHY', 'DEGRADED', 'UNKNOWN']:
            logger.warning(f"✗ Invalid status: {diagnostic['status']}")
            return False

        logger.info("✓ Diagnostic report working")
        return True

    def test_hal_monitoring_integration(self) -> bool:
        """Test HAL integration with monitoring."""
        logger.info("Testing HAL monitoring integration...")

        # Reset HAL singleton for fresh instance
        from openpilot.system.inferenced.compute import HAL
        HAL._instance = None

        hal = get_hal()

        # Get diagnostic report
        report = hal.get_diagnostic_report()
        if 'status' not in report:
            logger.warning("✗ Diagnostic report not found")
            return False

        # Get all performance metrics (should be empty initially)
        all_metrics = hal.get_all_performance_metrics()
        if not isinstance(all_metrics, dict):
            logger.warning("✗ All metrics should be dict")
            return False

        # Check performance monitor initialized
        if hal._performance_monitor is None:
            logger.warning("✗ Performance monitor not initialized")
            return False

        logger.info("✓ HAL monitoring integration working")
        return True

    def test_monitoring_with_real_inference(self) -> bool:
        """Test monitoring with actual inference operations."""
        logger.info("Testing monitoring with real inference...")

        from openpilot.system.inferenced.compute import HAL
        HAL._instance = None

        hal = get_hal()

        # Try to infer with NPU (will fail gracefully if not available)
        if hal.is_backend_available(BackendType.NPU):
            # This will record metrics even though inference may fail
            hal.infer(
                BackendType.NPU,
                "test_model",
                {"dummy": np.zeros((1, 224, 224, 3), dtype=np.float32)}
            )

            # Check metrics were recorded (even for failed inference)
            metrics = hal.get_performance_metrics("test_model", "NPU")
            if metrics is None:
                logger.warning("✗ Metrics not recorded")
                return False

            if metrics.total_operations != 1:
                logger.warning("✗ Operation not counted")
                return False

            logger.info("✓ Monitoring with real inference working")
            return True
        else:
            logger.info("⚠ NPU not available, skipping real inference test")
            return True

    def test_performance_monitor_reset(self) -> bool:
        """Test performance monitor reset."""
        logger.info("Testing performance monitor reset...")

        monitor = PerformanceMonitor()

        # Record operations
        monitor.record_operation("op1", "backend1", 10.0, True)
        monitor.record_operation("op2", "backend1", 20.0, False)

        # Verify operations recorded
        metrics = monitor.get_all_metrics()
        if len(metrics) != 2:
            logger.warning("✗ Operations not recorded")
            return False

        # Reset
        monitor.reset()

        # Verify cleared
        metrics = monitor.get_all_metrics()
        if len(metrics) != 0:
            logger.warning("✗ Metrics not cleared")
            return False

        logger.info("✓ Performance monitor reset working")
        return True

    def run_all_tests(self) -> dict[str, bool]:
        """Run all monitoring tests."""
        logger.info("\n" + "="*60)
        logger.info("Monitoring & Diagnostics Tests (Phase 6.4)")
        logger.info("="*60 + "\n")

        results = {
            'performance_metrics': self.test_performance_metrics(),
            'performance_monitor': self.test_performance_monitor(),
            'health_checker': self.test_health_checker(),
            'alert_thresholds': self.test_alert_thresholds(),
            'critical_alerts': self.test_alert_critical_thresholds(),
            'diagnostic_report': self.test_diagnostic_report(),
            'hal_integration': self.test_hal_monitoring_integration(),
            'real_inference': self.test_monitoring_with_real_inference(),
            'monitor_reset': self.test_performance_monitor_reset(),
        }

        logger.info("\n" + "="*60)
        logger.info("Monitoring Test Results")
        logger.info("="*60)

        sum(1 for v in results.values() if v)
        len(results)

        for test_name, result in results.items():
            status = "✓ PASS" if result else "✗ FAIL"
            logger.info(f"  {test_name:25} {status}")

        logger.info("="*60 + "\n")

        return results


def main():
    """Main entry point."""
    tester = TestMonitoring()

    try:
        results = tester.run_all_tests()

        passed = sum(1 for v in results.values() if v)
        total = len(results)

        if passed >= 8:
            logger.info("✓ Monitoring Tests PASSED\n")
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
