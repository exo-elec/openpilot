#!/usr/bin/env python3
"""
Test InferenceD daemon job execution.

Mocks cereal messaging so the test runs on dev PC without ARM .so files.
Verifies:
  - Job request parsing with input data
  - Model loading and caching
  - Inference execution and output serialization
  - Result message construction with output data
"""

from __future__ import annotations

import heapq
import sys
import time
import unittest  # noqa: TID251
from unittest.mock import MagicMock  # noqa: TID251
from dataclasses import dataclass

import numpy as np

# Placeholders filled by setUpModule after mocking cereal.
InferenceD = None  # type: ignore
InferenceJob = None  # type: ignore
InferenceResult = None  # type: ignore


def setUpModule():
    """Mock cereal.messaging only while inferenced.py is imported.

    Keeping the mock scoped to test execution (not module-collection time)
    prevents later test modules from importing a mocked ``cereal``.
    """
    global InferenceD, InferenceJob, InferenceResult
    mock_messaging = MagicMock()
    mock_messaging.PubMaster = lambda _: MagicMock()
    mock_messaging.SubMaster = lambda _: MagicMock()
    mock_messaging.new_message = lambda service, valid=True: MagicMock()
    sys.modules['cereal'] = MagicMock()
    sys.modules['cereal.messaging'] = mock_messaging
    from openpilot.system.inferenced.inferenced import InferenceD as _InferenceD
    from openpilot.system.inferenced.inferenced import InferenceJob as _InferenceJob
    from openpilot.system.inferenced.compute import InferenceResult as _InferenceResult
    InferenceD = _InferenceD
    InferenceJob = _InferenceJob
    InferenceResult = _InferenceResult


def tearDownModule():
    """Restore real cereal modules so later tests in the same process are not poisoned."""
    sys.modules.pop('cereal.messaging', None)
    sys.modules.pop('cereal', None)



@dataclass
class _MockCapnpData:
    """Mock capnp Data field."""
    _bytes: bytes
    def tobytes(self):
        return self._bytes


@dataclass
class _MockCapnpList:
    """Mock capnp List field."""
    _items: list
    def __iter__(self):
        return iter(self._items)


@dataclass
class _MockJobRequest:
    """Mock InferenceJobRequest capnp message."""
    jobId: int = 1
    daemonName: str = "testd"
    backendType: int = 1  # NPU
    modelName: str = "driving_vision"
    priority: int = 2
    timeoutMs: int = 1000
    inputData: object = None
    inputShape: object = None
    inputDtype: str = ""


class _MockPubMaster:
    """Mock PubMaster that captures sent messages."""
    def __init__(self):
        self.sent = []

    def send(self, service, msg):
        self.sent.append((service, msg))


class _MockSubMaster:
    """Mock SubMaster — unused in these tests (daemon reads from this)."""
    def __init__(self):
        self.updated = {}
        self._data = {}

    def update(self, timeout):
        pass

    def __getitem__(self, key):
        return self._data.get(key)


class TestDaemonExecution(unittest.TestCase):

    def _make_daemon(self):
        """Create InferenceD with mocked messaging."""
        daemon = InferenceD.__new__(InferenceD)
        daemon.hal = MagicMock()
        daemon._initialized = True
        daemon._running = False
        daemon._stop_event = MagicMock()
        daemon._job_queue = []
        daemon._queue_lock = MagicMock()
        daemon._job_seq = 0
        daemon._tasks_completed = 0
        daemon._tasks_failed = 0
        daemon._total_exec_time_ms = 0.0
        daemon._model_fmt = 'onnx'
        daemon._model_ext = 'onnx'
        daemon.pm = self.pub
        daemon.sm = self.sub
        daemon._messaging = MagicMock()
        daemon._messaging.new_message = lambda service, valid=True: self._new_message()
        return daemon

    def _new_message(self):
        """Factory for mock messages."""
        msg = MagicMock()
        msg.inferenceJobResult = MagicMock()
        msg.inferencedStatus = MagicMock()
        return msg

    def setUp(self):
        self.pub = _MockPubMaster()
        self.sub = _MockSubMaster()

    # ------------------------------------------------------------------
    # Input deserialization
    # ------------------------------------------------------------------

    def test_deserialize_input_float32(self):
        """Test that float32 input data is correctly deserialized."""
        daemon = self._make_daemon()
        arr = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        job = InferenceJob(
            job_id=1, daemon_name="t", backend_type=1,
            model_name="m", priority=2, timeout_ms=100,
            input_data=arr.tobytes(),
            input_shape=arr.shape,
            input_dtype=str(arr.dtype),
        )
        result = daemon._deserialize_input(job)
        self.assertIsNotNone(result)
        np.testing.assert_array_equal(result, arr)

    def test_deserialize_input_uint8(self):
        """Test uint8 image data deserialization."""
        daemon = self._make_daemon()
        arr = np.zeros((1, 12, 128, 256), dtype=np.uint8)
        arr[0, 0, 0, 0] = 255
        job = InferenceJob(
            job_id=1, daemon_name="t", backend_type=1,
            model_name="m", priority=2, timeout_ms=100,
            input_data=arr.tobytes(),
            input_shape=arr.shape,
            input_dtype=str(arr.dtype),
        )
        result = daemon._deserialize_input(job)
        self.assertIsNotNone(result)
        np.testing.assert_array_equal(result, arr)

    def test_deserialize_input_missing(self):
        """Test None returned when input fields are empty."""
        daemon = self._make_daemon()
        job = InferenceJob(
            job_id=1, daemon_name="t", backend_type=1,
            model_name="m", priority=2, timeout_ms=100,
        )
        result = daemon._deserialize_input(job)
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Model path resolution
    # ------------------------------------------------------------------

    def test_resolve_model_path_onnx(self):
        """Dev PC resolves to ONNX path."""
        daemon = self._make_daemon()
        daemon._model_fmt = "onnx"
        daemon._model_ext = "onnx"
        path = daemon._resolve_model_path("driving_vision")
        self.assertIn("onnx/driving_vision.onnx", path)

    def test_resolve_model_path_rknn(self):
        """ARM resolves to RKNN path."""
        daemon = self._make_daemon()
        daemon._model_fmt = "rknn"
        daemon._model_ext = "rknn"
        path = daemon._resolve_model_path("driving_vision")
        self.assertIn("rknn/driving_vision.rknn", path)

    def test_resolve_model_path_env_override(self):
        """EOP_MODEL_* env var overrides registry."""
        import os
        os.environ["EOP_MODEL_DRIVING_VISION"] = "/custom/model.rknn"
        daemon = self._make_daemon()
        path = daemon._resolve_model_path("driving_vision")
        self.assertEqual(path, "/custom/model.rknn")
        del os.environ["EOP_MODEL_DRIVING_VISION"]

    def test_resolve_model_path_no_file(self):
        """Operations like resize have no model file."""
        daemon = self._make_daemon()
        path = daemon._resolve_model_path("resize")
        self.assertEqual(path, "")

    def test_resolve_unknown_model(self):
        """Unknown model returns None."""
        daemon = self._make_daemon()
        path = daemon._resolve_model_path("unknown_model_xyz")
        self.assertIsNone(path)

    # ------------------------------------------------------------------
    # _execute_job with mocked backend
    # ------------------------------------------------------------------

    def test_execute_job_backend_unavailable(self):
        """Job fails when backend is not in HAL."""
        daemon = self._make_daemon()
        daemon.hal.get_backend.return_value = None

        job = InferenceJob(
            job_id=1, daemon_name="t", backend_type=1,  # NPU
            model_name="m", priority=2, timeout_ms=100,
        )
        success, reason = daemon._execute_job(job)
        self.assertFalse(success)
        self.assertIn("not available", reason)

    def test_execute_job_backend_not_initialized(self):
        """Job fails when backend exists but is not initialized."""
        daemon = self._make_daemon()
        backend = MagicMock()
        backend.is_available.return_value = False
        daemon.hal.get_backend.return_value = backend

        job = InferenceJob(
            job_id=1, daemon_name="t", backend_type=1,
            model_name="m", priority=2, timeout_ms=100,
        )
        success, reason = daemon._execute_job(job)
        self.assertFalse(success)
        self.assertIn("not initialized", reason)

    def test_execute_job_success(self):
        """Full inference round-trip with mocked backend."""
        daemon = self._make_daemon()

        # Mock backend that returns a known output
        backend = MagicMock()
        backend.is_available.return_value = True
        out_arr = np.array([0.5, 0.3, 0.2], dtype=np.float32)
        backend.infer.return_value = InferenceResult(
            success=True,
            outputs={"output": out_arr},
        )

        daemon.hal.get_backend.return_value = backend
        daemon.hal.is_model_cached.return_value = True

        input_arr = np.array([[1.0, 2.0]], dtype=np.float32)
        job = InferenceJob(
            job_id=1, daemon_name="t", backend_type=1,
            model_name="driving_vision", priority=2, timeout_ms=100,
            input_data=input_arr.tobytes(),
            input_shape=input_arr.shape,
            input_dtype=str(input_arr.dtype),
        )
        success, reason = daemon._execute_job(job)
        self.assertTrue(success)
        self.assertEqual(reason, "")

        # Verify output was serialized into job
        self.assertTrue(len(job.output_data) > 0)
        self.assertEqual(job.output_shape, (3,))
        self.assertEqual(job.output_dtype, "float32")

        # Verify backend was called with correct inputs
        backend.infer.assert_called_once()
        call_kwargs = backend.infer.call_args[1]
        self.assertEqual(call_kwargs["model_name"], "driving_vision")
        np.testing.assert_array_equal(call_kwargs["inputs"]["input"], input_arr)

    def test_execute_job_inference_failure(self):
        """Backend infer() returns failure."""
        daemon = self._make_daemon()
        backend = MagicMock()
        backend.is_available.return_value = True
        backend.infer.return_value = InferenceResult(
            success=False, error_message="NPU out of memory",
        )
        daemon.hal.get_backend.return_value = backend
        daemon.hal.is_model_cached.return_value = True

        job = InferenceJob(
            job_id=1, daemon_name="t", backend_type=1,
            model_name="m", priority=2, timeout_ms=100,
        )
        success, reason = daemon._execute_job(job)
        self.assertFalse(success)
        self.assertIn("NPU out of memory", reason)

    def test_execute_job_no_input(self):
        """Operation without input data (e.g. status query) succeeds."""
        daemon = self._make_daemon()
        backend = MagicMock()
        backend.is_available.return_value = True
        backend.infer.return_value = InferenceResult(success=True, outputs={})
        daemon.hal.get_backend.return_value = backend
        daemon.hal.is_model_cached.return_value = True

        job = InferenceJob(
            job_id=1, daemon_name="t", backend_type=1,
            model_name="m", priority=2, timeout_ms=100,
        )
        success, reason = daemon._execute_job(job)
        self.assertTrue(success)
        backend.infer.assert_called_once_with(model_name="m", inputs={})

    # ------------------------------------------------------------------
    # _submit_job_result with output data
    # ------------------------------------------------------------------

    def test_submit_result_with_output(self):
        """Result message includes serialized output tensor."""
        daemon = self._make_daemon()
        daemon._submit_job_result(
            job_id=42, success=True, exec_time_ms=12.5,
            output_data=b"\x00\x01\x02\x03",
            output_shape=(2, 2),
            output_dtype="uint8",
        )
        self.assertEqual(len(self.pub.sent), 1)
        service, msg = self.pub.sent[0]
        self.assertEqual(service, "inferenceJobResult")
        result = msg.inferenceJobResult
        self.assertEqual(result.jobId, 42)
        self.assertTrue(result.success)
        self.assertAlmostEqual(result.executionTimeMs, 12.5)
        self.assertEqual(result.outputData, b"\x00\x01\x02\x03")
        self.assertEqual(list(result.outputShape), [2, 2])
        self.assertEqual(result.outputDtype, "uint8")
        self.assertEqual(result.resultSize, 4)

    def test_submit_result_without_output(self):
        """Result message works fine when output is empty."""
        daemon = self._make_daemon()
        daemon._submit_job_result(
            job_id=1, success=False, exec_time_ms=0.0,
            error_reason="backend missing",
        )
        self.assertEqual(len(self.pub.sent), 1)
        service, msg = self.pub.sent[0]
        self.assertEqual(service, "inferenceJobResult")
        result = msg.inferenceJobResult
        self.assertFalse(result.success)
        self.assertEqual(result.errorReason, "backend missing")

    # ------------------------------------------------------------------
    # _process_job_request captures input data
    # ------------------------------------------------------------------

    def test_process_request_with_input(self):
        """Job request parsing extracts input tensor from capnp message."""
        daemon = self._make_daemon()
        arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        req = _MockJobRequest(
            jobId=7,
            inputData=_MockCapnpData(arr.tobytes()),
            inputShape=_MockCapnpList([3]),
            inputDtype="float32",
        )
        daemon._process_job_request(req)
        self.assertEqual(len(daemon._job_queue), 1)
        _key, job = daemon._job_queue[0]
        self.assertEqual(job.job_id, 7)
        self.assertEqual(job.input_shape, (3,))
        self.assertEqual(job.input_dtype, "float32")
        self.assertEqual(len(job.input_data), 12)  # 3 floats

    def test_priority_ordering(self):
        """Heap orders jobs by priority, then deadline."""
        daemon = self._make_daemon()
        now = time.monotonic()

        # Low-priority, distant deadline
        low = InferenceJob(
            job_id=1, daemon_name="t", backend_type=1,
            model_name="m", priority=3, timeout_ms=1000,
            submitted_time=now,
        )
        # High-priority, distant deadline
        high = InferenceJob(
            job_id=2, daemon_name="t", backend_type=1,
            model_name="m", priority=0, timeout_ms=1000,
            submitted_time=now,
        )
        # Normal priority, tight deadline
        urgent = InferenceJob(
            job_id=3, daemon_name="t", backend_type=1,
            model_name="m", priority=2, timeout_ms=1,
            submitted_time=now,
        )

        for job in (low, high, urgent):
            heapq.heappush(daemon._job_queue, (daemon._job_order_key(job), job))

        order = [heapq.heappop(daemon._job_queue)[1].job_id for _ in range(3)]
        self.assertEqual(order, [2, 3, 1])

    def test_publish_status_includes_capabilities(self):
        """Status message advertises available backends, models and health."""
        from openpilot.system.inferenced.compute import BackendType

        daemon = self._make_daemon()
        daemon.hal.get_available_backends.return_value = [BackendType.NPU, BackendType.EGPU]
        daemon.hal._models_cache = {'side_yolo_egpu': MagicMock(), 'rear_yolo_egpu': MagicMock()}
        daemon.hal.get_backend_health_report.return_value = {'NPU': 'ok', 'EGPU': 'ok'}

        daemon._publish_status()

        self.assertEqual(len(self.pub.sent), 1)
        service, msg = self.pub.sent[0]
        self.assertEqual(service, "inferencedStatus")
        status = msg.inferencedStatus
        self.assertEqual(list(status.availableBackends), ['NPU', 'EGPU'])
        self.assertEqual(sorted(status.availableModels), ['rear_yolo_egpu', 'side_yolo_egpu'])
        self.assertIn('NPU', status.backendHealth)
        self.assertIn('EGPU', status.backendHealth)


if __name__ == "__main__":
    unittest.main()
