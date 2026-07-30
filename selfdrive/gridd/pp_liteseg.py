"""
PPLiteSeg wrapper for gridd — road segmentation via centralized HAL.

Runs PP-LiteSeg on NPU Core 1 through InferenceClient,
replacing direct RKNNRunner usage.

Core assignment:
  Core 0 → modeld/driving_vision  (exclusive)
  Core 1 → PP-LiteSeg + other vision models (shared, time-sliced by driver)

PP-LiteSeg (19-class Cityscapes) provides road, sidewalk, and drivable surface masks.
If model file is missing, road_mask is None and gridd uses depth-only BEV detection.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np

from openpilot.common.swaglog import cloudlog
from openpilot.system.inferenced.client import InferenceClient
from openpilot.system.inferenced.compute import BackendType, ModelConfig

ROAD_CLASS_IDS = {0, 1}  # Cityscapes class 0=road, 1=sidewalk

_MODEL_SEARCH = [
    Path("/data/models/pp_liteseg_320_rk3588.rknn"),
    Path("/data/models/pp_liteseg_rk3588.rknn"),
    Path(__file__).parents[2] / "modeld/models/pp_liteseg_320_rk3588.rknn",
    Path(__file__).parents[2] / "modeld/models/pp_liteseg_rk3588.rknn",
]


def _find_model() -> Path | None:
    for p in _MODEL_SEARCH:
        if p.exists():
            return p
    return None


class PPLiteSeg:
    """Road segmentation via InferenceClient on NPU Core 1."""

    MODEL_NAME = 'ppliteseg_gridd'

    def __init__(self) -> None:
        self._client: InferenceClient | None = None
        self._npu = None
        self._available = False

        model_path = _find_model()
        if model_path is None:
            cloudlog.warning("PPLiteSeg: model not found — road_mask will be None (depth-only BEV)")
            return

        try:
            # Use centralized HAL - NPU with Core 1
            self._client = InferenceClient("gridd")
            self._npu = self._client.npu()

            # Load model via HAL
            model_cfg = ModelConfig(
                name=self.MODEL_NAME,
                path=str(model_path),
                model_type="segmentation",
                npu_cores=1,
            )
            if self._npu.load_model(model_cfg):
                self._available = True
                cloudlog.info(f"PPLiteSeg loaded on NPU Core 1: {model_path.name}")
            else:
                cloudlog.error("PPLiteSeg: NPU load_model failed")
                self._npu = None

        except Exception as e:
            cloudlog.error(f"PPLiteSeg init failed: {e}")
            self._npu = None

    @property
    def available(self) -> bool:
        return self._available

    def infer(self, bgr_frame: np.ndarray) -> np.ndarray | None:
        """
        Run PP-LiteSeg inference via HAL.

        Returns:
            (H, W) uint8 class index map, or None on failure.
            Road pixels: (result == 0) | (result == 1)
        """
        if self._npu is None:
            return None
        try:
            result = self._npu.infer(
                model_name=self.MODEL_NAME,
                inputs={'input': bgr_frame}
            )
            if not result.success:
                return None

            output = result.outputs.get('output')
            if output is None:
                return None

            # output shape: [1, C, H, W] or [C, H, W] or [H, W]
            if output.ndim == 4:
                seg = np.argmax(output[0], axis=0)
            elif output.ndim == 3:
                seg = np.argmax(output, axis=0)
            else:
                seg = output
            return seg.astype(np.uint8)

        except Exception as e:
            cloudlog.error(f"PPLiteSeg inference failed: {e}")
            return None

    def release(self) -> None:
        """Release HAL resources."""
        if self._client:
            self._client.release()
            self._client = None
        self._npu = None
        self._available = False
