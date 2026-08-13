#!/usr/bin/env python3
"""Integration tests for gridd RGA preprocessing and InferenceClient integration.

Tests:
  - RGA resize pipeline (hardware or OpenCV fallback)
  - PP-LiteSeg input preprocessing
  - Crop/resize operations via InferenceClient
  - OpenCV fallback on dev PC
"""
from __future__ import annotations

import pytest
import numpy as np

from openpilot.system.inferenced import InferenceClient, BackendType
from openpilot.system.inferenced.compute import get_hal


class TestGridDRGAIntegration:
    """Test RGA preprocessing pipeline for gridd."""

    def test_rga_resize_available(self):
        """RGA backend should be available (OpenCV fallback on dev PC)."""
        hal = get_hal()
        rga = hal.get_backend(BackendType.RGA)
        assert rga is not None, "RGA backend not available"
        assert rga.is_available()

    def test_rga_resize_720p_to_320(self):
        """Resize 1280x720 frame to 320x320 (typical road camera → PP-LiteSeg)."""
        hal = get_hal()
        rga = hal.get_backend(BackendType.RGA)
        if rga is None:
            pytest.skip("RGA not available")

        frame = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
        result = rga.infer(
            'resize',
            {'input': frame, 'width': 320, 'height': 320}
        )
        assert result.success, f"RGA resize failed: {result.error_message}"
        assert 'output' in result.outputs
        output = result.outputs['output']
        assert output.shape == (320, 320, 3)

    def test_rga_resize_preserves_dtype(self):
        """RGA resize should preserve uint8 dtype."""
        hal = get_hal()
        rga = hal.get_backend(BackendType.RGA)
        if rga is None:
            pytest.skip("RGA not available")

        frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
        result = rga.infer(
            'resize',
            {'input': frame, 'width': 320, 'height': 240}
        )
        assert result.success
        output = result.outputs['output']
        assert output.dtype == np.uint8

    def test_rga_crop_operation(self):
        """RGA crop operation should extract ROI."""
        hal = get_hal()
        rga = hal.get_backend(BackendType.RGA)
        if rga is None:
            pytest.skip("RGA not available")

        frame = np.arange(0, 480 * 640 * 3, dtype=np.uint8).reshape((480, 640, 3))
        result = rga.infer(
            'crop',
            {'input': frame, 'x': 100, 'y': 50, 'width': 200, 'height': 150}
        )
        assert result.success, f"RGA crop failed: {result.error_message}"
        assert 'output' in result.outputs
        output = result.outputs['output']
        assert output.shape == (150, 200, 3)

    def test_inference_client_rga_resize(self):
        """InferenceClient.rga() should provide resize for gridd preprocessing."""
        client = InferenceClient("gridd_test")
        try:
            rga = client.rga()
            frame = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
            result = rga.infer(
                'resize',
                {'input': frame, 'width': 320, 'height': 320}
            )
            assert result.success
            assert result.outputs['output'].shape == (320, 320, 3)
        except RuntimeError:
            pytest.skip("RGA not available via InferenceClient")

    def test_gridd_preprocess_frame_mock(self):
        """Simulate gridd _preprocess_frame with RGA fallback."""
        from openpilot.selfdrive.gridd.gridd import PPLITESEG_INPUT_SIZE

        hal = get_hal()
        rga = hal.get_backend(BackendType.RGA)

        frame = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
        target_w, target_h = PPLITESEG_INPUT_SIZE

        if rga is not None:
            result = rga.infer(
                'resize',
                {'input': frame, 'width': target_w, 'height': target_h}
            )
            if result.success:
                preprocessed = result.outputs['output']
                assert preprocessed.shape == (target_h, target_w, 3)
                return

        # OpenCV fallback
        import cv2
        preprocessed = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        assert preprocessed.shape == (target_h, target_w, 3)


class TestGridDMPPIntegration:
    """Test MPP encode/decode cycle (recordd-relevant)."""

    def test_mpp_h264_encode_stub(self):
        """MPP H.264 encode should return data (stub on dev PC)."""
        hal = get_hal()
        mpp = hal.get_backend(BackendType.MPP)
        if mpp is None:
            pytest.skip("MPP not available")

        frame = np.zeros((720, 1280), dtype=np.uint8)  # NV12-ish
        result = mpp.infer(
            'h264_encode',
            {'frame': frame, 'width': 1280, 'height': 720, 'bitrate': 4000, 'fps': 20}
        )
        assert result.success
        assert 'data' in result.outputs
        assert len(result.outputs['data']) > 0

    def test_mpp_h264_decode_stub(self):
        """MPP H.264 decode should return frame (stub on dev PC)."""
        hal = get_hal()
        mpp = hal.get_backend(BackendType.MPP)
        if mpp is None:
            pytest.skip("MPP not available")

        encoded = b'\x00\x00\x00\x01\x67' + bytes([0x42]) * 64
        result = mpp.infer(
            'h264_decode',
            {'data': encoded, 'width': 1280, 'height': 720}
        )
        assert result.success
        assert 'data' in result.outputs
        decoded = result.outputs['data']
        assert isinstance(decoded, np.ndarray)
        assert decoded.shape == (720, 1280, 3)


class TestGridDIPCLatency:
    """Measure IPC-style latency (HAL round-trip) for gridd operations."""

    def test_rga_resize_latency(self):
        """RGA resize should complete within reasonable time."""
        hal = get_hal()
        rga = hal.get_backend(BackendType.RGA)
        if rga is None:
            pytest.skip("RGA not available")

        import time
        frame = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)

        start = time.perf_counter()
        result = rga.infer(
            'resize',
            {'input': frame, 'width': 320, 'height': 320}
        )
        latency_ms = (time.perf_counter() - start) * 1000

        assert result.success
        assert latency_ms < 100.0, f"RGA resize too slow: {latency_ms:.1f}ms"

    def test_inference_client_latency(self):
        """InferenceClient round-trip latency should be low (in-process HAL)."""
        client = InferenceClient("latency_test")
        try:
            rga = client.rga()
            import time
            frame = np.random.randint(0, 256, (480, 640, 3), dtype=np.uint8)

            latencies = []
            for _ in range(10):
                start = time.perf_counter()
                result = rga.infer('resize', {'input': frame, 'width': 320, 'height': 240})
                latencies.append((time.perf_counter() - start) * 1000)
                assert result.success

            avg_latency = sum(latencies) / len(latencies)
            assert avg_latency < 50.0, f"Avg latency too high: {avg_latency:.1f}ms"
        except RuntimeError:
            pytest.skip("RGA not available")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])  # noqa: TID251
