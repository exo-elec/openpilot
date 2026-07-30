"""
Multi-source road corridor boundary fusion for pathd.

Fuses road boundaries from:
  * groundObjects.roadGeometry (PP-LiteSeg segmentation + stereo depth)
  * modelV2.roadEdges (vision model predictions)

Uses confidence-weighted priority selection to provide robust corridor
boundaries for trajectory planning.
"""

from __future__ import annotations

import numpy as np
from cereal import log
from openpilot.common.swaglog import cloudlog

# Import standard distance sampling from modeld constants
try:
    from openpilot.selfdrive.modeld.constants import ModelConstants
    X_IDXS = np.array(ModelConstants.X_IDXS, dtype=np.float32)  # 33 points: 0-192m
except ImportError:
    # Fallback: quadratic spacing 0-192m
    def index_function(idx, max_val=192.0, max_idx=32):
        return max_val * ((idx / max_idx) ** 2)
    X_IDXS = np.array([index_function(i) for i in range(33)], dtype=np.float32)

# Confidence thresholds for source selection
SEGMENTATION_HIGH_CONFIDENCE = 0.7  # Use segmentation if confidence >= 0.7
VISION_DEFAULT_CONFIDENCE = 0.8     # Assumed confidence for vision model
CONFIDENCE_EPSILON = 1e-4           # Tolerance for float32 precision

# Default corridor (used when no sources available)
DEFAULT_CORRIDOR_WIDTH = 3.0  # meters (±3m from center)


def fuse_corridor_boundaries(
    model_v2: log.ModelDataV2 | None,
    ground_objects: log.GroundObjects | None,
) -> tuple[np.ndarray, np.ndarray, log.EnhancedTrajectory.BoundarySource, float]:
    """
    Fuse road boundaries from multiple sources using confidence-weighted priority.

    Priority (highest to lowest):
      1. groundObjects.roadGeometry (segmentation + depth) - if confidence > 0.7
      2. modelV2.roadEdges (vision model) - always available, reliable
      3. Default corridor (±3m) - last resort

    Args:
        model_v2: Vision model output with roadEdges
        ground_objects: Ground-level detections with roadGeometry

    Returns:
        left_boundary: 33 floats, lateral offset (m, positive = left)
        right_boundary: 33 floats, lateral offset (m, negative = right)
        source: Enum indicating which source was used
        confidence: Combined confidence score [0.0, 1.0]
    """
    # Source 1: groundObjects.roadGeometry (segmentation + depth fusion)
    if ground_objects is not None and hasattr(ground_objects, 'roadGeometry'):
        road_geom = ground_objects.roadGeometry
        if (hasattr(road_geom, 'roadEdges') and
            len(road_geom.roadEdges) >= 2 and
            road_geom.roadEdgeConfidence >= (SEGMENTATION_HIGH_CONFIDENCE - CONFIDENCE_EPSILON)):

            try:
                left_edge = road_geom.roadEdges[0]
                right_edge = road_geom.roadEdges[1]

                if len(left_edge.y) == 33 and len(right_edge.y) == 33:
                    left_boundary = np.array(left_edge.y, dtype=np.float32)
                    right_boundary = np.array(right_edge.y, dtype=np.float32)
                    confidence = float(road_geom.roadEdgeConfidence)

                    cloudlog.debug(f"Using segmentation boundaries (conf={confidence:.2f})")
                    return (
                        left_boundary,
                        right_boundary,
                        log.EnhancedTrajectory.BoundarySource.segmentation,
                        confidence
                    )
            except Exception as e:
                cloudlog.warning(f"Failed to extract segmentation boundaries: {e}")

    # Source 2: modelV2.roadEdges (vision model - primary fallback)
    if model_v2 is not None and hasattr(model_v2, 'roadEdges') and len(model_v2.roadEdges) >= 2:
        try:
            left_edge = model_v2.roadEdges[0]
            right_edge = model_v2.roadEdges[1]

            if len(left_edge.y) == 33 and len(right_edge.y) == 33:
                left_boundary = np.array(left_edge.y, dtype=np.float32)
                right_boundary = np.array(right_edge.y, dtype=np.float32)

                cloudlog.debug("Using vision model boundaries")
                return (
                    left_boundary,
                    right_boundary,
                    log.EnhancedTrajectory.BoundarySource.vision,
                    VISION_DEFAULT_CONFIDENCE
                )
        except Exception as e:
            cloudlog.warning(f"Failed to extract vision boundaries: {e}")

    # Source 3: Default wide corridor (last resort)
    cloudlog.warning("No road boundary data available, using default corridor")
    left_boundary = np.full(33, DEFAULT_CORRIDOR_WIDTH, dtype=np.float32)
    right_boundary = np.full(33, -DEFAULT_CORRIDOR_WIDTH, dtype=np.float32)

    return (
        left_boundary,
        right_boundary,
        log.EnhancedTrajectory.BoundarySource.vision,  # Mark as vision (default source)
        0.0  # Zero confidence indicates default fallback
    )


def validate_corridor_boundaries(
    left_boundary: np.ndarray,
    right_boundary: np.ndarray,
    min_width: float = 2.0,
    max_width: float = 6.0
) -> bool:
    """
    Validate that corridor boundaries are physically plausible.

    Args:
        left_boundary: 33 floats, left edge positions
        right_boundary: 33 floats, right edge positions
        min_width: Minimum lane width (meters)
        max_width: Maximum lane width (meters)

    Returns:
        True if boundaries are valid, False otherwise
    """
    if len(left_boundary) != 33 or len(right_boundary) != 33:
        cloudlog.error(f"Invalid boundary length: left={len(left_boundary)}, right={len(right_boundary)}")
        return False

    # Check all values are finite
    if not np.all(np.isfinite(left_boundary)) or not np.all(np.isfinite(right_boundary)):
        cloudlog.error("Corridor boundaries contain non-finite values")
        return False

    # Check left > right (left should be positive, right should be negative)
    widths = left_boundary - right_boundary

    if not np.all(widths > min_width):
        cloudlog.warning(f"Corridor too narrow: min width={np.min(widths):.2f}m < {min_width}m")
        return False

    if not np.all(widths < max_width):
        cloudlog.warning(f"Corridor too wide: max width={np.max(widths):.2f}m > {max_width}m")
        return False

    return True


def get_corridor_confidence_text(confidence: float) -> str:
    """Get human-readable confidence level."""
    if confidence >= 0.9:
        return "high"
    elif confidence >= 0.7:
        return "good"
    elif confidence >= 0.5:
        return "medium"
    elif confidence > 0.0:
        return "low"
    else:
        return "fallback"
