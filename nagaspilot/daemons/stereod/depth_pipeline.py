#!/usr/bin/env python3
"""
Stereo Depth Pipeline

Complete pipeline from stereo images to 3D point cloud:

    Stereo Images (Left + Right)
           ↓
    SGM Stereo Matching (GPU; CPU only by explicit offline request)
           ↓
    Disparity Map (pixels)
           ↓
    Depth Map (meters)
           ↓
    3D Point Cloud (X, Y, Z)

Terminology:
- Disparity: Pixel offset between left and right images (inverse of depth)
- Depth: Distance from camera in meters (Z)
- Point Cloud: 3D coordinates (X, Y, Z) for each pixel

Usage:
    pipeline = DepthPipeline(width=1920, height=1080)
    result = pipeline.process(left_image, right_image, Q_matrix)

    # Access outputs
    disparity = result.disparity  # Pixel offsets
    depth = result.depth          # Meters (Z)
    points_3d = result.pointcloud # 3D coordinates (X, Y, Z)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import cv2

# Import SGM (stereo matching)
from openpilot.nagaspilot.daemons.stereod.sgm import SGM, SGMConfig

from openpilot.common.swaglog import cloudlog


@dataclass
class DepthPipelineResult:
    """Complete depth pipeline result."""
    # Raw outputs
    disparity: np.ndarray           # Disparity map (pixels)
    disparity_confidence: np.ndarray  # Confidence per pixel

    # Derived outputs
    depth: np.ndarray | None = None       # Depth map (meters)
    pointcloud: np.ndarray | None = None  # 3D points (X, Y, Z)

    # Metadata
    processing_time_ms: float = 0.0
    using_gpu: bool = False
    success: bool = True
    error_message: str = ""


class DepthPipeline:
    """GPU disparity with an optional CPU numerical Q projection reference.

    GPU matching is mandatory unless use_gpu=False explicitly requests the
    offline NumPy matcher. This helper is not the live geometric grid pipeline.
    """

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        max_disparity: int = 64,
        use_gpu: bool = True,
        *, gpu=None
    ):
        """
        Initialize depth pipeline.

        Args:
            width: Image width
            height: Image height
            max_disparity: Maximum disparity search range
            use_gpu: Require GPU matching; False explicitly selects the offline CPU reference
            gpu: Optional HAL-owned GPU context; otherwise this helper owns a client
        """
        self.width = width
        self.height = height
        self.use_gpu = use_gpu

        self._client = None
        try:
            if use_gpu and gpu is None:
                from openpilot.system.inferenced import InferenceClient
                self._client = InferenceClient("stereo_depth_helper")
                gpu = self._client.gpu()
            self.sgm = SGM(SGMConfig(target_width=width, target_height=height, max_disparity=max_disparity),
                           target="gpu" if use_gpu else "cpu", gpu=gpu)
        except Exception:
            if self._client is not None:
                self._client.release()
            raise

        cloudlog.info(f"DepthPipeline initialized ({width}x{height}, prefer_gpu={use_gpu})")

    def process(
        self,
        left: np.ndarray,
        right: np.ndarray,
        Q: np.ndarray | None = None
    ) -> DepthPipelineResult:
        """
        Process stereo images through complete pipeline.

        Args:
            left: Rectified uint8 grayscale left image (H, W)
            right: Rectified uint8 grayscale right image (H, W)
            Q: Reprojection matrix (4, 4). If None, only disparity is computed.

        Returns:
            DepthPipelineResult with disparity, depth, and point cloud
        """
        import time
        t_start = time.perf_counter()

        try:
            # Step 1: SGM Stereo Matching → Disparity
            sgm_result = self.sgm.compute(left, right)

            if not sgm_result.success:
                return DepthPipelineResult(
                    disparity=sgm_result.disparity,
                    disparity_confidence=sgm_result.confidence,
                    success=False,
                    error_message=sgm_result.error_message
                )

            disparity = sgm_result.disparity
            confidence = sgm_result.confidence

            # Step 2 & 3: Reprojection → Depth + Point Cloud
            depth = None
            pointcloud = None

            if Q is not None:
                q = np.asarray(Q, dtype=np.float64)
                if q.shape != (4, 4) or not np.isfinite(q).all() or np.linalg.matrix_rank(q) != 4:
                    raise ValueError("Expected a finite, full-rank 4x4 Q at the disparity resolution")
                # Complete homogeneous Q, including principal-point offsets.
                # This optional reference projection remains on CPU.
                xyz = cv2.reprojectImageTo3D(disparity.astype(np.float32), q)
                valid = (np.isfinite(disparity) & (disparity > 0) & np.isfinite(confidence) &
                         (confidence > 0) & np.isfinite(xyz).all(axis=2) & (xyz[:, :, 2] > 0))
                depth = np.where(valid, xyz[:, :, 2], 0).astype(np.float32)
                pointcloud = xyz[valid].astype(np.float32)

            elapsed_ms = (time.perf_counter() - t_start) * 1000

            return DepthPipelineResult(
                disparity=disparity,
                disparity_confidence=confidence,
                depth=depth,
                pointcloud=pointcloud,
                processing_time_ms=elapsed_ms,
                using_gpu=sgm_result.backend_used == "opencl",
                success=True
            )

        except Exception as e:
            cloudlog.error(f"Depth pipeline failed: {e}")
            return DepthPipelineResult(
                disparity=np.zeros((self.height, self.width)),
                disparity_confidence=np.zeros((self.height, self.width)),
                success=False,
                error_message=str(e)
            )

    def is_using_gpu(self) -> bool:
        """Report the constructed matching backend, not the requested preference."""
        return self.sgm.gpu_available

    def release(self):
        """Release resources."""
        try:
            self.sgm.release()
        finally:
            if self._client is not None:
                self._client.release()
                self._client = None


# Convenience function
def compute_depth(
    left: np.ndarray,
    right: np.ndarray,
    Q: np.ndarray,
    max_disparity: int = 64
) -> DepthPipelineResult:
    """
    Compute complete depth pipeline (stereo → disparity → depth → point cloud).

    This is the recommended function for most use cases.
    Requires a GPU; never falls back to CPU matching.

    Args:
        left: Left stereo image
        right: Right stereo image
        Q: Reprojection matrix (from stereo calibration)
        max_disparity: Maximum disparity search range

    Returns:
        DepthPipelineResult with all outputs
    """
    h, w = left.shape[:2]
    pipeline = DepthPipeline(width=w, height=h, max_disparity=max_disparity)

    try:
        return pipeline.process(left, right, Q)
    finally:
        pipeline.release()


# Simple disparity-only function (faster, no 3D)
def compute_disparity(
    left: np.ndarray,
    right: np.ndarray,
    max_disparity: int = 64
) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute stereo disparity only (no depth/point cloud).

    Faster than full pipeline when only 2D disparity is needed.

    Args:
        left: Left stereo image
        right: Right stereo image
        max_disparity: Maximum disparity search range

    Returns:
        (disparity_map, confidence_map)
    """
    h, w = left.shape[:2]
    pipeline = DepthPipeline(width=w, height=h, max_disparity=max_disparity)
    try:
        result = pipeline.process(left, right)
        if not result.success:
            raise RuntimeError(result.error_message)
        return result.disparity, result.disparity_confidence
    finally:
        pipeline.release()


def test_pipeline():
    """Test depth pipeline."""
    import time

    print("Testing Depth Pipeline...")

    # Create test stereo pair
    h, w = 480, 640
    left = np.random.randint(0, 255, (h, w), dtype=np.uint8)
    right = np.roll(left, 10, axis=1)  # Simple shift

    # Q matrix (typical stereo)
    Q = np.array([
        [1, 0, 0, -w/2],
        [0, 1, 0, -h/2],
        [0, 0, 0, 800],
        [0, 0, -1/0.12, 0]
    ])

    # Test full pipeline
    print("\n1. Full pipeline (disparity → depth → point cloud)")
    pipeline = DepthPipeline(width=w, height=h)

    print(f"   Using GPU: {pipeline.is_using_gpu()}")

    # Warmup
    for _ in range(3):
        _ = pipeline.process(left, right, Q)

    # Benchmark
    times = []
    for _ in range(10):
        t0 = time.perf_counter()
        result = pipeline.process(left, right, Q)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)

    avg_time = np.mean(times)
    print(f"   Average: {avg_time:.2f}ms ({1000/avg_time:.1f} fps)")
    print("   Output shapes:")
    print(f"     Disparity: {result.disparity.shape}")
    print(f"     Depth: {result.depth.shape if result.depth is not None else 'None'}")
    print(f"     Pointcloud: {result.pointcloud.shape if result.pointcloud is not None else 'None'}")

    pipeline.release()

    # Test disparity-only
    print("\n2. Disparity only (faster)")
    t0 = time.perf_counter()
    disp, conf = compute_disparity(left, right)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"   Time: {elapsed:.2f}ms")
    print(f"   Output: {disp.shape}")

    print("\nTest complete!")


if __name__ == "__main__":
    test_pipeline()
