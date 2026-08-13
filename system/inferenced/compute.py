#!/usr/bin/env python3
"""
Compute Hardware Abstraction Layer (HAL)

Unified interface to all compute accelerators:
- NPU: Neural Processing Unit (RKNN, Hailo)
- GPU: Graphics Processing Unit (Mali OpenCL via ACL)
- CPU: ARM CPU with NEON (via ACL)
- RGA: 2D Graphics Accelerator (Rockchip)
- MPP: Media Process Platform (Rockchip)

ACL Integration:
- Unified ACL backend handles both GPU (OpenCL) and CPU (NEON)
- Auto-selects best backend based on operation size
- SGM stereo depth via ACL (replaces custom OpenCL)

Single file containing: base classes + HAL + scheduler + selector
"""

from __future__ import annotations

import time
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import IntEnum, Enum
from typing import Any

from openpilot.system.inferenced.compute_recovery import (
    ErrorRecoveryManager, BackendHealthMonitor, ErrorCategory, ErrorInfo, create_error_from_exception
)
from openpilot.system.inferenced.monitoring import (
    PerformanceMonitor, HealthChecker, AlertThresholds, DiagnosticReport,
    HealthCheckResult
)

logger = logging.getLogger(__name__)


# ============================================================================
# Base Types
# ============================================================================

class BackendType(IntEnum):
    """Hardware backend types."""
    NONE = 0
    NPU = 1       # Neural Processing Unit (Rockchip RKNN)
    ACL = 2       # ARM Compute Library (Mali GPU or ARM NEON CPU)
    RGA = 4       # 2D Graphics Accelerator (Rockchip RGA)
    MPP = 5       # Media Process Platform (Rockchip MPP)
    HAILO_8 = 6   # Hailo-8 NPU (26 TOPS, camera inference)
    ONNX = 7      # ONNX Runtime (x86 dev PC / CPU fallback)
    DX_M1 = 8     # DeepX DX-M1 PCIe AI accelerator (20 TOPS, camera inference tier)


class WorkloadClass:
    """Workload class constants for backend routing.

    SAFETY_INFERENCE — Rockchip RKNN (SoC NPU) only. Driving vision, lane/seg.
                       No fallback — if RKNN absent the system cannot operate.

    CAMERA_INFERENCE — Hailo-8 or DX-M1 (stateless, no on-device DRAM — uses host RAM).
                       Side/rear BSD, AVM. Degrades if no card. Independent of voice tier.

    VOICE_INFERENCE  — AX-M1 (Radxa AICore, AXCL) or Hailo-10H (both have on-device DRAM).
                       Whisper STT, wake word. Degrades if no card. Independent of camera tier.

    Each tier is fully independent — no cross-tier fallback.
    """
    SAFETY_INFERENCE = 'safety_inference'
    CAMERA_INFERENCE = 'camera_inference'
    VOICE_INFERENCE  = 'voice_inference'


class TaskPriority(IntEnum):
    """Task priority levels."""
    LOW = 3
    NORMAL = 2
    HIGH = 1
    CRITICAL = 0


@dataclass
class BackendStats:
    """Backend statistics."""
    tasks_submitted: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_exec_time_ms: float = 0.0

    @property
    def average_latency_ms(self) -> float:
        if self.tasks_completed > 0:
            return self.total_exec_time_ms / self.tasks_completed
        return 0.0


@dataclass
class InferenceResult:
    """Inference operation result."""
    task_id: str = ""
    backend_type: BackendType = BackendType.NONE
    model_name: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    inference_time_ms: float = 0.0
    total_time_ms: float = 0.0
    success: bool = False
    error_message: str | None = None
    timed_out: bool = False


@dataclass
class ModelConfig:
    """Model configuration."""
    name: str = ""
    path: str = ""
    model_type: str = ""
    input_shapes: dict[str, Any] = field(default_factory=dict)
    output_shapes: dict[str, Any] = field(default_factory=dict)
    npu_cores: int = 0
    loaded: bool = False
    load_time_ms: float = 0.0


# ============================================================================
# Hardware Backend Base Class
# ============================================================================

class HardwareBackend(ABC):
    """Base class for all hardware backends."""

    def __init__(self, backend_type: BackendType):
        self.backend_type = backend_type
        self._initialized = False
        self._stats = BackendStats()

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the backend. Returns True on success."""

    @abstractmethod
    def release(self) -> None:
        """Release all resources."""

    def is_available(self) -> bool:
        """Check if backend is available and initialized."""
        return self._initialized

    @abstractmethod
    def infer(self, model_name: str, inputs: dict[str, Any]) -> InferenceResult:
        """Execute inference/computation."""

    def load_model(self, config: ModelConfig) -> bool:
        """Load a model (optional - for NPU/GPU backends)."""
        return True

    def unload_model(self, name: str) -> bool:
        """Unload a model."""
        return True

    def get_stats(self) -> BackendStats:
        """Get backend statistics."""
        return self._stats

    def get_device_info(self) -> dict[str, Any]:
        """Get device information."""
        return {
            'backend_type': self.backend_type.name,
            'available': self.is_available(),
        }

    def reset_stats(self) -> None:
        """Reset statistics."""
        self._stats = BackendStats()


# ============================================================================
# HAL Configuration
# ============================================================================

@dataclass
class HALConfig:
    """HAL configuration."""
    enable_npu: bool = True
    enable_acl: bool = True     # ARM Compute Library (GPU/CPU auto-select)
    enable_rga: bool = True
    enable_mpp: bool = True
    enable_hailo_8: bool = True  # Hailo-8 (26 TOPS) — gracefully skips if not present
    enable_dx_m1: bool = True   # DEEPX DX-M1 (20 TOPS) — gracefully skips if not present
    enable_onnx: bool = True    # ONNX Runtime fallback (dev PC / x86_64)
    num_workers: int = 4
    max_queue_size: int = 100
    inference_timeout_ms: float = 1000.0  # Maximum inference time before timeout (ADAS critical)
    models_to_preload: list[tuple[BackendType, ModelConfig]] = field(default_factory=list)  # Models to preload on init


# ============================================================================
# Main HAL Class
# ============================================================================

class HAL:
    """
    Hardware Abstraction Layer - unified access to all compute accelerators.
    Singleton pattern - only one instance exists.
    """

    _instance: HAL | None = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config: HALConfig | None = None):
        if self._initialized or hasattr(self, '_executor'):
            return

        self.config = config or HALConfig()
        self._backends: dict[BackendType, HardwareBackend] = {}
        self._initialized = False
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._models_cache: dict[str, ModelConfig] = {}  # Cache loaded models
        self._recovery_manager = ErrorRecoveryManager()  # Error recovery & fallbacks
        self._performance_monitor = PerformanceMonitor(window_size=100)  # Performance metrics
        self._health_checker = HealthChecker()  # Health check management
        self._alert_thresholds = AlertThresholds()  # Alert thresholds for degradation
        self._diagnostic_report = None  # Will be initialized after all components ready

    def initialize(self) -> bool:
        """Initialize all enabled backends and preload configured models."""
        if self._initialized:
            return True

        logger.info("Initializing Compute HAL...")

        if self.config.enable_npu:
            self._init_backend(BackendType.NPU, 'system.inferenced.rockchip_npu', 'RKNNBackend')

        if self.config.enable_acl:
            self._init_backend(BackendType.ACL, 'system.inferenced.arm_acl', 'ACLBackend')

        if self.config.enable_rga:
            self._init_backend(BackendType.RGA, 'system.inferenced.rockchip_rga', 'RGABackend')

        if self.config.enable_mpp:
            self._init_backend(BackendType.MPP, 'system.inferenced.rockchip_mpp', 'MPPBackend')

        if self.config.enable_hailo_8:
            self._init_backend(BackendType.HAILO_8, 'system.inferenced.hailo_hef', 'Hailo8Backend')

        if self.config.enable_dx_m1:
            self._init_backend(BackendType.DX_M1, 'system.inferenced.deepx_dxnn', 'DeepXBackend')

        if self.config.enable_onnx:
            self._init_backend(BackendType.ONNX, 'system.inferenced.onnx_backend', 'OnnxBackend')

        # Setup error recovery and fallback strategies
        self._setup_recovery()

        # Preload configured models
        self._preload_models()

        # Initialize diagnostic report with all monitoring components
        self._diagnostic_report = DiagnosticReport(
            self._performance_monitor,
            self._health_checker,
            self._alert_thresholds
        )

        # Register basic health checks for all backends
        self._register_health_checks()

        self._initialized = True
        logger.info(f"HAL initialized with {len(self._backends)} backends")
        return True

    def _init_backend(self, backend_type: BackendType, module_path: str, class_name: str):
        """Initialize a single backend."""
        try:
            import importlib
            module = importlib.import_module(f'openpilot.{module_path}')
            backend_class = getattr(module, class_name)
            backend = backend_class()

            if backend.initialize():
                self._backends[backend_type] = backend
                logger.info(f"{backend_type.name} backend initialized")
            else:
                logger.warning(f"{backend_type.name} backend initialization failed")

        except Exception as e:
            logger.warning(f"{backend_type.name} backend error: {e}")

    def _setup_recovery(self):
        """Setup error recovery strategies and health monitors."""
        # Register health monitors for all backends
        for backend_type, _backend in self._backends.items():
            monitor = BackendHealthMonitor(backend_type.name, max_failures=3)
            self._recovery_manager.register_backend_monitor(monitor)

        # ACL handles GPU/CPU selection internally; no separate fallback strategy needed

    def _register_health_checks(self):
        """Register health check functions with the health checker."""
        # Check if critical backends are available
        def check_npu_available() -> HealthCheckResult:
            is_available = self.is_backend_available(BackendType.NPU)
            return HealthCheckResult(
                is_healthy=is_available,
                message="NPU backend available" if is_available else "NPU backend not available"
            )

        def check_acl_available() -> HealthCheckResult:
            is_available = self.is_backend_available(BackendType.ACL)
            return HealthCheckResult(
                is_healthy=is_available,
                message="ACL backend available" if is_available else "ACL backend not available"
            )

        def check_backends_healthy() -> HealthCheckResult:
            unhealthy_backends = []
            for backend_type in self.get_available_backends():
                if not self.is_backend_healthy(backend_type):
                    unhealthy_backends.append(backend_type.name)

            if unhealthy_backends:
                return HealthCheckResult(
                    is_healthy=False,
                    message=f"Unhealthy backends: {', '.join(unhealthy_backends)}"
                )
            return HealthCheckResult(
                is_healthy=True,
                message="All backends healthy"
            )

        self._health_checker.register_check("npu_available", check_npu_available)
        self._health_checker.register_check("acl_available", check_acl_available)
        self._health_checker.register_check("backends_healthy", check_backends_healthy)

    def _preload_models(self):
        """Preload configured models on daemon startup."""
        if not self.config.models_to_preload:
            return

        logger.info(f"Preloading {len(self.config.models_to_preload)} models...")
        preloaded = 0

        for backend_type, model_config in self.config.models_to_preload:
            backend = self.get_backend(backend_type)
            if backend is None:
                logger.warning(f"Cannot preload {model_config.name}: {backend_type.name} not available")
                continue

            try:
                start_time = time.monotonic()
                if backend.load_model(model_config):
                    load_time_ms = (time.monotonic() - start_time) * 1000
                    model_config.loaded = True
                    model_config.load_time_ms = load_time_ms
                    self.cache_model(model_config)
                    logger.info(f"  ✓ Preloaded {model_config.name} ({load_time_ms:.1f}ms)")
                    preloaded += 1
                else:
                    logger.warning(f"  ✗ Failed to preload {model_config.name}")
            except Exception:
                logger.exception(f"  ✗ Error preloading {model_config.name}")

        logger.info(f"Preloaded {preloaded}/{len(self.config.models_to_preload)} models")

    def get_backend(self, backend_type: BackendType) -> HardwareBackend | None:
        """Get a specific backend."""
        return self._backends.get(backend_type)

    def get_available_backends(self) -> list[BackendType]:
        """Get list of available backends."""
        return list(self._backends.keys())

    def is_backend_available(self, backend_type: BackendType) -> bool:
        """Check if a backend is available."""
        return backend_type in self._backends

    def infer(self, backend_type: BackendType, model_name: str,
              inputs: dict, priority: TaskPriority = TaskPriority.NORMAL,
              timeout_ms: float | None = None) -> InferenceResult:
        """Run inference with error recovery, fallback support, and performance monitoring."""
        start_time = time.monotonic()
        backend = self.get_backend(backend_type)
        if backend is None:
            result = InferenceResult(
                success=False,
                error_message=f"Backend {backend_type.name} not available"
            )
            # Record failed operation
            self._performance_monitor.record_operation(
                operation_name=model_name,
                backend_name=backend_type.name,
                latency_ms=(time.monotonic() - start_time) * 1000,
                success=False
            )
            return result

        # Check backend health
        if not self._recovery_manager.is_backend_healthy(backend_type.name):
            error_info = ErrorInfo(
                category=ErrorCategory.BACKEND_CRASH,
                message=f"Backend {backend_type.name} is unhealthy (too many failures)",
                backend_name=backend_type.name,
                model_name=model_name,
                is_recoverable=False,
            )
            self._recovery_manager.record_error(error_info)
            result = InferenceResult(
                backend_type=backend_type,
                model_name=model_name,
                success=False,
                error_message=f"Backend {backend_type.name} unhealthy"
            )
            # Record failed operation
            self._performance_monitor.record_operation(
                operation_name=model_name,
                backend_name=backend_type.name,
                latency_ms=(time.monotonic() - start_time) * 1000,
                success=False
            )
            return result

        timeout_sec = (timeout_ms if timeout_ms is not None else self.config.inference_timeout_ms) / 1000.0
        try:
            future = self._executor.submit(backend.infer, model_name, inputs)
            result = future.result(timeout=timeout_sec)

            # Record operation metrics
            latency_ms = (time.monotonic() - start_time) * 1000
            self._performance_monitor.record_operation(
                operation_name=model_name,
                backend_name=backend_type.name,
                latency_ms=latency_ms,
                success=result.success
            )

            monitor = self._recovery_manager.backend_monitors.get(backend_type.name)
            if monitor is not None:
                if result.success:
                    monitor.record_success()
                else:
                    monitor.record_failure()

            return result

        except FutureTimeoutError:
            error_info = ErrorInfo(
                category=ErrorCategory.TIMEOUT,
                message=f"Inference timeout ({timeout_sec*1000:.0f}ms)",
                backend_name=backend_type.name,
                model_name=model_name,
                is_recoverable=True,
            )
            self._recovery_manager.record_error(error_info)
            monitor = self._recovery_manager.backend_monitors.get(backend_type.name)
            if monitor is not None:
                monitor.record_failure()

            # The hung thread still holds the single worker — replace the executor
            # so subsequent calls are not blocked waiting for the hung thread to finish.
            self._executor.shutdown(wait=False)
            self._executor = ThreadPoolExecutor(max_workers=1)

            # Record failed operation
            self._performance_monitor.record_operation(
                operation_name=model_name,
                backend_name=backend_type.name,
                latency_ms=(time.monotonic() - start_time) * 1000,
                success=False
            )

            return InferenceResult(
                backend_type=backend_type,
                model_name=model_name,
                success=False,
                timed_out=True,
                error_message=f"Inference timeout ({timeout_sec*1000:.0f}ms)"
            )

        except Exception as e:
            error_info = create_error_from_exception(e, backend_type.name, model_name)
            self._recovery_manager.record_error(error_info)
            monitor = self._recovery_manager.backend_monitors.get(backend_type.name)
            if monitor is not None:
                monitor.record_failure()

            # Record failed operation
            self._performance_monitor.record_operation(
                operation_name=model_name,
                backend_name=backend_type.name,
                latency_ms=(time.monotonic() - start_time) * 1000,
                success=False
            )

            return InferenceResult(
                backend_type=backend_type,
                model_name=model_name,
                success=False,
                error_message=str(e)
            )

    def get_error_summary(self) -> dict[str, Any]:
        """Get summary of recent errors."""
        return self._recovery_manager.get_error_summary()

    def get_backend_health_report(self) -> dict[str, Any]:
        """Get health status for all backends."""
        return self._recovery_manager.get_backend_health_report()

    def is_backend_healthy(self, backend_type: BackendType) -> bool:
        """Check if a backend is healthy."""
        return self._recovery_manager.is_backend_healthy(backend_type.name)

    def get_diagnostic_report(self) -> dict[str, Any]:
        """Get comprehensive diagnostic report."""
        if self._diagnostic_report is None:
            return {'error': 'Diagnostic report not initialized'}
        return self._diagnostic_report.generate_report()

    def print_diagnostic_report(self) -> None:
        """Print diagnostic report to logger."""
        if self._diagnostic_report is None:
            logger.warning("Diagnostic report not initialized")
            return
        self._diagnostic_report.print_report()

    def get_performance_metrics(self, operation_name: str, backend_name: str):
        """Get performance metrics for a specific operation."""
        return self._performance_monitor.get_metrics(operation_name, backend_name)

    def get_all_performance_metrics(self):
        """Get all collected performance metrics."""
        return self._performance_monitor.get_all_metrics()

    def cache_model(self, model_config: ModelConfig) -> None:
        """Cache a loaded model."""
        self._models_cache[model_config.name] = model_config

    def get_cached_model(self, model_name: str) -> ModelConfig | None:
        """Get a cached model config."""
        return self._models_cache.get(model_name)

    def is_model_cached(self, model_name: str) -> bool:
        """Check if a model is cached."""
        return model_name in self._models_cache

    def clear_model_cache(self) -> None:
        """Clear all cached models."""
        self._models_cache.clear()
        logger.info("Model cache cleared")

    def release(self):
        """Release all backends and cleanup executor."""
        for backend in self._backends.values():
            backend.release()
        self._backends.clear()
        self._models_cache.clear()
        self._executor.shutdown(wait=False)
        self._initialized = False
        HAL._instance = None


def get_hal() -> HAL:
    """Get or create HAL instance."""
    hal = HAL()
    if not hal._initialized:
        hal.initialize()
    return hal


# ============================================================================
# Backend Selector
# ============================================================================

class OperationType(Enum):
    """Types of compute operations."""
    NEURAL_NETWORK = "nn"
    SGM_STEREO = "sgm"
    GEMM = "gemm"
    CONV2D = "conv2d"
    IMAGE_RESIZE = "resize"
    IMAGE_CROP = "crop"
    VIDEO_ENCODE = "video_enc"
    VIDEO_DECODE = "video_dec"


@dataclass
class OperationConfig:
    """Configuration for an operation."""
    op_type: OperationType
    input_shape: tuple
    data_type: str = "float32"


class BackendSelector:
    """Intelligent backend selection based on operation characteristics."""

    GEMM_GPU_THRESHOLD = 256
    SGM_GPU_THRESHOLD = 640

    def __init__(self, hal: HAL):
        self.hal = hal

    def select_for_sgm(self, width: int, height: int) -> HardwareBackend | None:
        """Select backend for SGM stereo depth."""
        # SGM is ASSIGNED to ACL. Prefers GPU, falls back to CPU.
        return self.hal.get_backend(BackendType.ACL)

    def select_for_gemm(self, matrix_size: int) -> HardwareBackend | None:
        """Select backend for matrix multiply."""
        # GEMM is ASSIGNED to ACL. Prefers GPU, falls back to CPU.
        return self.hal.get_backend(BackendType.ACL)

    def select_for_resize(self) -> HardwareBackend | None:
        """Select backend for image resize."""
        # Always prefer RGA
        return self.hal.get_backend(BackendType.RGA)


# ============================================================================
# Convenience Functions
# ============================================================================

def select_for_sgm(width: int, height: int) -> HardwareBackend | None:
    """Select best backend for SGM stereo depth."""
    return BackendSelector(get_hal()).select_for_sgm(width, height)


def select_for_gemm(matrix_size: int) -> HardwareBackend | None:
    """Select best backend for matrix multiply."""
    return BackendSelector(get_hal()).select_for_gemm(matrix_size)


def select_for_resize() -> HardwareBackend | None:
    """Select best backend for image resize."""
    return BackendSelector(get_hal()).select_for_resize()


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    # Types
    'BackendType',
    'TaskPriority',
    'BackendStats',
    'InferenceResult',
    'ModelConfig',
    'OperationType',
    'OperationConfig',
    # Base
    'HardwareBackend',
    # HAL
    'HAL',
    'HALConfig',
    'get_hal',
    # Selector
    'BackendSelector',
    'select_for_sgm',
    'select_for_gemm',
    'select_for_resize',
]
