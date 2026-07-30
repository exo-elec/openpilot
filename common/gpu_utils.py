#!/usr/bin/env python3
"""
Common GPU utilities for hardware acceleration.

Provides standardized GPU detection and initialization across all daemons.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Try to import OpenCL
try:
    import pyopencl as cl
    HAS_OPENCL = True
except ImportError:
    HAS_OPENCL = False
    logger.debug("pyopencl not available")


@dataclass
class GPUInfo:
    """GPU device information."""
    name: str
    platform: str
    device_type: str
    compute_units: int
    global_memory_mb: int
    is_mali: bool
    device_id: int | None = None


def detect_mali_gpu() -> GPUInfo | None:
    """
    Detect Mali GPU and return device info.
    
    Returns:
        GPUInfo if Mali GPU found, None otherwise
    """
    if not HAS_OPENCL:
        return None
    
    try:
        platforms = cl.get_platforms()
        
        for platform in platforms:
            platform_name = platform.name
            
            # Get GPU devices
            try:
                devices = platform.get_devices(cl.device_type.GPU)
            except cl.RuntimeError:
                continue
            
            for device in devices:
                device_name = device.name
                device_name_lower = device_name.lower()
                
                # Check if Mali GPU
                is_mali = 'mali' in device_name_lower or 'arm' in platform_name.lower()
                
                if is_mali:
                    # Get device info
                    try:
                        compute_units = device.max_compute_units
                        global_mem = device.global_mem_size // (1024 * 1024)  # MB
                        
                        return GPUInfo(
                            name=device_name,
                            platform=platform_name,
                            device_type='GPU',
                            compute_units=compute_units,
                            global_memory_mb=global_mem,
                            is_mali=True,
                            device_id=device.int_ptr
                        )
                    except Exception as e:
                        logger.debug(f"Error getting device info: {e}")
                        # Return basic info
                        return GPUInfo(
                            name=device_name,
                            platform=platform_name,
                            device_type='GPU',
                            compute_units=0,
                            global_memory_mb=0,
                            is_mali=True
                        )
        
        return None
        
    except Exception as e:
        logger.warning(f"GPU detection failed: {e}")
        return None


def get_mali_gpu_model() -> str:
    """
    Get Mali GPU model name.
    
    Returns:
        GPU model string (e.g., 'Mali-G52', 'Mali-G610', 'Unknown')
    """
    gpu_info = detect_mali_gpu()
    if gpu_info and gpu_info.is_mali:
        return gpu_info.name
    return 'Unknown'


def is_mali_g610() -> bool:
    """Check if running on Mali-G610 (RK3588)."""
    gpu = get_mali_gpu_model()
    return 'g610' in gpu.lower()


def get_recommended_work_group_size() -> tuple[int, int]:
    """
    Get recommended OpenCL work group size for current GPU.

    Returns:
        (local_x, local_y) tuple
    """
    return (16, 16)


def create_opencl_context() -> tuple[cl.Context, cl.CommandQueue, cl.Device | None]:
    """
    Create OpenCL context for Mali GPU.
    
    Returns:
        Tuple of (context, queue, device) or None if failed
    """
    if not HAS_OPENCL:
        return None
    
    try:
        platforms = cl.get_platforms()
        
        for platform in platforms:
            try:
                devices = platform.get_devices(cl.device_type.GPU)
            except cl.RuntimeError:
                continue
            
            for device in devices:
                if 'mali' in device.name.lower() or 'arm' in platform.name.lower():
                    ctx = cl.Context([device])
                    queue = cl.CommandQueue(ctx)
                    logger.info(f"OpenCL context created for {device.name}")
                    return ctx, queue, device
        
        return None
        
    except Exception as e:
        logger.warning(f"Failed to create OpenCL context: {e}")
        return None


def log_gpu_info():
    """Log GPU information for debugging."""
    gpu_info = detect_mali_gpu()
    
    if gpu_info:
        logger.info("=" * 50)
        logger.info("GPU Information:")
        logger.info(f"  Name: {gpu_info.name}")
        logger.info(f"  Platform: {gpu_info.platform}")
        logger.info(f"  Type: {gpu_info.device_type}")
        logger.info(f"  Compute Units: {gpu_info.compute_units}")
        logger.info(f"  Global Memory: {gpu_info.global_memory_mb} MB")
        logger.info(f"  Is Mali: {gpu_info.is_mali}")
        logger.info("=" * 50)
    else:
        logger.warning("No Mali GPU detected")


def test_gpu_utils():
    """Test GPU utilities."""
    print("Testing GPU Utilities...")
    
    # Test detection
    gpu_info = detect_mali_gpu()
    if gpu_info:
        print(f"✓ GPU detected: {gpu_info.name}")
        print(f"  Platform: {gpu_info.platform}")
        print(f"  Compute Units: {gpu_info.compute_units}")
        print(f"  Is G610: {is_mali_g610()}")
    else:
        print("✗ No GPU detected")
    
    # Test context creation
    ctx_result = create_opencl_context()
    if ctx_result:
        ctx, queue, device = ctx_result
        print(f"✓ OpenCL context created for {device.name}")
    else:
        print("✗ Failed to create OpenCL context")
    
    print("\nTest complete!")


if __name__ == "__main__":
    test_gpu_utils()
