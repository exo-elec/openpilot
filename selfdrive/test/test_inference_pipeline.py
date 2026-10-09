#!/usr/bin/env python3
"""
Integration test: ONNX inference pipeline (dev PC / x86_64).

Validates the full chain:
  VisionIPC frame → DrivingModelFrame stub → HAL → ONNX backend → model output

Run:
  OPENPILOT_STUB_PARAMS_PYX=1 .venv/bin/python -m pytest \
      selfdrive/test/test_inference_pipeline.py --override-ini="addopts=" -v
"""
import numpy as np
import pytest


@pytest.fixture(scope="module")
def onnx_backend():
    """Initialize HAL and return ONNX backend (skips if models missing)."""
    from openpilot.system.inferenced.compute import HAL, HALConfig, BackendType

    hal = HAL(HALConfig())
    hal.initialize()

    backend = hal.get_backend(BackendType.ONNX)
    if backend is None:
        pytest.skip("ONNX backend not available")
    return backend


@pytest.fixture(scope="module")
def loaded_vision(onnx_backend):
    from openpilot.system.inferenced.compute import ModelConfig
    cfg = ModelConfig(name="driving_vision", path="")
    if not onnx_backend.load_model(cfg):
        pytest.skip("driving_vision.onnx not found — run models/download_models.sh dev-pc")
    return onnx_backend


@pytest.fixture(scope="module")
def loaded_policy(onnx_backend):
    from openpilot.system.inferenced.compute import ModelConfig
    cfg = ModelConfig(name="driving_policy", path="")
    if not onnx_backend.load_model(cfg):
        pytest.skip("driving_policy.onnx not found — run models/download_models.sh dev-pc")
    return onnx_backend


def test_inference_backend_selection():
    """inference_backend() returns ONNX (not mock NPU) on dev PC."""
    from openpilot.system.inferenced.compute import HAL, HALConfig, BackendType
    from openpilot.system.inferenced import InferenceClient

    HAL(HALConfig()).initialize()
    client = InferenceClient("test")
    backend = client.inference_backend()
    assert backend.backend_type == BackendType.ONNX or not getattr(backend, "_use_mock", False)


@pytest.mark.slow
def test_visionbuf_data_access():
    """VisionBuf.data returns a readable numpy buffer.

    Marked slow: requires a running openpilot system or CARLA bridge.
    Run explicitly with: pytest -m slow
    """
    pytest.importorskip("msgq.visionipc", reason="msgq VisionIPC not available")
    from msgq.visionipc import VisionIpcServer, VisionIpcClient, VisionStreamType

    W, H = 256, 128
    server = VisionIpcServer("test_vipc_data")
    server.create_buffers(VisionStreamType.VISION_STREAM_ROAD, 2, W, H)
    server.start_listener()

    client = VisionIpcClient("test_vipc_data", VisionStreamType.VISION_STREAM_ROAD, False)
    if not client.connect(False):
        pytest.skip("VisionIPC shared memory not available — needs running openpilot")

    yuv_size = W * H * 3 // 2
    frame = np.full(yuv_size, 64, dtype=np.uint8)
    server.send(VisionStreamType.VISION_STREAM_ROAD, frame.tobytes(), 0, 0, 0)

    buf = client.recv(timeout_ms=500)
    if buf is None:
        pytest.skip("VisionIPC recv returned None — msgq shm may not be initialized")
    raw = np.frombuffer(buf.data, dtype=np.uint8)
    assert raw.shape[0] == yuv_size
    assert raw[0] == 64


def test_driving_model_frame_stub():
    """DrivingModelFrame stub constructor and buffer interface work without VisionBuf."""
    from openpilot.selfdrive.modeld.models.commonmodel_pyx import CLContext, DrivingModelFrame, _CLMem
    from openpilot.selfdrive.modeld.constants import ModelConstants

    ctx = CLContext()
    assert ctx.context is None  # mock context on dev PC

    frame = DrivingModelFrame(ctx, ModelConstants.TEMPORAL_SKIP)

    # Test buffer_from_cl with a manually created _CLMem
    W, H = 256, 128
    yuv_size = W * H * 3 // 2
    mock_data = np.zeros(yuv_size, dtype=np.uint8)
    mock_cl = _CLMem(mock_data)
    result = frame.buffer_from_cl(mock_cl)
    assert isinstance(result, np.ndarray)
    assert result.dtype == np.uint8
    assert result.shape[0] == yuv_size


def test_vision_model_output_shape(loaded_vision):
    """Vision model produces (1, 1576) output from uint8 image input."""
    img = np.zeros((1, 12, 128, 256), dtype=np.uint8)
    result = loaded_vision.infer("driving_vision", {"img": img, "big_img": img})
    assert result.success, result.error_message
    out = result.outputs.get("outputs")
    assert out is not None
    assert out.shape == (1, 1576)
    assert result.inference_time_ms > 0


def test_policy_model_output_shape(loaded_policy):
    """Policy model produces (1, 1000) output from float16 feature inputs."""
    desire = np.zeros((1, 25, 8), dtype=np.float16)
    traffic = np.zeros((1, 2), dtype=np.float16)
    features = np.zeros((1, 25, 512), dtype=np.float16)

    result = loaded_policy.infer("driving_policy", {
        "desire_pulse": desire,
        "traffic_convention": traffic,
        "features_buffer": features,
    })
    assert result.success, result.error_message
    out = result.outputs.get("outputs")
    assert out is not None
    assert out.shape == (1, 1000)


def test_model_state_init():
    """ModelState initializes with ONNX backend on dev PC without crashing."""
    import os
    os.environ.setdefault("SIMULATION", "1")

    from openpilot.system.inferenced.compute import HAL, HALConfig
    from openpilot.system.inferenced import InferenceClient
    from openpilot.selfdrive.modeld.models.commonmodel_pyx import CLContext
    from openpilot.selfdrive.modeld.modeld import ModelState

    HAL(HALConfig()).initialize()
    client = InferenceClient("modeld_test")
    ctx = CLContext()

    try:
        model = ModelState(ctx, client)
    except FileNotFoundError as e:
        pytest.skip(f"driving model artifacts not present on this host: {e}")
    assert model.vision_input_names == ["img", "big_img"]
    assert model.runner.backend_name in ("ONNX", "NPU")
