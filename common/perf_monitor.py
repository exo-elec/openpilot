#!/usr/bin/env python3
"""
Unified Performance Monitoring - Centralized metrics collection for all daemons.

Provides consistent performance tracking across:
- stereod (stereo depth)
- pointcloudd (point cloud processing)
- gridd (BEV grid generation)
- recordd (video recording)
- modeld (neural network inference)

Features:
- Latency tracking with circular buffers
- FPS calculation
- Throughput metrics
- Automatic stats publishing to cereal
- Thread-safe operations

Usage:
    from common.perf_monitor import PerformanceMonitor, LatencyTimer

    # Global monitor instance
    monitor = PerformanceMonitor("stereod")

    # Record latency
    with LatencyTimer(monitor, "sgm"):
        result = sgm.compute(left, right)

    # Or manual recording
    monitor.record_latency("preprocess", latency_ms=5.2)
    monitor.record_fps("camera", fps=20.0)

    # Get stats
    stats = monitor.get_stats()

    # Publish to cereal (for UI/monitoring)
    monitor.publish(pm)
"""
from __future__ import annotations

import time
import logging
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class LatencyStats:
    """Statistics for a single operation's latency."""
    count: int = 0
    total_ms: float = 0.0
    min_ms: float = float('inf')
    max_ms: float = 0.0
    avg_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0

    # Circular buffer for percentile calculation
    _samples: deque = field(default_factory=lambda: deque(maxlen=1000))

    def record(self, latency_ms: float) -> None:
        """Record a latency sample."""
        self.count += 1
        self.total_ms += latency_ms
        self.min_ms = min(self.min_ms, latency_ms)
        self.max_ms = max(self.max_ms, latency_ms)
        self._samples.append(latency_ms)
        self._update_stats()

    def _update_stats(self) -> None:
        """Update derived statistics."""
        if self.count == 0:
            return

        self.avg_ms = self.total_ms / self.count

        if len(self._samples) >= 10:
            sorted_samples = sorted(self._samples)
            n = len(sorted_samples)
            self.p95_ms = sorted_samples[int(n * 0.95)]
            self.p99_ms = sorted_samples[int(n * 0.99)]
        else:
            self.p95_ms = self.avg_ms
            self.p99_ms = self.avg_ms

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary."""
        return {
            'count': self.count,
            'avg_ms': round(self.avg_ms, 2),
            'min_ms': round(self.min_ms, 2) if self.min_ms != float('inf') else 0.0,
            'max_ms': round(self.max_ms, 2),
            'p95_ms': round(self.p95_ms, 2),
            'p99_ms': round(self.p99_ms, 2),
        }


@dataclass
class FPSStats:
    """Statistics for frames per second."""
    frame_count: int = 0
    fps: float = 0.0
    target_fps: float = 20.0

    _last_time: float = field(default_factory=time.time)  # noqa: TID251
    _frame_times: deque = field(default_factory=lambda: deque(maxlen=100))

    def record_frame(self, timestamp: float | None = None) -> None:
        """Record a frame occurrence."""
        now = timestamp or time.monotonic()
        self.frame_count += 1

        if self._last_time > 0:
            dt = now - self._last_time
            if dt > 0:
                self._frame_times.append(1.0 / dt)

        self._last_time = now
        self._update_fps()

    def _update_fps(self) -> None:
        """Update FPS calculation."""
        if len(self._frame_times) >= 5:
            self.fps = statistics.mean(self._frame_times)
        elif self._frame_times:
            self.fps = statistics.mean(self._frame_times)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            'frame_count': self.frame_count,
            'fps': round(self.fps, 2),
            'target_fps': self.target_fps,
            'dropped_frames': max(0, int(self.frame_count - self.fps * self.frame_count / self.target_fps)) if self.target_fps > 0 else 0,
        }


class PerformanceMonitor:
    """
    Centralized performance monitor for daemons.

    Thread-safe performance tracking with automatic statistics calculation.
    """

    def __init__(self, component: str, buffer_size: int = 1000):
        """
        Initialize performance monitor.

        Args:
            component: Component name (e.g., "stereod", "gridd")
            buffer_size: Size of circular buffers for samples
        """
        self.component = component
        self._buffer_size = buffer_size

        # Latency tracking per operation
        self._latency: dict[str, LatencyStats] = {}
        self._latency_lock = Lock()

        # FPS tracking per stream
        self._fps: dict[str, FPSStats] = {}
        self._fps_lock = Lock()

        # Custom counters
        self._counters: dict[str, int] = {}
        self._counters_lock = Lock()

        # Start time
        self._start_time = time.monotonic()

        logger.debug(f"PerformanceMonitor initialized for {component}")

    def record_latency(self, operation: str, latency_ms: float) -> None:
        """
        Record latency for an operation.

        Args:
            operation: Operation name (e.g., "sgm", "reproject", "costmap")
            latency_ms: Latency in milliseconds
        """
        with self._latency_lock:
            if operation not in self._latency:
                self._latency[operation] = LatencyStats()
            self._latency[operation].record(latency_ms)

    def record_fps(self, stream: str, timestamp: float | None = None) -> None:
        """
        Record a frame for FPS calculation.

        Args:
            stream: Stream name (e.g., "camera", "output")
            timestamp: Optional timestamp (default: current time)
        """
        with self._fps_lock:
            if stream not in self._fps:
                self._fps[stream] = FPSStats()
            self._fps[stream].record_frame(timestamp)

    def set_target_fps(self, stream: str, target: float) -> None:
        """Set target FPS for a stream (for drop calculation)."""
        with self._fps_lock:
            if stream not in self._fps:
                self._fps[stream] = FPSStats()
            self._fps[stream].target_fps = target

    def increment_counter(self, name: str, value: int = 1) -> None:
        """Increment a custom counter."""
        with self._counters_lock:
            self._counters[name] = self._counters.get(name, 0) + value

    def get_latency_stats(self, operation: str | None = None) -> dict[str, Any]:
        """
        Get latency statistics.

        Args:
            operation: Specific operation (None for all operations)

        Returns:
            Dictionary of latency statistics
        """
        with self._latency_lock:
            if operation:
                if operation in self._latency:
                    return {operation: self._latency[operation].to_dict()}
                return {}

            return {op: stats.to_dict() for op, stats in self._latency.items()}

    def get_fps_stats(self, stream: str | None = None) -> dict[str, Any]:
        """Get FPS statistics."""
        with self._fps_lock:
            if stream:
                if stream in self._fps:
                    return {stream: self._fps[stream].to_dict()}
                return {}

            return {s: stats.to_dict() for s, stats in self._fps.items()}

    def get_stats(self) -> dict[str, Any]:
        """Get all performance statistics."""
        uptime_sec = time.monotonic() - self._start_time

        with self._latency_lock, self._fps_lock, self._counters_lock:
            return {
                'component': self.component,
                'uptime_sec': round(uptime_sec, 2),
                'latency': {op: stats.to_dict() for op, stats in self._latency.items()},
                'fps': {s: stats.to_dict() for s, stats in self._fps.items()},
                'counters': dict(self._counters),
            }

    def reset(self) -> None:
        """Reset all statistics."""
        with self._latency_lock, self._fps_lock, self._counters_lock:
            self._latency.clear()
            self._fps.clear()
            self._counters.clear()
            self._start_time = time.monotonic()

    def is_healthy(self, max_latency_ms: float | None = None) -> bool:
        """
        Check if performance is healthy.

        Args:
            max_latency_ms: Maximum acceptable latency (None to skip check)

        Returns:
            True if performance is within acceptable bounds
        """
        if max_latency_ms is None:
            return True

        with self._latency_lock:
            for op, stats in self._latency.items():
                if stats.avg_ms > max_latency_ms:
                    logger.warning(f"{self.component}.{op} avg latency {stats.avg_ms:.1f}ms exceeds {max_latency_ms}ms")
                    return False

        return True

    def log_summary(self, level: int = logging.INFO) -> None:
        """Log performance summary."""
        stats = self.get_stats()

        logger.log(level, f"=== {self.component} Performance Summary ===")
        logger.log(level, f"Uptime: {stats['uptime_sec']:.1f}s")

        if stats['latency']:
            logger.log(level, "Latency:")
            for op, lat in stats['latency'].items():
                logger.log(level, f"  {op}: {lat['avg_ms']:.1f}ms avg, {lat['p95_ms']:.1f}ms p95")

        if stats['fps']:
            logger.log(level, "FPS:")
            for stream, fps in stats['fps'].items():
                logger.log(level, f"  {stream}: {fps['fps']:.1f} fps (target: {fps['target_fps']})")

        if stats['counters']:
            logger.log(level, "Counters:")
            for name, value in stats['counters'].items():
                logger.log(level, f"  {name}: {value}")


class LatencyTimer:
    """
    Context manager for timing operations.

    Usage:
        monitor = PerformanceMonitor("stereod")

        with LatencyTimer(monitor, "sgm"):
            result = sgm.compute(left, right)

        # Or with custom callback
        with LatencyTimer(monitor, "custom", on_record=logger.debug):
            do_work()
    """

    def __init__(
        self,
        monitor: PerformanceMonitor,
        operation: str,
        on_record: Callable[[str, float | None, None]] = None
    ):
        self.monitor = monitor
        self.operation = operation
        self.on_record = on_record
        self._start_time: float | None = None

    def __enter__(self) -> LatencyTimer:
        self._start_time = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._start_time is not None:
            elapsed_ms = (time.monotonic() - self._start_time) * 1000
            self.monitor.record_latency(self.operation, elapsed_ms)

            if self.on_record:
                self.on_record(self.operation, elapsed_ms)


# Global registry for component monitors
_monitors: dict[str, PerformanceMonitor] = {}
_registry_lock = Lock()


def get_monitor(component: str) -> PerformanceMonitor:
    """
    Get or create a monitor for a component.

    This allows shared access to monitors across modules.

    Args:
        component: Component name

    Returns:
        PerformanceMonitor instance
    """
    with _registry_lock:
        if component not in _monitors:
            _monitors[component] = PerformanceMonitor(component)
        return _monitors[component]


def get_all_stats() -> dict[str, dict[str, Any]]:
    """Get statistics from all registered monitors."""
    with _registry_lock:
        return {name: monitor.get_stats() for name, monitor in _monitors.items()}


def reset_all() -> None:
    """Reset all monitors."""
    with _registry_lock:
        for monitor in _monitors.values():
            monitor.reset()


def log_all_summaries(level: int = logging.INFO) -> None:
    """Log summaries from all monitors."""
    with _registry_lock:
        for monitor in _monitors.values():
            monitor.log_summary(level)


# Convenience decorator
def timed(operation: str | None = None):
    """
    Decorator to time function execution.

    Usage:
        monitor = PerformanceMonitor("stereod")

        @timed("sgm")
        def compute_sgm(left, right):
            return sgm.compute(left, right)

    Args:
        operation: Operation name (default: function name)
    """
    def decorator(func: Callable) -> Callable:
        op_name = operation or func.__name__

        def wrapper(*args, **kwargs):
            # Try to find monitor in args or use global
            monitor = None
            for arg in args:
                if isinstance(arg, PerformanceMonitor):
                    monitor = arg
                    break

            if monitor is None:
                # Try to get from kwargs
                monitor = kwargs.get('monitor')

            if monitor:
                with LatencyTimer(monitor, op_name):
                    return func(*args, **kwargs)
            else:
                return func(*args, **kwargs)

        return wrapper
    return decorator


# Test
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("Testing PerformanceMonitor...")

    # Create monitor
    monitor = PerformanceMonitor("test")

    # Record some latencies
    for i in range(100):
        monitor.record_latency("op1", 10.0 + i * 0.1)
        monitor.record_latency("op2", 5.0 + i * 0.05)

    # Record FPS
    for _ in range(100):
        monitor.record_fps("stream1")
        time.sleep(0.01)  # ~100 fps

    # Increment counters
    monitor.increment_counter("frames")
    monitor.increment_counter("errors", 5)

    # Get stats
    stats = monitor.get_stats()
    print(f"\nStats: {stats}")

    # Test timer
    with LatencyTimer(monitor, "timed_op"):
        time.sleep(0.01)

    # Log summary
    monitor.log_summary()

    # Test global registry
    monitor2 = get_monitor("test2")
    monitor2.record_latency("op", 1.0)

    all_stats = get_all_stats()
    print(f"\nAll stats: {list(all_stats.keys())}")

    print("\n✓ PerformanceMonitor test complete!")
