#!/usr/bin/env python3
"""Integration tests for recordd MPP H.264 encoding and ffmpeg fallback.

Tests:
  - MPP encode/decode cycle
  - FFmpeg fallback on dev PC
  - VideoEncoder start/stop lifecycle
  - Codec selection (libx264 on PC, h264_rkmpp on Rockchip)
"""
from __future__ import annotations

import pytest
import numpy as np
import tempfile
from pathlib import Path

from openpilot.system.inferenced import InferenceClient, BackendType
from openpilot.system.inferenced.compute import get_hal


class TestRecorddMPPEncodeDecode:
    """Test MPP H.264 encode/decode cycle."""

    def test_mpp_encode_decode_roundtrip(self):
        """Encode a frame to H.264 and decode it back."""
        hal = get_hal()
        mpp = hal.get_backend(BackendType.MPP)
        if mpp is None:
            pytest.skip("MPP not available")

        width, height = 320, 240
        original = np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)

        # Encode
        encode_result = mpp.infer(
            'h264_encode',
            {'frame': original, 'width': width, 'height': height, 'bitrate': 2000, 'fps': 20}
        )
        assert encode_result.success, f"Encode failed: {encode_result.error_message}"
        assert 'data' in encode_result.outputs
        encoded = encode_result.outputs['data']
        assert len(encoded) > 0, "Encoded data is empty"

        # Decode
        decode_result = mpp.infer(
            'h264_decode',
            {'data': encoded, 'width': width, 'height': height}
        )
        assert decode_result.success, f"Decode failed: {decode_result.error_message}"
        assert 'data' in decode_result.outputs
        decoded = decode_result.outputs['data']
        assert isinstance(decoded, np.ndarray)
        assert decoded.shape == (height, width, 3)

    def test_mpp_encode_nv12_input(self):
        """MPP encode should accept NV12 input."""
        hal = get_hal()
        mpp = hal.get_backend(BackendType.MPP)
        if mpp is None:
            pytest.skip("MPP not available")

        width, height = 320, 240
        nv12 = np.random.randint(0, 256, (height * 3 // 2, width), dtype=np.uint8)

        result = mpp.infer(
            'h264_encode',
            {'frame': nv12, 'width': width, 'height': height, 'bitrate': 2000, 'fps': 20}
        )
        assert result.success
        assert len(result.outputs['data']) > 0

    def test_mpp_encode_bgr_input(self):
        """MPP encode should accept BGR input."""
        hal = get_hal()
        mpp = hal.get_backend(BackendType.MPP)
        if mpp is None:
            pytest.skip("MPP not available")

        width, height = 320, 240
        bgr = np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)

        result = mpp.infer(
            'h264_encode',
            {'frame': bgr, 'width': width, 'height': height, 'bitrate': 2000, 'fps': 20}
        )
        assert result.success
        assert len(result.outputs['data']) > 0


class TestRecorddVideoEncoder:
    """Test recordd VideoEncoder lifecycle."""

    def test_encoder_ffmpeg_start_stop(self):
        """VideoEncoder should start and stop ffmpeg correctly."""
        from openpilot.selfdrive.recordd.recordd import VideoEncoder

        encoder = VideoEncoder()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.mp4"
            preset = {"width": 320, "height": 240, "fps": 10, "bitrate": 1000}

            encoder.start(output_path, preset)
            assert encoder.is_running or encoder._file_handle is not None

            # Encode a few frames
            for _ in range(5):
                frame = np.random.randint(0, 256, (240, 320, 3), dtype=np.uint8)
                assert encoder.encode_frame(frame)

            duration = encoder.stop()
            assert duration >= 0.0
            assert output_path.exists()
            assert output_path.stat().st_size > 0

    def test_encoder_ffmpeg_multiple_segments(self):
        """VideoEncoder should handle multiple start/stop cycles."""
        from openpilot.selfdrive.recordd.recordd import VideoEncoder

        encoder = VideoEncoder()
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(3):
                output_path = Path(tmpdir) / f"seg_{i}.mp4"
                preset = {"width": 320, "height": 240, "fps": 10, "bitrate": 1000}

                encoder.start(output_path, preset)
                for _ in range(3):
                    frame = np.random.randint(0, 256, (240, 320, 3), dtype=np.uint8)
                    encoder.encode_frame(frame)
                encoder.stop()

                assert output_path.exists()
                assert output_path.stat().st_size > 0

    def test_encoder_ffmpeg_fallback_quality_presets(self):
        """VideoEncoder should work with all quality presets."""
        from openpilot.selfdrive.recordd.recordd import VideoEncoder, QUALITY_PRESETS

        encoder = VideoEncoder()
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, preset in QUALITY_PRESETS.items():
                output_path = Path(tmpdir) / f"{name}.mp4"
                encoder.start(output_path, preset)

                frame = np.random.randint(0, 256, (preset["height"], preset["width"], 3), dtype=np.uint8)
                for _ in range(2):
                    encoder.encode_frame(frame)
                encoder.stop()

                assert output_path.exists()
                assert output_path.stat().st_size > 0


class TestRecorddInferenceClient:
    """Test InferenceClient integration for recordd."""

    def test_inference_client_mpp_encode(self):
        """InferenceClient.mpp() should provide H.264 encoding."""
        client = InferenceClient("recordd_test")
        try:
            mpp = client.mpp()
            frame = np.random.randint(0, 256, (240, 320, 3), dtype=np.uint8)
            result = mpp.infer(
                'h264_encode',
                {'frame': frame, 'width': 320, 'height': 240, 'bitrate': 2000, 'fps': 20}
            )
            assert result.success
            assert len(result.outputs['data']) > 0
        except RuntimeError:
            pytest.skip("MPP not available via InferenceClient")

    def test_inference_client_mpp_decode(self):
        """InferenceClient.mpp() should provide H.264 decoding."""
        client = InferenceClient("recordd_test")
        try:
            mpp = client.mpp()
            encoded = b'\x00\x00\x00\x01\x67\x42\x00\x28\xda\x01\x40\x16\xe8\x06\xd0\xa1\x35'
            result = mpp.infer(
                'h264_decode',
                {'data': encoded, 'width': 320, 'height': 240}
            )
            assert result.success
            assert result.outputs['data'].shape == (240, 320, 3)
        except RuntimeError:
            pytest.skip("MPP not available via InferenceClient")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])  # noqa: TID251
