#!/usr/bin/env python3
"""
Error Recovery and Fallback Mechanisms for Inference HAL

Handles:
- Fallback chains (GPU → CPU for ACL)
- Auto-restart of hung backends
- Graceful degradation
- Comprehensive error logging
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional, Callable

logger = logging.getLogger(__name__)


class ErrorCategory(IntEnum):
    """Inference error categories."""
    TIMEOUT = 1
    RESOURCE_EXHAUSTED = 2
    MODEL_NOT_FOUND = 3
    INVALID_INPUT = 4
    BACKEND_CRASH = 5
    UNKNOWN = 99


@dataclass
class ErrorInfo:
    """Detailed error information."""
    category: ErrorCategory
    message: str
    backend_name: str = ""
    model_name: str = ""
    timestamp: float = field(default_factory=time.monotonic)
    is_recoverable: bool = True
    retry_count: int = 0
    suggested_action: str = ""


class FallbackStrategy:
    """Fallback chain for operations (e.g., GPU → CPU)."""

    def __init__(self, primary_backend_name: str, fallback_backend_name: str):
        self.primary = primary_backend_name
        self.fallback = fallback_backend_name
        self.use_fallback = False
        self.failure_count = 0
        self.success_count = 0

    def should_try_fallback(self) -> bool:
        """Determine if should try fallback based on failure rate."""
        total = self.failure_count + self.success_count
        if total < 3:
            return False
        failure_rate = self.failure_count / total
        return failure_rate > 0.5

    def record_success(self):
        """Record successful operation."""
        self.success_count += 1
        self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self):
        """Record failed operation."""
        self.failure_count += 1

    def reset(self):
        """Reset fallback state."""
        self.use_fallback = False
        self.failure_count = 0
        self.success_count = 0


class BackendHealthMonitor:
    """Monitor backend health and detect crashes/hangs."""

    def __init__(self, backend_name: str, max_failures: int = 3):
        self.backend_name = backend_name
        self.max_failures = max_failures
        self.consecutive_failures = 0
        self.last_failure_time: Optional[float] = None
        self.is_healthy = True
        self._lock = threading.Lock()

    def record_success(self):
        """Record successful operation."""
        with self._lock:
            self.consecutive_failures = 0
            self.is_healthy = True

    def record_failure(self) -> bool:
        """Record failed operation. Returns True if backend should be restarted."""
        with self._lock:
            self.consecutive_failures += 1
            self.last_failure_time = time.monotonic()

            if self.consecutive_failures >= self.max_failures:
                self.is_healthy = False
                logger.error(
                    f"Backend {self.backend_name} marked unhealthy "
                    f"({self.consecutive_failures} consecutive failures)"
                )
                return True
            return False

    def reset(self):
        """Reset health monitor."""
        with self._lock:
            self.consecutive_failures = 0
            self.is_healthy = True
            self.last_failure_time = None

    def get_status(self) -> dict[str, Any]:
        """Get health status."""
        with self._lock:
            return {
                'backend_name': self.backend_name,
                'is_healthy': self.is_healthy,
                'consecutive_failures': self.consecutive_failures,
                'last_failure_time': self.last_failure_time,
            }


class ErrorRecoveryManager:
    """Manage error recovery, fallbacks, and auto-restart."""

    def __init__(self):
        self.fallback_strategies: dict[str, FallbackStrategy] = {}
        self.backend_monitors: dict[str, BackendHealthMonitor] = {}
        self.error_history: list[ErrorInfo] = []
        self.max_error_history = 1000
        self._lock = threading.Lock()

    def register_fallback_strategy(self, strategy: FallbackStrategy):
        """Register a fallback strategy."""
        strategy_name = f"{strategy.primary}→{strategy.fallback}"
        self.fallback_strategies[strategy_name] = strategy
        logger.info(f"Registered fallback strategy: {strategy_name}")

    def register_backend_monitor(self, monitor: BackendHealthMonitor):
        """Register a backend health monitor."""
        self.backend_monitors[monitor.backend_name] = monitor
        logger.info(f"Registered health monitor for {monitor.backend_name}")

    def record_error(self, error_info: ErrorInfo):
        """Record an error."""
        with self._lock:
            self.error_history.append(error_info)

            # Keep history size bounded
            if len(self.error_history) > self.max_error_history:
                self.error_history.pop(0)

        logger.error(
            f"[{error_info.category.name}] {error_info.message} "
            f"(model={error_info.model_name}, retry={error_info.retry_count})"
        )

    def get_fallback_backend(self, primary: str) -> Optional[str]:
        """Get fallback backend for a primary backend."""
        for strategy in self.fallback_strategies.values():
            if strategy.primary == primary and strategy.should_try_fallback():
                logger.warning(f"Falling back from {primary} to {strategy.fallback}")
                return strategy.fallback
        return None

    def is_backend_healthy(self, backend_name: str) -> bool:
        """Check if backend is healthy."""
        monitor = self.backend_monitors.get(backend_name)
        if monitor is None:
            return True  # Unknown backends assumed healthy
        return monitor.is_healthy

    def should_restart_backend(self, backend_name: str) -> bool:
        """Check if backend should be restarted."""
        monitor = self.backend_monitors.get(backend_name)
        if monitor is None:
            return False
        return not monitor.is_healthy

    def get_error_summary(self) -> dict[str, Any]:
        """Get summary of recent errors."""
        with self._lock:
            if not self.error_history:
                return {'total_errors': 0}

            errors_by_category = {}
            for error in self.error_history:
                cat = error.category.name
                errors_by_category[cat] = errors_by_category.get(cat, 0) + 1

            return {
                'total_errors': len(self.error_history),
                'errors_by_category': errors_by_category,
                'last_error': {
                    'message': self.error_history[-1].message,
                    'category': self.error_history[-1].category.name,
                    'timestamp': self.error_history[-1].timestamp,
                }
            }

    def get_backend_health_report(self) -> dict[str, Any]:
        """Get health status for all backends."""
        return {
            backend_name: monitor.get_status()
            for backend_name, monitor in self.backend_monitors.items()
        }


def create_error_from_exception(
    exc: Exception,
    backend_name: str,
    model_name: str
) -> ErrorInfo:
    """Create ErrorInfo from an exception."""
    exc_type = type(exc).__name__

    if 'Timeout' in exc_type:
        category = ErrorCategory.TIMEOUT
        message = f"Inference timeout: {str(exc)}"
        recoverable = True
    elif 'Memory' in exc_type or 'OOM' in str(exc):
        category = ErrorCategory.RESOURCE_EXHAUSTED
        message = f"Resource exhausted: {str(exc)}"
        recoverable = False
    elif 'NotFound' in exc_type or 'not found' in str(exc).lower():
        category = ErrorCategory.MODEL_NOT_FOUND
        message = f"Model not found: {str(exc)}"
        recoverable = False
    elif 'Invalid' in exc_type or 'Invalid' in str(exc):
        category = ErrorCategory.INVALID_INPUT
        message = f"Invalid input: {str(exc)}"
        recoverable = False
    else:
        category = ErrorCategory.UNKNOWN
        message = f"Unexpected error: {str(exc)}"
        recoverable = True

    return ErrorInfo(
        category=category,
        message=message,
        backend_name=backend_name,
        model_name=model_name,
        is_recoverable=recoverable,
    )
