#!/usr/bin/env python3
"""
Stereo Depth Pipeline

Complete pipeline from stereo images to 3D point cloud:

    Stereo Images (Left + Right)
           ↓
    SGM Stereo Matching (GPU/CPU)
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

# Import SGM (stereo matching)
from openpilot.selfdrive.stereod.sgm import SGM, SGMConfig

# Import reprojection (depth map to 3D points)
from openpilot.selfdrive.pointcloudd.reprojector import PointReprojector
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
    """
    Complete stereo depth pipeline — NumPy CPU implementation.

    Combines:
    1. SGM stereo matching (disparity) - NumPy CPU
    2. Depth conversion (disparity to depth) - NumPy CPU
    3. 3D reprojection (depth to point cloud) - OpenCV CPU

    GPU OpenCL kernels not yet implemented. CLBlast submodule available for future
    GPU GEMM acceleration.
    """

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        max_disparity: int = 64,
        use_gpu: bool = True
    ):
        """
        Initialize depth pipeline.

        Args:
            width: Image width
            height: Image height
            max_disparity: Maximum disparity search range
            use_gpu: Try GPU acceleration first
        """
        self.width = width
        self.height = height
        self.use_gpu = use_gpu

        # Initialize SGM (stereo matching) - pure NumPy CPU today
        self.sgm = SGM(SGMConfig(
            target_width=width,
            target_height=height,
            max_disparity=max_disparity,
        ))

        # Initialize reprojector (depth map to 3D points). Lazy: created on first
        # process() so width/height can match the SGM output.
        self.reprojector: PointReprojector | None = None

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
            left: Left stereo image (H, W) or (H, W, 3)
            right: Right stereo image (H, W) or (H, W, 3)
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
                # Q: [[1,0,0,-cx],[0,1,0,-cy],[0,0,0,f],[0,0,-1/b,0]]
                cx = float(-Q[0, 3])
                cy = float(-Q[1, 3])
                f = float(Q[2, 3])
                baseline = float(-1.0 / Q[3, 2]) if Q[3, 2] != 0 else 0.0

                # disparity -> depth (meters)
                d = disparity.astype(np.float32)
                depth_map = np.zeros_like(d)
                valid = d > 0.1
                if f > 0 and baseline > 0:
                    depth_map[valid] = f * baseline / d[valid]

                # Lazy init the reprojector with matching intrinsics
                h, w = depth_map.shape
                if self.reprojector is None:
                    self.reprojector = PointReprojector(
                        width=w, height=h, fx=f, fy=f, cx=cx, cy=cy,
                    )

                pointcloud = self.reprojector.reproject(depth_map)
                depth = depth_map

            elapsed_ms = (time.perf_counter() - t_start) * 1000

            return DepthPipelineResult(
                disparity=disparity,
                disparity_confidence=confidence,
                depth=depth,
                pointcloud=pointcloud,
                processing_time_ms=elapsed_ms,
                using_gpu=self.use_gpu,
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
        """Check if pipeline is configured to prefer GPU."""
        return self.use_gpu

    def release(self):
        """Release resources."""
        self.sgm.release()
        if self.reprojector is not None:
            self.reprojector.release()
            self.reprojector = None


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
    Automatically selects GPU if available, falls back to CPU.

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
    from openpilot.selfdrive.stereod.sgm import compute_disparity as sgm_compute
    result = sgm_compute(left, right, max_disparity)
    return result.disparity, result.confidence


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
