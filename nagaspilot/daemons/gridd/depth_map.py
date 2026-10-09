"""
DepthMap - SGBM stereo depth computation for gridd.

Pulls stereo frames from v4l2d (merged v4l2d+stereod) via VisionIPC,
then computes XYZ point cloud using SGBM stereo matching.
"""
import cv2
import numpy as np


class DepthMap:
    def __init__(self, left_map1, left_map2, right_map1, right_map2, Q, downscale: int = 4):
        self.left_map1 = left_map1
        self.left_map2 = left_map2
        self.right_map1 = right_map1
        self.right_map2 = right_map2
        self.Q = Q
        self.downscale = downscale
        self.matcher = cv2.StereoSGBM_create(
            minDisparity=0, numDisparities=160, blockSize=5,
            P1=8*3*5*5, P2=32*3*5*5,
            disp12MaxDiff=1, uniquenessRatio=10,
            speckleWindowSize=100, speckleRange=32,
            mode=cv2.SGBM_MODE_SGBM_3WAY
        )

    def compute(self, left_nv12: bytes, right_nv12: bytes, width: int, height: int) -> 'np.ndarray | None':
        try:
            left_gray = self._nv12_to_gray(left_nv12, width, height)
            right_gray = self._nv12_to_gray(right_nv12, width, height)
            lr = cv2.remap(left_gray, self.left_map1, self.left_map2, cv2.INTER_LINEAR)
            rr = cv2.remap(right_gray, self.right_map1, self.right_map2, cv2.INTER_LINEAR)
            d = self.downscale
            lrs = cv2.resize(lr, (width // d, height // d), interpolation=cv2.INTER_LINEAR)
            rrs = cv2.resize(rr, (width // d, height // d), interpolation=cv2.INTER_LINEAR)
            disp_small = self.matcher.compute(lrs, rrs).astype(np.float32) / 16.0
            disp = cv2.resize(disp_small, (width, height), interpolation=cv2.INTER_LINEAR) * d
            # Scale Q for the full-resolution reprojection
            Q_scaled = self.Q.copy()
            xyz = cv2.reprojectImageTo3D(disp, Q_scaled)
            return xyz
        except Exception:
            return None

    @staticmethod
    def _nv12_to_gray(data: bytes, width: int, height: int) -> np.ndarray:
        nv12 = np.frombuffer(data, dtype=np.uint8).reshape((height * 3 // 2, width))
        return nv12[:height]
