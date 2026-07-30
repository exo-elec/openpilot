#!/usr/bin/env python3
"""
Inference client for selfdrive daemons.

DEPRECATED: This module re-exports from system.inferenced for backward compatibility.
New code should import directly from openpilot.system.inferenced.
"""

# Re-export everything from centralized HAL
from openpilot.system.inferenced import (
    # Client API
    InferenceClient,
    ClientConfig,
    
    # Core HAL
    BackendType,
    TaskPriority,
    BackendStats,
    InferenceResult,
    ModelConfig,
    OperationType,
    OperationConfig,
    HardwareBackend,
    HAL,
    HALConfig,
    get_hal,
    
    # Backend selector
    BackendSelector,
    select_for_sgm,
    select_for_gemm,
    select_for_resize,
)

# Deprecated aliases for old imports
class TaskPriority:
    """Deprecated - use from openpilot.system.inferenced."""
    LOW = TaskPriority.LOW
    NORMAL = TaskPriority.NORMAL
    HIGH = TaskPriority.HIGH
    CRITICAL = TaskPriority.CRITICAL


class InferenceScheduler:
    """Deprecated - use InferenceClient from openpilot.system.inferenced."""
    
    def __init__(self):
        raise DeprecationWarning(
            "InferenceScheduler is deprecated. "
            "Use InferenceClient from openpilot.system.inferenced"
        )


__all__ = [
    'InferenceClient',
    'ClientConfig',
    'BackendType',
    'TaskPriority',
    'BackendStats',
    'InferenceResult',
    'ModelConfig',
    'OperationType',
    'OperationConfig',
    'HardwareBackend',
    'HAL',
    'HALConfig',
    'get_hal',
    'BackendSelector',
    'select_for_sgm',
    'select_for_gemm',
    'select_for_resize',
]
