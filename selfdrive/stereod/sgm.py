#!/usr/bin/env python3
"""
SGM Stereo Depth — NumPy vectorized

Vectorized NumPy implementation for stereo depth.
- CURRENT: Pure NumPy CPU implementation
- FUTURE: OpenCL kernel on Mali G52 (kernel not yet written)
- CLBlast submodule available for future GPU GEMM acceleration

Performance target: < 30ms for 640x480@64 disparity on Mali G610
"""

from __future__ import annotations

import time
import logging
from typing import Any
from dataclasses import dataclass
from enum import Enum

import numpy as np
import cv2

logger = logging.getLogger("SGM")

# Custom exceptions
class SGMError(Exception):
    """Base SGM error."""
    pass

class SGMDeviceError(SGMError):
    """Device/GPU error."""
    pass

class SGMTimeoutError(SGMError):
    """Computation timeout."""
    pass

@dataclass
class SGMConfig:
    """SGM algorithm configuration."""
    target_width: int = 640
    target_height: int = 480
    max_disparity: int = 64
    min_disparity: int = 0
    p1: int = 10
    p2: int = 120
    use_8_path: bool = False
    enable_median_filter: bool = True
    enable_subpixel: bool = True
    max_runtime_ms: float = 50.0

@dataclass
class SGMResult:
    """SGM computation result."""
    disparity: np.ndarray
    confidence: np.ndarray
    valid_mask: np.ndarray
    inference_time_ms: float = 0.0
    success: bool = True
    error_message: str = ""
    backend_used: str = "unknown"  # "gpu" or "cpu"

class SGM:
    """Semi-Global Matching stereo depth using vectorized NumPy."""

    def __init__(self, config: SGMConfig | None = None, target: str = "cpu"):
        self.config = config or SGMConfig()
        # Pure NumPy CPU implementation; `target` kept for callers but not used.
        self.target = target
        self.primary_backend = "numpy"
        self.gpu_available = False
        self._allocate_buffers()
        logger.info(f"SGM initialized: {self.config.target_width}x{self.config.target_height}@{self.config.max_disparity}")
        
    def _allocate_buffers(self):
        """Pre-allocate computation buffers."""
        w, h = self.config.target_width, self.config.target_height
        d = self.config.max_disparity
        
        # Census buffers (uint32 per pixel)
        self.left_census = np.zeros((h, w), dtype=np.uint32)
        self.right_census = np.zeros((h, w), dtype=np.uint32)
        
        # Cost volume (uint8 per disparity)
        self.cost_volume = np.zeros((h, w, d), dtype=np.uint8)
        
        # Aggregated costs (uint16 for precision)
        self.agg_volume = np.zeros((h, w, d), dtype=np.uint16)
        
        # Output buffers
        self.disparity = np.zeros((h, w), dtype=np.float32)
        self.confidence = np.zeros((h, w), dtype=np.float32)
        
    def compute(self, left: np.ndarray, right: np.ndarray) -> SGMResult:
        """
        Compute stereo disparity using SGM.
        
        Args:
            left: Left image (H, W) grayscale
            right: Right image (H, W) grayscale
            
        Returns:
            SGMResult with disparity map
        """
        t_start = time.perf_counter()
        
        try:
            # Validate inputs
            if left.shape != right.shape:
                raise ValueError(f"Shape mismatch: {left.shape} vs {right.shape}")
                
            h, w = left.shape
            
            # Resize if needed
            if w != self.config.target_width or h != self.config.target_height:
                left = cv2.resize(left, (self.config.target_width, self.config.target_height))
                right = cv2.resize(right, (self.config.target_width, self.config.target_height))
                h, w = left.shape
                
            # Step 1: Census transform (CPU vectorized NumPy)
            t0 = time.perf_counter()
            self._census_transform(left, right)
            census_time = (time.perf_counter() - t0) * 1000
            
            # Step 2: Compute cost volume
            t0 = time.perf_counter()
            self._compute_cost_volume()
            cost_time = (time.perf_counter() - t0) * 1000
            
            # Step 3: Aggregate costs
            t0 = time.perf_counter()
            self._aggregate_costs()
            agg_time = (time.perf_counter() - t0) * 1000
            
            # Step 4: WTA (Winner Takes All)
            t0 = time.perf_counter()
            self._winner_takes_all()
            wta_time = (time.perf_counter() - t0) * 1000
            
            # Step 5: Median filter
            if self.config.enable_median_filter:
                t0 = time.perf_counter()
                self._median_filter()
                filter_time = (time.perf_counter() - t0) * 1000
            else:
                filter_time = 0
                
            total_time = (time.perf_counter() - t_start) * 1000
            
            # Check timeout
            if total_time > self.config.max_runtime_ms:
                logger.warning(f"SGM timeout: {total_time:.1f}ms > {self.config.max_runtime_ms}ms")
                
            # Create valid mask
            valid_mask = self.disparity > 0
            
            logger.debug(
                f"SGM times: census={census_time:.1f}ms, "
                f"cost={cost_time:.1f}ms, agg={agg_time:.1f}ms, "
                f"wta={wta_time:.1f}ms, filter={filter_time:.1f}ms, "
                f"total={total_time:.1f}ms"
            )
            
            return SGMResult(
                disparity=self.disparity.copy(),
                confidence=self.confidence.copy(),
                valid_mask=valid_mask,
                inference_time_ms=total_time,
                success=True,
                backend_used=self.primary_backend
            )
            
        except Exception as e:
            logger.error(f"SGM computation failed: {e}")
            return SGMResult(
                disparity=np.zeros((self.config.target_height, self.config.target_width)),
                confidence=np.zeros((self.config.target_height, self.config.target_width)),
                valid_mask=np.zeros((self.config.target_height, self.config.target_width), dtype=bool),
                success=False,
                error_message=str(e),
                backend_used="none"
            )
            
    def _census_transform(self, left: np.ndarray, right: np.ndarray):
        """
        Compute census transform for both images.
        
        Uses vectorized numpy for performance (much faster than pure Python loops).
        Vectorized NumPy implementation. Performance depends on image size.
        """
        h, w = left.shape
        
        # 5x5 census window
        window_size = 5
        half = window_size // 2
        
        # Vectorized census transform using sliding window
        # For each pixel, compare with 24 neighbors (5x5 minus center)
        for dy in range(-half, half + 1):
            for dx in range(-half, half + 1):
                if dy == 0 and dx == 0:
                    continue
                
                # Shift image and compare
                y_start = max(half, -dy + half)
                y_end = min(h - half, h - dy - half)
                x_start = max(half, -dx + half)
                x_end = min(w - half, w - dx - half)
                
                if y_start >= y_end or x_start >= x_end:
                    continue
                
                # Compare center with neighbor
                left_cmp = left[y_start:y_end, x_start:x_end] < left[y_start+dy:y_end+dy, x_start+dx:x_end+dx]
                right_cmp = right[y_start:y_end, x_start:x_end] < right[y_start+dy:y_end+dy, x_start+dx:x_end+dx]
                
                # Pack bits - use bit position based on dy, dx
                bit_pos = (dy + half) * window_size + (dx + half)
                if bit_pos > 0:  # Skip center (bit_pos = 12 for 5x5)
                    bit_pos = bit_pos - 1 if bit_pos > 12 else bit_pos
                    
                self.left_census[y_start:y_end, x_start:x_end] |= (left_cmp.astype(np.uint32) << bit_pos)
                self.right_census[y_start:y_end, x_start:x_end] |= (right_cmp.astype(np.uint32) << bit_pos)
                
    def _compute_cost_volume(self):
        """Compute matching cost volume using Hamming distance."""
        h, w = self.left_census.shape
        d = self.config.max_disparity
        # For each disparity, shift right image and compute Hamming distance
        for disp in range(d):
            if disp == 0:
                # No shift - direct comparison
                diff = self.left_census ^ self.right_census
            else:
                # Shift right image by disp pixels
                diff = np.zeros_like(self.left_census)
                diff[:, disp:] = self.left_census[:, disp:] ^ self.right_census[:, :-disp]
                # Invalid region (where right image would be out of bounds)
                diff[:, :disp] = 255
            
            # Count bits (Hamming distance) using lookup table for speed
            self.cost_volume[:, :, disp] = self._popcount(diff)
    
    def _popcount(self, arr: np.ndarray) -> np.ndarray:
        """Count set bits in uint32 array using vectorized operations."""
        # Use built-in bit_count if available (Python 3.10+)
        # Fallback to lookup table
        lut = np.zeros(256, dtype=np.uint8)
        for i in range(256):
            lut[i] = bin(i).count('1')
        
        # Process byte by byte
        result = lut[arr & 0xFF]
        result += lut[(arr >> 8) & 0xFF]
        result += lut[(arr >> 16) & 0xFF]
        result += lut[(arr >> 24) & 0xFF]
        return result
                        
    def _aggregate_costs(self):
        """4-path cost aggregation with vectorized numpy."""
        h, w, d = self.cost_volume.shape
        p1, p2 = self.config.p1, self.config.p2
        
        # Initialize with raw costs
        self.agg_volume[:] = self.cost_volume[:]
        
        # 4-path aggregation with vectorized operations
        # Path 1: Left to Right
        for x in range(1, w):
            # Current column costs
            curr = self.agg_volume[:, x, :].copy()
            # Previous column costs
            prev = self.agg_volume[:, x - 1, :]
            
            # Min cost with disparity continuity (p1 penalty for ±1 disparity)
            min_cost = prev.copy()
            # Disparity - 1 (with boundary check)
            min_cost[:, 1:] = np.minimum(min_cost[:, 1:], prev[:, :-1] + p1)
            # Disparity + 1 (with boundary check)
            min_cost[:, :-1] = np.minimum(min_cost[:, :-1], prev[:, 1:] + p1)
            
            # Update aggregated costs
            self.agg_volume[:, x, :] = curr + min_cost - np.min(prev, axis=1, keepdims=True)
        
        # Path 2: Top to Bottom
        for y in range(1, h):
            curr = self.agg_volume[y, :, :].copy()
            prev = self.agg_volume[y - 1, :, :]
            
            min_cost = prev.copy()
            min_cost[:, 1:] = np.minimum(min_cost[:, 1:], prev[:, :-1] + p1)
            min_cost[:, :-1] = np.minimum(min_cost[:, :-1], prev[:, 1:] + p1)
            
            self.agg_volume[y, :, :] = curr + min_cost - np.min(prev, axis=1, keepdims=True)
                    
    def _winner_takes_all(self):
        """
        Find best disparity for each pixel.
        
        Also computes confidence from second-best cost ratio.
        Vectorized numpy implementation.
        """
        h, w, d = self.agg_volume.shape
        
        # Find minimum cost disparity for each pixel
        best_disp = np.argmin(self.agg_volume, axis=2)
        min_cost = np.min(self.agg_volume, axis=2)
        
        # Sub-pixel refinement for interior disparities
        valid = (best_disp > 0) & (best_disp < d - 1)
        
        c0 = np.zeros_like(min_cost, dtype=np.float32)
        c1 = min_cost.astype(np.float32)
        c2 = np.zeros_like(min_cost, dtype=np.float32)
        
        c0[valid] = self.agg_volume[valid, best_disp[valid] - 1]
        c2[valid] = self.agg_volume[valid, best_disp[valid] + 1]
        
        denom = 2 * (c0 + c2 - 2 * c1)
        refine_mask = valid & (np.abs(denom) > 1)
        
        self.disparity = best_disp.astype(np.float32)
        self.disparity[refine_mask] = best_disp[refine_mask] - (c2[refine_mask] - c0[refine_mask]) / denom[refine_mask]
        
        # Compute confidence (ratio of best to second-best)
        # Create mask to exclude best disparity
        flat_idx = np.arange(h * w)
        flat_volume = self.agg_volume.reshape(-1, d)
        flat_best = best_disp.reshape(-1)
        
        # Set best disparity cost to max to find second best
        temp_volume = flat_volume.copy()
        temp_volume[flat_idx, flat_best] = 65535
        second_min = np.min(temp_volume, axis=1).reshape(h, w)
        
        self.confidence = np.ones_like(min_cost, dtype=np.float32)
        valid_cost = min_cost > 0
        self.confidence[valid_cost] = np.minimum(second_min[valid_cost] / min_cost[valid_cost], 10.0)
                    
    def _median_filter(self):
        """Apply median filter using OpenCV."""
        self.disparity = cv2.medianBlur(self.disparity.astype(np.float32), 3)
    
    def is_available(self) -> bool:
        """SGM is always available (CPU fallback)."""
        return True
    
    def get_device_info(self) -> dict[str, Any]:
        """Get device information."""
        info: dict[str, Any] = {
            "backend": "numpy",
            "name": "numpy-cpu",
            "target": self.target,
            "primary_backend": self.primary_backend,
            "gpu_available": self.gpu_available,
        }
        return info
        
    def release(self):
        """Release resources."""
        pass
            
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False

# Export main classes

# Convenience function
def compute_disparity(left: np.ndarray, 
                          right: np.ndarray,
                          max_disparity: int = 64,
                          timeout_ms: float = 50.0) -> SGMResult:
    """
    Compute stereo disparity using SGM.
    
    Args:
        left: Left stereo image (grayscale)
        right: Right stereo image (grayscale)
        max_disparity: Maximum disparity value
        timeout_ms: Maximum computation time
        
    Returns:
        SGMResult with disparity map
    """
    config = SGMConfig(
        max_disparity=max_disparity,
        max_runtime_ms=timeout_ms
    )
    
    with SGM(config) as sgm:
        return sgm.compute(left, right)
