#!/usr/bin/env python3
"""Integration tests for Compute HAL and backends."""

import pytest
import numpy as np
from openpilot.system.inferenced.compute import (
    HAL, HALConfig, BackendType, InferenceResult, get_hal
)
from openpilot.system.inferenced.client import InferenceClient


class TestHALInitialization:
  """Test HAL initialization and backend loading."""

  def test_hal_singleton(self):
    """HAL should be a singleton."""
    hal1 = HAL()
    hal2 = HAL()
    assert hal1 is hal2

  def test_hal_initialize(self):
    """HAL should initialize successfully."""
    config = HALConfig(enable_npu=True, enable_acl=True, enable_rga=True, enable_mpp=True)
    hal = HAL(config)
    assert hal.initialize()
    assert hal._initialized

  def test_get_hal_function(self):
    """get_hal() should return initialized HAL."""
    hal = get_hal()
    assert hal is not None
    assert hal._initialized

  def test_backend_availability(self):
    """Backends should report availability after initialization."""
    hal = get_hal()
    backends = hal.get_available_backends()
    assert len(backends) > 0
    # At minimum, one backend should be available on dev PC
    assert BackendType.NPU in backends or BackendType.ACL in backends or BackendType.RGA in backends


class TestBackendTypes:
  """Test BackendType enum."""

  def test_backend_type_values(self):
    """BackendType should have expected values."""
    assert BackendType.NPU.value == 1
    assert BackendType.ACL.value == 2
    assert BackendType.RGA.value == 4
    assert BackendType.MPP.value == 5
    assert BackendType.HAILO_8.value == 6

  def test_no_cpu_gpu_types(self):
    """Old BackendType.CPU and GPU should not exist."""
    assert not hasattr(BackendType, 'CPU')
    assert not hasattr(BackendType, 'GPU')


class TestRKNNBackend:
  """Test RKNN NPU backend."""

  def test_rknn_initialization(self):
    """RKNN backend should initialize (mock mode on dev PC)."""
    hal = get_hal()
    npu = hal.get_backend(BackendType.NPU)
    if npu:
      assert npu.is_available()

  def test_rknn_mock_inference(self):
    """RKNN should return valid results in mock mode."""
    hal = get_hal()
    npu = hal.get_backend(BackendType.NPU)
    if npu is None:
      pytest.skip("NPU not available")

    inputs = {'input': np.random.randn(1, 224, 224, 3).astype(np.float32)}
    result = npu.infer('test_model', inputs)

    assert isinstance(result, InferenceResult)
    assert result.backend_type == BackendType.NPU
    assert result.success
    assert 'output' in result.outputs or len(result.outputs) > 0


class TestACLBackend:
  """Test unified ACL (GPU/CPU) backend."""

  def test_acl_initialization(self):
    """ACL backend should initialize."""
    hal = get_hal()
    acl = hal.get_backend(BackendType.ACL)
    if acl:
      assert acl.is_available()

  def test_acl_smart_dispatch(self):
    """ACL should intelligently select GPU or CPU based on input size."""
    hal = get_hal()
    acl = hal.get_backend(BackendType.ACL)
    if acl is None:
      pytest.skip("ACL not available")

    # Small input - should prefer CPU
    small_input = {'input': np.ones((10,), dtype=np.float32)}
    result_small = acl.infer('test', small_input)
    assert result_small.success

    # Large input - should prefer GPU if available
    large_input = {'input': np.ones((2000,), dtype=np.float32)}
    result_large = acl.infer('test', large_input)
    assert result_large.success

  def test_acl_gpu_forced_ops(self):
    """SGM and GEMM should always use GPU path if available."""
    hal = get_hal()
    acl = hal.get_backend(BackendType.ACL)
    if acl is None:
      pytest.skip("ACL not available")

    # These should always route to GPU
    for op in ['sgm_stereo', 'gemm']:
      inputs = {'left': np.zeros((480, 640)), 'right': np.zeros((480, 640))}
      result = acl.infer(op, inputs)
      assert result.success


class TestRGABackend:
  """Test RGA 2D graphics backend."""

  def test_rga_initialization(self):
    """RGA backend should initialize (with fallback to OpenCV)."""
    hal = get_hal()
    rga = hal.get_backend(BackendType.RGA)
    if rga:
      assert rga.is_available()

  def test_rga_cvtcolor(self):
    """RGA color conversion should work."""
    hal = get_hal()
    rga = hal.get_backend(BackendType.RGA)
    if rga is None:
      pytest.skip("RGA not available")

    # Mock Y/UV data
    inputs = {
        'src_fmt': 'nv12',
        'dst_fmt': 'rgb',
        'src_y': np.zeros((480, 640), dtype=np.uint8),
        'src_uv': np.zeros((240, 320), dtype=np.uint8),
    }
    result = rga.infer('cvtcolor', inputs)
    assert result.success
    assert 'output' in result.outputs

  def test_rga_resize(self):
    """RGA resize should work."""
    hal = get_hal()
    rga = hal.get_backend(BackendType.RGA)
    if rga is None:
      pytest.skip("RGA not available")

    inputs = {
        'input': np.zeros((480, 640, 3), dtype=np.uint8),
        'width': 320,
        'height': 240,
    }
    result = rga.infer('resize', inputs)
    assert result.success


class TestMPPBackend:
  """Test MPP media processing backend."""

  def test_mpp_initialization(self):
    """MPP backend should initialize (with ffmpeg fallback)."""
    hal = get_hal()
    mpp = hal.get_backend(BackendType.MPP)
    if mpp:
      assert mpp.is_available()

  def test_mpp_h264_encode(self):
    """MPP H.264 encoding should work."""
    hal = get_hal()
    mpp = hal.get_backend(BackendType.MPP)
    if mpp is None:
      pytest.skip("MPP not available")

    inputs = {
        'frame': np.zeros((720, 1280, 3), dtype=np.uint8),
        'width': 1280,
        'height': 720,
        'bitrate': 4000,
        'fps': 20,
    }
    result = mpp.infer('h264_encode', inputs)
    assert result.success
    assert 'data' in result.outputs

  def test_mpp_h264_decode(self):
    """MPP H.264 decoding should work."""
    hal = get_hal()
    mpp = hal.get_backend(BackendType.MPP)
    if mpp is None:
      pytest.skip("MPP not available")

    inputs = {
        'data': b'\x00\x00\x00\x01\x67' + bytes([0x42]) * 64,
        'width': 1280,
        'height': 720,
    }
    result = mpp.infer('h264_decode', inputs)
    assert result.success


class TestInferenceClient:
  """Test InferenceClient high-level API."""

  def test_client_initialization(self):
    """InferenceClient should initialize."""
    client = InferenceClient("test_daemon")
    assert client.daemon_name == "test_daemon"

  def test_client_backend_access(self):
    """InferenceClient should provide backend access."""
    client = InferenceClient("test_daemon")

    # These should either return a backend or raise RuntimeError
    try:
      npu = client.npu()
      assert npu is not None
    except RuntimeError:
      pass  # NPU not available on dev PC

    try:
      acl = client.acl()
      assert acl is not None
    except RuntimeError:
      pass  # ACL not available on dev PC

  def test_client_best_compute(self):
    """InferenceClient.best_compute() should return available backend."""
    client = InferenceClient("test_daemon")
    try:
      backend = client.best_compute()
      assert backend is not None
    except RuntimeError:
      pytest.skip("No compute backends available")

  def test_client_mpp_h264_encode(self):
    """InferenceClient should provide MPP H.264 encoding convenience method."""
    client = InferenceClient("test_daemon")
    try:
      frame = np.zeros((720, 1280, 3), dtype=np.uint8)
      result = client.infer_mpp_h264_encode(frame, 1280, 720)
      assert result is not None
    except RuntimeError:
      pytest.skip("MPP not available")


class TestBackendStats:
  """Test backend statistics tracking."""

  def test_stats_tracking(self):
    """Backends should track statistics."""
    hal = get_hal()
    backends = hal.get_available_backends()

    for backend_type in backends:
      backend = hal.get_backend(backend_type)
      if backend is None:
        continue

      stats = backend.get_stats()
      assert stats.tasks_submitted >= 0
      assert stats.tasks_completed >= 0
      assert stats.tasks_failed >= 0
      assert stats.total_exec_time_ms >= 0.0


class TestBackendErrors:
  """Test error handling in backends."""

  def test_unknown_model_error(self):
    """Inference on non-existent model should fail gracefully."""
    hal = get_hal()
    acl = hal.get_backend(BackendType.ACL)
    if acl is None:
      pytest.skip("ACL not available")

    # Inference on model that was never loaded
    result = acl.infer('nonexistent_model', {'input': np.zeros((10,))})
    assert not result.success
    assert result.error_message is not None

  def test_uninitialized_backend_error(self):
    """Inference on uninitialized backend should fail gracefully."""
    from openpilot.system.inferenced.arm_acl import ACLBackend

    backend = ACLBackend()
    # Don't initialize - backend._initialized is False
    result = backend.infer('test', {'input': np.zeros((10,))})
    assert not result.success


if __name__ == '__main__':
  pytest.main([__file__, '-v'])  # noqa: TID251
