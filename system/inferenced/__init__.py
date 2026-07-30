"""
InferenceD - Centralized Hardware Abstraction Layer

All daemons MUST use this module to access compute hardware.
No direct hardware access allowed.

Official 3rd party submodules:
- NPU: rockchip_rknpu2
- GPU/CPU: arm_compute (ACL)
- RGA: rockchip_rga
- MPP: rockchip_mpp

Usage:
    from openpilot.system.inferenced import InferenceClient
    
    client = InferenceClient("modeld")
    
    # Run inference
    result = client.npu().infer('vision_model', {'input': data})
    
    # Or get specific backend
    gpu = client.acl()
    result = gpu.infer('sgm', {'left': left_img, 'right': right_img})
"""

# Core HAL
from openpilot.system.inferenced.compute import (
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
    BackendSelector,
    select_for_sgm,
    select_for_gemm,
    select_for_resize,
)

# Client API (preferred for daemons)
from openpilot.system.inferenced.client import (
    InferenceClient,
    ClientConfig,
)

# Backend implementations
from openpilot.system.inferenced.rockchip_npu import RKNNBackend
from openpilot.system.inferenced.rockchip_rga import RGABackend
from openpilot.system.inferenced.rockchip_mpp import MPPBackend
from openpilot.system.inferenced.arm_acl import ACLBackend
from openpilot.system.inferenced.hailo_hef import Hailo8Backend
from openpilot.system.inferenced.onnx_backend import OnnxBackend

__all__ = [
    # Client API (use this)
    'InferenceClient',
    'ClientConfig',

    # Core HAL
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

    # Backend selector
    'BackendSelector',
    'select_for_sgm',
    'select_for_gemm',
    'select_for_resize',

    # Backend implementations
    'RKNNBackend',
    'RGABackend',
    'MPPBackend',
    'ACLBackend',
    'Hailo8Backend',
    'OnnxBackend',
]
