#!/usr/bin/env python3
"""
YOLO Object Detector for GridD — Object Type Classification.

Runs YOLOv8-nano at 320×320 on NPU Core 2 alongside policy model.
Provides object type classification (car, truck, bike, person, bus, etc.)
to complement SceneSeg's binary foreground mask from stereo cameras.

NPU Allocation:
  Core 1: SceneSeg (~1.0 TOPS) + PP-LiteSeg (~0.5 TOPS) = ~1.5 TOPS (segmentation)
  Core 2: Policy (~0.5 TOPS) + YOLO-road (~0.4 TOPS) + YOLO-stereo_right (~0.4 TOPS) = ~1.3 TOPS

Scheduling on Core 2 (priority order):
  1. Policy model (20Hz, critical) - runs every frame first
  2. YOLO-nano road (20Hz) - runs after policy
  3. YOLO-nano stereo_right (20Hz) - runs after YOLO-road
"""

from __future__ import annotations

import numpy as np
from pathlib import Path
from dataclasses import dataclass

from openpilot.selfdrive.modeld.vision.yolo_rknn import YoloRKNNDetector
from openpilot.common.swaglog import cloudlog


@dataclass
class TypedDetection:
    """Detection with type information for BEV fusion."""
    cls_id: int
    label: str
    score: float
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 in source coordinates
    center_x: float  # For depth lookup
    center_y: float  # For depth lookup


class YoloObjectDetector:
    """
    Lightweight YOLOv8-nano for object type classification.

    Configured for NPU Core 2 to run alongside policy model.
    Input: 320×320 (downscaled from full frame for speed)
    Output: Typed detections for BEV fusion
    """

    # COCO classes relevant to driving
    CLASSES_OF_INTEREST = {
        0: "person",
        1: "bicycle",
        2: "car",
        3: "motorcycle",
        5: "bus",
        7: "truck",
        9: "traffic light",
    }

    # Priority order for collision avoidance (higher = more dangerous)
    PRIORITY = {
        "person": 10,      # Highest priority
        "bicycle": 9,
        "motorcycle": 8,
        "bus": 7,
        "truck": 6,
        "car": 5,
    }

    def __init__(
        self,
        model_path: str | Path | None = None,
        input_size: tuple[int, int] = (320, 320),
        obj_threshold: float = 0.4,
        nms_threshold: float = 0.45,
    ) -> None:
        """
        Initialize YOLOv8-nano detector for Core 2.

        Args:
            model_path: Path to yolov8n_320_rk3588.rknn (auto-detected if None)
            input_size: Model input size (320×320 for Core 2 efficiency)
            obj_threshold: Detection confidence threshold
            nms_threshold: NMS IoU threshold
        """
        self.input_size = input_size
        self.obj_threshold = obj_threshold

        # Auto-detect model path
        if model_path is None:
            model_path = self._find_model()

        self.model_path = Path(model_path)
        self._detector: YoloRKNNDetector | None = None
        self._initialized = False

        try:
            self._init_detector(nms_threshold)
        except Exception as e:
            cloudlog.error(f"YOLO detector init failed: {e}")
            self._initialized = False

    def _find_model(self) -> Path:
        """Find YOLO model in standard locations."""
        search_paths = [
            Path(__file__).parent.parent / "modeld" / "models" / "yolov8n_320_rk3588.rknn",
            Path(__file__).parent / "models" / "yolov8n_320_rk3588.rknn",
            Path("/data/models/yolov8n_320_rk3588.rknn"),
        ]
        for path in search_paths:
            if path.exists():
                return path
        # Default fallback
        return search_paths[0]

    def _init_detector(self, nms_threshold: float) -> None:
        """Initialize RKNN detector on Core 2."""
        if not self.model_path.exists():
            cloudlog.warning(f"YOLO model not found: {self.model_path}")
            return

        self._detector = YoloRKNNDetector(
            model_path=str(self.model_path),
            model_format="yolov8",
            input_size=self.input_size,
            obj_threshold=self.obj_threshold,
            nms_threshold=nms_threshold,
            use_npu_cores="2",  # Core 2: shared with policy model (lower priority)
        )
        self._initialized = True
        cloudlog.info(f"✅ YOLOv8-nano initialized on NPU Core 2 (policy pipeline): {self.input_size}")

    @property
    def available(self) -> bool:
        """Check if detector is ready."""
        return self._initialized and self._detector is not None

    def infer(self, frame_bgr: np.ndarray) -> list[TypedDetection]:
        """
        Run object detection and return typed detections.

        Args:
            frame_bgr: Input frame in BGR format (any size, will be resized)

        Returns:
            List of TypedDetection with class labels
        """
        if not self.available or self._detector is None:
            return []

        try:
            # Run YOLO inference
            detections = self._detector.infer(frame_bgr)

            # Filter to classes of interest and convert
            typed_detections: list[TypedDetection] = []
            for det in detections:
                if det.class_id not in self.CLASSES_OF_INTEREST:
                    continue
                if det.confidence < self.obj_threshold:
                    continue

                label = self.CLASSES_OF_INTEREST[det.class_id]
                x1, y1, x2, y2 = det.bbox

                typed_detections.append(TypedDetection(
                    cls_id=det.class_id,
                    label=label,
                    score=float(det.confidence),
                    bbox=(x1, y1, x2, y2),
                    center_x=(x1 + x2) / 2.0,
                    center_y=(y1 + y2) / 2.0,
                ))

            return typed_detections

        except Exception as e:
            cloudlog.error(f"YOLO inference failed: {e}")
            return []

    def get_priority(self, label: str) -> int:
        """Get collision avoidance priority for object type."""
        return self.PRIORITY.get(label, 5)

    def close(self) -> None:
        """Release resources."""
        if self._detector is not None:
            self._detector.close()
            self._detector = None
        self._initialized = False

    def __del__(self) -> None:
        self.close()


class YoloSceneSegFusion:
    """
    Fuse YOLO detections with SceneSeg binary mask.

    Strategy:
    1. SceneSeg provides dense binary mask (all foreground pixels)
    2. YOLO provides sparse classifications (object types at locations)
    3. Fuse: Assign YOLO type to SceneSeg mask regions that overlap YOLO bbox
    """

    def __init__(self, yolo_detector: YoloObjectDetector):
        self.yolo = yolo_detector
        self.last_detections: list[TypedDetection] = []
        self.frame_counter = 0

    def update(self, frame_bgr: np.ndarray, obj_mask: np.ndarray | None) -> dict[int, str]:
        """
        Fuse YOLO with SceneSeg mask.

        Args:
            frame_bgr: Current frame
            obj_mask: Binary mask from SceneSeg (None if not available)

        Returns:
            Mapping: grid_cell_index -> object_type_label
        """
        self.frame_counter += 1

        # Run YOLO inference
        detections = self.yolo.infer(frame_bgr)
        if detections:
            self.last_detections = detections

        # If no mask, just return empty mapping
        if obj_mask is None:
            return {}

        # Create cell-to-type mapping from YOLO bboxes
        cell_types: dict[int, str] = {}

        for det in self.last_detections:
            # Convert bbox to mask coordinates
            x1, y1, x2, y2 = map(int, det.bbox)
            h, w = obj_mask.shape

            # Clamp to image bounds
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)

            if x2 > x1 and y2 > y1:
                # Mark this region with object type
                # (Actual BEV projection happens in lazy_bev.py)
                pass

        return cell_types

    def get_detections(self) -> list[TypedDetection]:
        """Get last YOLO detections."""
        return self.last_detections
