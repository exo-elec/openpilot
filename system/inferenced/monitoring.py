#!/usr/bin/env python3
"""
Monitoring and Diagnostics for Inference HAL

Provides:
- Health check endpoints
- Performance metrics collection
- Diagnostic status reporting
- Alert thresholds and degradation detection
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Callable
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Performance metrics for an operation."""
    operation_name: str
    backend_name: str
    min_latency_ms: float = float('inf')
    max_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    total_time_ms: float = 0.0
    last_updated: float = field(default_factory=time.monotonic)

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_operations == 0:
            return 0.0
        return (self.successful_operations / self.total_operations) * 100

    @property
    def throughput_ops_sec(self) -> float:
        """Calculate throughput in operations per second."""
        if self.total_time_ms == 0:
            return 0.0
        return (self.total_operations / self.total_time_ms) * 1000


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    is_healthy: bool
    message: str
    timestamp: float = field(default_factory=time.monotonic)
    details: dict[str, Any] = field(default_factory=dict)


class PerformanceMonitor:
    """Monitor performance metrics for operations."""

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.metrics: dict[str, PerformanceMetrics] = {}
        self.latency_history: dict[str, deque] = {}
        self._lock = threading.Lock()

    def record_operation(
        self,
        operation_name: str,
        backend_name: str,
        latency_ms: float,
        success: bool
    ):
        """Record an operation's performance."""
        with self._lock:
            key = f"{backend_name}:{operation_name}"

            if key not in self.metrics:
                self.metrics[key] = PerformanceMetrics(
                    operation_name=operation_name,
                    backend_name=backend_name
                )
                self.latency_history[key] = deque(maxlen=self.window_size)

            metrics = self.metrics[key]
            metrics.total_operations += 1
            metrics.total_time_ms += latency_ms
            metrics.min_latency_ms = min(metrics.min_latency_ms, latency_ms)
            metrics.max_latency_ms = max(metrics.max_latency_ms, latency_ms)
            metrics.avg_latency_ms = metrics.total_time_ms / metrics.total_operations

            if success:
                metrics.successful_operations += 1
            else:
                metrics.failed_operations += 1

            metrics.last_updated = time.monotonic()
            self.latency_history[key].append(latency_ms)

    def get_metrics(self, operation_name: str, backend_name: str) -> Optional[PerformanceMetrics]:
        """Get metrics for an operation."""
        with self._lock:
            key = f"{backend_name}:{operation_name}"
            return self.metrics.get(key)

    def get_all_metrics(self) -> dict[str, PerformanceMetrics]:
        """Get all collected metrics."""
        with self._lock:
            refs = dict(self.metrics)  # fast shallow copy under lock
        return copy.deepcopy(refs)     # deepcopy after lock released — record_operation() unblocked

    def get_summary(self) -> dict[str, Any]:
        """Get summary of all metrics."""
        with self._lock:
            if not self.metrics:
                return {'total_operations': 0}

            total_ops = sum(m.total_operations for m in self.metrics.values())
            total_success = sum(m.successful_operations for m in self.metrics.values())
            avg_latency = sum(m.avg_latency_ms * m.total_operations for m in self.metrics.values()) / total_ops if total_ops > 0 else 0

            return {
                'total_operations': total_ops,
                'successful_operations': total_success,
                'failed_operations': total_ops - total_success,
                'overall_success_rate': (total_success / total_ops * 100) if total_ops > 0 else 0,
                'average_latency_ms': avg_latency,
                'num_tracked_operations': len(self.metrics),
            }

    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self.metrics.clear()
            self.latency_history.clear()


class HealthChecker:
    """Perform health checks on backends and overall system."""

    def __init__(self):
        self.health_check_functions: dict[str, Callable[[], HealthCheckResult]] = {}
        self._lock = threading.Lock()

    def register_check(self, name: str, check_fn: Callable[[], HealthCheckResult]):
        """Register a health check function."""
        with self._lock:
            self.health_check_functions[name] = check_fn
            logger.info(f"Registered health check: {name}")

    def run_check(self, name: str) -> Optional[HealthCheckResult]:
        """Run a specific health check."""
        with self._lock:
            check_fn = self.health_check_functions.get(name)
            if check_fn is None:
                return None

        try:
            return check_fn()
        except Exception as e:
            logger.error(f"Health check '{name}' failed: {e}")
            return HealthCheckResult(
                is_healthy=False,
                message=f"Health check failed: {e}"
            )

    def run_all_checks(self) -> dict[str, HealthCheckResult]:
        """Run all registered health checks."""
        with self._lock:
            check_names = list(self.health_check_functions.keys())

        results = {}
        for name in check_names:
            result = self.run_check(name)
            if result:
                results[name] = result

        return results

    def get_overall_health(self) -> bool:
        """Check if all systems are healthy."""
        results = self.run_all_checks()
        return all(r.is_healthy for r in results.values())


class AlertThresholds:
    """Alert thresholds for degraded performance."""

    def __init__(self):
        self.latency_warning_ms: float = 50.0
        self.latency_critical_ms: float = 100.0
        self.success_rate_warning: float = 95.0  # percent
        self.success_rate_critical: float = 90.0  # percent
        self.error_rate_warning: float = 5.0  # percent
        self.error_rate_critical: float = 10.0  # percent

    def check_metrics(self, metrics: PerformanceMetrics) -> dict[str, Any]:
        """Check metrics against thresholds."""
        alerts = []

        # Latency checks
        if metrics.avg_latency_ms > self.latency_critical_ms:
            alerts.append({
                'severity': 'CRITICAL',
                'type': 'HIGH_LATENCY',
                'message': f"High latency: {metrics.avg_latency_ms:.1f}ms (threshold: {self.latency_critical_ms}ms)"
            })
        elif metrics.avg_latency_ms > self.latency_warning_ms:
            alerts.append({
                'severity': 'WARNING',
                'type': 'HIGH_LATENCY',
                'message': f"Elevated latency: {metrics.avg_latency_ms:.1f}ms (threshold: {self.latency_warning_ms}ms)"
            })

        # Success rate checks
        if metrics.success_rate < self.success_rate_critical:
            alerts.append({
                'severity': 'CRITICAL',
                'type': 'LOW_SUCCESS_RATE',
                'message': f"Low success rate: {metrics.success_rate:.1f}% (threshold: {self.success_rate_critical}%)"
            })
        elif metrics.success_rate < self.success_rate_warning:
            alerts.append({
                'severity': 'WARNING',
                'type': 'LOW_SUCCESS_RATE',
                'message': f"Degraded success rate: {metrics.success_rate:.1f}% (threshold: {self.success_rate_warning}%)"
            })

        return {
            'operation': f"{metrics.backend_name}:{metrics.operation_name}",
            'alerts': alerts,
            'metrics': {
                'latency_ms': metrics.avg_latency_ms,
                'success_rate': metrics.success_rate,
                'total_operations': metrics.total_operations,
            }
        }


class DiagnosticReport:
    """Generate comprehensive diagnostic reports."""

    def __init__(
        self,
        performance_monitor: PerformanceMonitor,
        health_checker: HealthChecker,
        alert_thresholds: AlertThresholds
    ):
        self.performance_monitor = performance_monitor
        self.health_checker = health_checker
        self.alert_thresholds = alert_thresholds

    def generate_report(self) -> dict[str, Any]:
        """Generate comprehensive diagnostic report."""
        health_results = self.health_checker.run_all_checks()
        performance_summary = self.performance_monitor.get_summary()

        # Check for alerts
        all_metrics = self.performance_monitor.get_all_metrics()
        alerts_by_operation = {}
        critical_alerts = []

        for metrics in all_metrics.values():
            alert_result = self.alert_thresholds.check_metrics(metrics)
            if alert_result['alerts']:
                alerts_by_operation[alert_result['operation']] = alert_result['alerts']
                for alert in alert_result['alerts']:
                    if alert['severity'] == 'CRITICAL':
                        critical_alerts.append(alert)

        overall_health = self.health_checker.get_overall_health()
        has_critical_alerts = len(critical_alerts) > 0

        return {
            'timestamp': time.monotonic(),
            'overall_health': overall_health and not has_critical_alerts,
            'health_checks': {
                name: {
                    'is_healthy': result.is_healthy,
                    'message': result.message,
                    'details': result.details
                }
                for name, result in health_results.items()
            },
            'performance': performance_summary,
            'alerts': {
                'critical': critical_alerts,
                'by_operation': alerts_by_operation,
            },
            'status': 'HEALTHY' if (overall_health and not has_critical_alerts) else (
                'DEGRADED' if alerts_by_operation else 'UNKNOWN'
            )
        }

    def print_report(self):
        """Print diagnostic report to logger."""
        report = self.generate_report()

        logger.info("\n" + "="*70)
        logger.info("INFERENCE HAL DIAGNOSTIC REPORT")
        logger.info("="*70)

        logger.info(f"\nStatus: {report['status']}")
        logger.info(f"Overall Health: {'✓ HEALTHY' if report['overall_health'] else '✗ UNHEALTHY'}")

        logger.info("\n--- Health Checks ---")
        for name, result in report['health_checks'].items():
            status = "✓" if result['is_healthy'] else "✗"
            logger.info(f"  {status} {name}: {result['message']}")

        logger.info("\n--- Performance ---")
        perf = report['performance']
        logger.info(f"  Total Operations: {perf['total_operations']}")
        logger.info(f"  Success Rate: {perf['overall_success_rate']:.1f}%")
        logger.info(f"  Average Latency: {perf['average_latency_ms']:.2f}ms")

        if report['alerts']['critical']:
            logger.info("\n--- Critical Alerts ---")
            for alert in report['alerts']['critical']:
                logger.error(f"  🔴 {alert['type']}: {alert['message']}")

        if report['alerts']['by_operation']:
            logger.info("\n--- Operation Alerts ---")
            for op, alerts in report['alerts']['by_operation'].items():
                logger.warning(f"  {op}:")
                for alert in alerts:
                    logger.warning(f"    {alert['severity']}: {alert['message']}")

        logger.info("="*70 + "\n")
