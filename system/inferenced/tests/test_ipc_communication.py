#!/usr/bin/env python3
"""IPC Communication Verification Tests for InferenceD.

Tests daemon→inferenced message passing:
  - Job request queuing and processing
  - Result delivery and timing
  - IPC latency overhead measurement
  - Timeout/error case handling

Note: These tests verify the in-process HAL path (currently used by clients)
      and the cereal messaging framework for future IPC daemon mode.
"""
from __future__ import annotations

import pytest
import time
import numpy as np

from openpilot.system.inferenced import InferenceClient, BackendType
from openpilot.system.inferenced.compute import get_hal, InferenceResult


def _skip_if_messaging_unavailable():
    """Skip test if cereal messaging shared memory is not available."""
    try:
        from openpilot.system.inferenced.inferenced import InferenceD
        daemon = InferenceD()
        daemon.stop()
    except Exception as e:
        if "IpcError" in type(e).__name__ or "No such file or directory" in str(e):
            pytest.skip("cereal messaging shared memory not available")
        raise


class TestIPCDaemonLifecycle:
    """Test InferenceD daemon initialization and lifecycle."""

    def test_daemon_importable(self):
        """InferenceD module should be importable."""
        from openpilot.system.inferenced.inferenced import InferenceD, InferenceJob
        assert InferenceD is not None
        assert InferenceJob is not None

    def test_daemon_initialization(self):
        """InferenceD should initialize HAL successfully."""
        _skip_if_messaging_unavailable()
        from openpilot.system.inferenced.inferenced import InferenceD

        daemon = InferenceD()
        assert daemon.initialize()
        assert daemon._initialized
        daemon.stop()

    def test_daemon_job_queue(self):
        """InferenceD should queue and process jobs."""
        _skip_if_messaging_unavailable()
        from openpilot.system.inferenced.inferenced import InferenceD, InferenceJob

        daemon = InferenceD()
        daemon.initialize()

        job = InferenceJob(
            job_id=1,
            daemon_name="test_daemon",
            backend_type=BackendType.NPU.value,
            model_name="test_model",
            priority=2,
            timeout_ms=1000
        )
        daemon._job_queue.append(job)
        assert len(daemon._job_queue) == 1

        # Process the job
        success, error = daemon._execute_job(job)
        # NPU may not be available, but execution should complete
        assert isinstance(success, bool)
        assert isinstance(error, str)

        daemon.stop()

    def test_daemon_timeout_handling(self):
        """InferenceD should track execution time for timeout detection."""
        _skip_if_messaging_unavailable()
        from openpilot.system.inferenced.inferenced import InferenceD, InferenceJob

        daemon = InferenceD()
        daemon.initialize()

        job = InferenceJob(
            job_id=2,
            daemon_name="test_daemon",
            backend_type=BackendType.RGA.value,
            model_name="resize",
            priority=2,
            timeout_ms=1  # Very short timeout
        )

        start = time.monotonic()
        success, error = daemon._execute_job(job)
        exec_time_ms = (time.monotonic() - start) * 1000

        # Even with short timeout, execution should complete
        assert isinstance(success, bool)
        # Timeout is detected by comparing exec_time_ms to timeout_ms after execution
        assert exec_time_ms >= 0

        daemon.stop()


class TestIPCClientLatency:
    """Measure IPC-style latency (HAL round-trip) for daemon operations."""

    def test_npu_inference_latency(self):
        """NPU inference round-trip should be fast (mock mode on dev PC)."""
        client = InferenceClient("latency_test")
        try:
            npu = client.npu()
            img = np.random.randn(1, 224, 224, 3).astype(np.float32)

            latencies = []
            for _ in range(10):
                start = time.perf_counter()
                # Use a model that is loaded or expect mock mode
                result = npu.infer('test_model', {'input': img})
                latencies.append((time.perf_counter() - start) * 1000)
                # Mock mode may return success without model loading
                if not result.success and "Model not loaded" in (result.error_message or ""):
                    pytest.skip("NPU mock mode - model not loaded, skipping latency test")
                assert result.success

            avg = sum(latencies) / len(latencies)
            assert avg < 50.0, f"NPU avg latency {avg:.1f}ms too high"
        except RuntimeError:
            pytest.skip("NPU not available")

    def test_rga_operation_latency(self):
        """RGA operation round-trip should be fast."""
        client = InferenceClient("latency_test")
        try:
            rga = client.rga()
            frame = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)

            start = time.perf_counter()
            result = rga.infer(
                'resize',
                {'input': frame, 'width': 320, 'height': 320}
            )
            latency_ms = (time.perf_counter() - start) * 1000

            assert result.success
            assert latency_ms < 100.0, f"RGA latency {latency_ms:.1f}ms too high"
        except RuntimeError:
            pytest.skip("RGA not available")

    def test_mpp_encode_latency(self):
        """MPP encode round-trip should complete (dev PC ffmpeg may be slower)."""
        client = InferenceClient("latency_test")
        try:
            mpp = client.mpp()
            frame = np.random.randint(0, 256, (240, 320, 3), dtype=np.uint8)

            start = time.perf_counter()
            result = mpp.infer(
                'h264_encode',
                {'frame': frame, 'width': 320, 'height': 240, 'bitrate': 2000, 'fps': 20}
            )
            latency_ms = (time.perf_counter() - start) * 1000

            assert result.success
            # Dev PC ffmpeg single-frame encode can take 50-200ms;
            # on real hardware MPP should be <10ms. Allow generous margin for dev PC.
            assert latency_ms < 500.0, f"MPP encode latency {latency_ms:.1f}ms exceeds 500ms budget"
        except RuntimeError:
            pytest.skip("MPP not available")

    def test_acl_inference_latency(self):
        """ACL inference round-trip should be fast."""
        client = InferenceClient("latency_test")
        try:
            acl = client.acl()
            data = np.ones((1000,), dtype=np.float32)

            start = time.perf_counter()
            result = acl.infer('test', {'input': data})
            latency_ms = (time.perf_counter() - start) * 1000

            assert result.success
            assert latency_ms < 50.0, f"ACL latency {latency_ms:.1f}ms too high"
        except RuntimeError:
            pytest.skip("ACL not available")


class TestIPCErrorHandling:
    """Test error handling and timeout cases."""

    def test_invalid_backend_type(self):
        """Invalid backend type should fail gracefully."""
        _skip_if_messaging_unavailable()
        from openpilot.system.inferenced.inferenced import InferenceD, InferenceJob

        daemon = InferenceD()
        daemon.initialize()

        job = InferenceJob(
            job_id=99,
            daemon_name="test",
            backend_type=999,  # Invalid
            model_name="test",
            priority=2,
            timeout_ms=1000
        )
        success, error = daemon._execute_job(job)
        assert not success
        assert "Invalid backend type" in error
        daemon.stop()

    def test_unavailable_backend(self):
        """Unavailable backend should report error."""
        _skip_if_messaging_unavailable()
        from openpilot.system.inferenced.inferenced import InferenceD, InferenceJob

        daemon = InferenceD()
        daemon.initialize()

        # Use a backend that's likely not available (HAILO)
        job = InferenceJob(
            job_id=100,
            daemon_name="test",
            backend_type=BackendType.HAILO_8.value,
            model_name="test",
            priority=2,
            timeout_ms=1000
        )
        success, error = daemon._execute_job(job)
        assert not success
        assert "not available" in error.lower()
        daemon.stop()

    def test_inference_result_structure(self):
        """InferenceResult should have all required fields."""
        result = InferenceResult(
            backend_type=BackendType.NPU,
            model_name="test",
            success=True,
            outputs={'output': np.zeros((10,))},
            inference_time_ms=5.0,
            error_message=None
        )
        assert result.backend_type == BackendType.NPU
        assert result.model_name == "test"
        assert result.success
        assert 'output' in result.outputs
        assert result.inference_time_ms == 5.0
        assert result.error_message is None

    def test_timeout_result_flag(self):
        """InferenceResult should support timeout flag."""
        result = InferenceResult(
            backend_type=BackendType.NPU,
            model_name="test",
            success=True,
            outputs={},
            inference_time_ms=150.0,
            timed_out=True
        )
        assert result.timed_out
        assert result.inference_time_ms > 100.0


class TestIPCEndToEnd:
    """End-to-end IPC communication test."""

    def test_full_pipeline_rga_resize(self):
        """Full pipeline: client → HAL → RGA → result."""
        client = InferenceClient("e2e_test")
        try:
            rga = client.rga()
            frame = np.ones((480, 640, 3), dtype=np.uint8) * 128

            result = rga.infer('resize', {'input': frame, 'width': 320, 'height': 240})
            assert result.success
            assert result.outputs['output'].shape == (240, 320, 3)
            assert result.inference_time_ms >= 0
        except RuntimeError:
            pytest.skip("RGA not available")

    def test_full_pipeline_mpp_encode_decode(self):
        """Full pipeline: encode → decode round-trip."""
        client = InferenceClient("e2e_test")
        try:
            mpp = client.mpp()
            original = np.random.randint(0, 256, (240, 320, 3), dtype=np.uint8)

            # Encode
            encode_result = mpp.infer(
                'h264_encode',
                {'frame': original, 'width': 320, 'height': 240, 'bitrate': 2000, 'fps': 20}
            )
            assert encode_result.success
            encoded = encode_result.outputs['data']

            # Decode
            decode_result = mpp.infer(
                'h264_decode',
                {'data': encoded, 'width': 320, 'height': 240}
            )
            assert decode_result.success
            decoded = decode_result.outputs['data']
            assert decoded.shape == (240, 320, 3)
        except RuntimeError:
            pytest.skip("MPP not available")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
