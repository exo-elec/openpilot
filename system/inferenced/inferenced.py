#!/usr/bin/env python3
"""
InferenceD - Centralized Hardware Inference Daemon

Manages all compute hardware with centralized scheduling:
- RKNN NPU (primary)
- Mali GPU (ACL OpenCL)
- ARM CPU (ACL NEON)
- RGA (2D accelerator)
- MPP (media processing)
- Hailo-8 or DEEPX DX-M1 (optional, camera_inference + voice_inference tiers)

Daemons submit jobs via cereal messages; InferenceD manages execution
and prevents resource conflicts.

Architecture:
  modeld, stereod, etc
    └─> InferenceClient (submits via IPC)
             └─> inferenced daemon (owns HAL, schedules jobs)
"""

from __future__ import annotations

import heapq
import json
import os
import time
import threading
from dataclasses import dataclass, field

import numpy as np
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog

from openpilot.system.inferenced.compute import HAL, HALConfig, BackendType, ModelConfig


@dataclass
class InferenceJob:
  """Pending inference job."""
  job_id: int
  daemon_name: str
  backend_type: int
  model_name: str
  priority: int
  timeout_ms: int
  submitted_time: float = field(default_factory=time.monotonic)
  # Input data (serialized from capnp message)
  input_data: bytes = b''
  input_shape: tuple = ()
  input_dtype: str = ''
  # Output data (populated after execution)
  output_data: bytes = b''
  output_shape: tuple = ()
  output_dtype: str = ''


class InferenceD:
  """Centralized inference daemon with resource scheduling."""

  # Model path registry: maps model name → (model_path, model_type)
  # Populated at init; override via MODEL_PATH env var for custom locations
  MODEL_REGISTRY: dict[str, tuple[str, str]] = {
    'driving_vision': ('models/{fmt}/driving_vision.{ext}', 'vision'),
    'driving_policy': ('models/{fmt}/driving_policy.{ext}', 'policy'),
    'yolo_640':       ('models/{fmt}/yolo_640.{ext}', 'detection'),
    'yolo_side':      ('models/hef/yolov8n.hef', 'detection'),  # Hailo-8 HEF, sided+reard — fixed format, no rknn/onnx variant
    'side_yolo_egpu': ('models/onnx/yolo_side.onnx', 'detection'),
    'rear_yolo_egpu': ('models/onnx/yolo_rear.onnx', 'detection'),
    'side_seg_egpu':       ('models/onnx/seg_side.onnx', 'segmentation'),
    'rear_seg_egpu':       ('models/onnx/seg_rear.onnx', 'segmentation'),
    'front_road_seg_egpu': ('models/onnx/seg_front_road.onnx', 'segmentation'),
    'front_wide_seg_egpu': ('models/onnx/seg_front_wide.onnx', 'segmentation'),
    'sgm_stereo':     ('', 'sgm'),  # ACL operation — no model file
    'h264_encode':    ('', 'codec'),  # MPP operation — no model file
    'h264_decode':    ('', 'codec'),
    'resize':         ('', 'image_op'),  # RGA operation
    'cvtcolor':       ('', 'image_op'),
    'crop':           ('', 'image_op'),
  }

  def __init__(self):
    """Initialize inference daemon."""
    set_daemon_affinity("inferenced")

    self.hal = HAL(HALConfig())
    self._initialized = False
    self._running = False
    self._stop_event = threading.Event()

    # Job queue: priority heap ordered by (priority, deadline, seq).
    # Lower priority value = higher priority; earlier deadline wins ties.
    self._job_queue: list[tuple[tuple[int, float, int], InferenceJob]] = []
    self._queue_lock = threading.Lock()
    self._job_seq = 0

    # Messaging (lazy-import cereal to avoid ARM .so on dev PC)
    import cereal.messaging as messaging
    self._messaging = messaging
    self.pm = messaging.PubMaster(['inferencedStatus', 'inferenceJobResult'])
    self.sm = messaging.SubMaster(['inferenceJobRequest'])

    # Stats
    self._tasks_completed = 0
    self._tasks_failed = 0
    self._total_exec_time_ms = 0.0

    # Detect platform format (rknn on ARM, onnx on dev PC)
    self._model_fmt = 'rknn' if os.path.isdir('/sys/bus/platform/devices/rockchip') else 'onnx'
    self._model_ext = 'rknn' if self._model_fmt == 'rknn' else 'onnx'

    cloudlog.info(f"InferenceD: Initialized (model format: {self._model_fmt})")

  def _job_order_key(self, job: InferenceJob) -> tuple[int, float, int]:
    """Return heap ordering key for a job.

    Priority is the primary sort (CRITICAL=0 first).  Deadline is the
    secondary sort so tight-deadline jobs of the same priority run before
    relaxed ones.  A monotonic sequence breaks ties to preserve FIFO order.
    """
    deadline = (job.submitted_time + job.timeout_ms / 1000.0
                if job.timeout_ms > 0 else float('inf'))
    self._job_seq += 1
    return (job.priority, deadline, self._job_seq)

  def initialize(self) -> bool:
    """Initialize hardware abstraction layer."""
    try:
      if not self.hal.initialize():
        cloudlog.error("InferenceD: HAL initialization failed")
        return False
      self._initialized = True
      cloudlog.info("InferenceD: HAL ready")
      return True
    except Exception as e:
      cloudlog.exception(f"InferenceD: Initialization error: {e}")
      return False

  def _resolve_model_path(self, model_name: str) -> str | None:
    """Resolve model name to file path. Returns None for ops without model files."""
    entry = self.MODEL_REGISTRY.get(model_name)
    if entry is None:
      return None
    path_template, model_type = entry
    if not path_template:
      return ''  # Operation-type backend (RGA, MPP, ACL SGM) — no file needed
    # Allow override via env var
    env_key = f"EOP_MODEL_{model_name.upper()}"
    env_path = os.getenv(env_key)
    if env_path:
      return env_path
    # Fixed-format entries (e.g. Hailo .hef) have no {fmt}/{ext} placeholders
    if '{fmt}' not in path_template:
      return path_template
    return path_template.format(fmt=self._model_fmt, ext=self._model_ext)

  def _load_model_for_job(self, job: InferenceJob, backend) -> bool:
    """Load model into backend if not already cached."""
    if not job.model_name:
      return True  # No model needed (e.g. RGA resize)

    if self.hal.is_model_cached(job.model_name):
      return True

    model_path = self._resolve_model_path(job.model_name)
    if model_path is None:
      cloudlog.warning(f"InferenceD: Unknown model '{job.model_name}'")
      return False
    if not model_path:
      return True  # Operation-type backend
    if not os.path.exists(model_path):
      cloudlog.warning(f"InferenceD: Model file not found: {model_path}")
      return False

    config = ModelConfig(
      name=job.model_name,
      path=model_path,
      model_type=self.MODEL_REGISTRY.get(job.model_name, ('', 'unknown'))[1],
    )
    loaded = backend.load_model(config)
    if loaded:
      self.hal.cache_model(config)
    return loaded

  def _deserialize_input(self, job: InferenceJob) -> np.ndarray | None:
    """Deserialize input data from job into numpy array."""
    if not job.input_data or not job.input_shape or not job.input_dtype:
      return None
    try:
      arr = np.frombuffer(job.input_data, dtype=np.dtype(job.input_dtype))
      return arr.reshape(job.input_shape)
    except Exception as e:
      cloudlog.warning(f"InferenceD: Failed to deserialize input: {e}")
      return None

  def _submit_job_result(self, job_id: int, success: bool,
                         exec_time_ms: float = 0.0,
                         error_reason: str = "",
                         output_data: bytes = b'',
                         output_shape: tuple = (),
                         output_dtype: str = '') -> None:
    """Submit result back to requesting daemon."""
    try:
      msg = self._messaging.new_message('inferenceJobResult', valid=True)
      result = msg.inferenceJobResult
      result.timestamp = int(time.monotonic() * 1e9)
      result.jobId = job_id
      result.success = success
      result.executionTimeMs = exec_time_ms
      result.errorReason = error_reason
      if output_data:
        result.outputData = output_data
        result.outputShape = list(output_shape)
        result.outputDtype = output_dtype
        result.resultSize = len(output_data)
      self.pm.send('inferenceJobResult', msg)
    except Exception as e:
      cloudlog.warning(f"InferenceD: Failed to send result: {e}")

  def _process_job_request(self, req) -> None:
    """Process incoming job request."""
    try:
      job = InferenceJob(
          job_id=req.jobId,
          daemon_name=req.daemonName,
          backend_type=req.backendType,
          model_name=req.modelName,
          priority=req.priority,
          timeout_ms=req.timeoutMs,
          input_data=req.inputData.tobytes() if req.inputData else b'',
          input_shape=tuple(req.inputShape) if req.inputShape else (),
          input_dtype=req.inputDtype if req.inputDtype else '',
      )
      with self._queue_lock:
        heapq.heappush(self._job_queue, (self._job_order_key(job), job))
      cloudlog.debug(f"InferenceD: Job {job.job_id} queued from {job.daemon_name}")
    except Exception as e:
      cloudlog.warning(f"InferenceD: Failed to process job request: {e}")
      try:
        self._submit_job_result(req.jobId, success=False, error_reason=f"Job queuing error: {e}")
        self._tasks_failed += 1
      except Exception:
        pass

  def _execute_job(self, job: InferenceJob) -> tuple[bool, str]:
    """
    Execute inference job on appropriate backend.

    Returns:
      (success: bool, error_reason: str)
    """
    try:
      backend_type = BackendType(job.backend_type)
      backend = self.hal.get_backend(backend_type)

      if backend is None:
        return False, f"Backend {backend_type.name} not available"

      if not backend.is_available():
        return False, f"Backend {backend_type.name} not initialized"

      # Load model if required
      if not self._load_model_for_job(job, backend):
        return False, f"Failed to load model '{job.model_name}'"

      # Deserialize input
      input_array = self._deserialize_input(job)
      if input_array is None and job.input_data:
        return False, "Failed to deserialize input data"

      # Build inputs dict for backend.infer()
      inputs = {}
      if input_array is not None:
        inputs['input'] = input_array
      # MPP and RGA ops use named parameters, not 'input'
      if backend_type == BackendType.MPP and job.model_name in ('h264_encode', 'h264_decode'):
        # Reconstruct MPP inputs from job metadata if needed
        # For now, h264_encode path expects frame/width/height in inputs
        pass
      if backend_type == BackendType.RGA and job.model_name in ('resize', 'cvtcolor', 'crop'):
        # RGA ops expect width/height in inputs
        pass

      # Run inference
      cloudlog.debug(f"InferenceD: Executing job {job.job_id} on {backend_type.name} (model: {job.model_name})")
      result = backend.infer(model_name=job.model_name, inputs=inputs)

      if not result.success:
        return False, result.error_message or "Inference failed"

      # Serialize primary output for IPC result message
      # Supports single-output models; multi-output stored in result metadata
      if result.outputs:
        if len(result.outputs) > 1:
          cloudlog.warning(
            f"InferenceD: Job {job.job_id} produced {len(result.outputs)} outputs; " +
            f"only '{next(iter(result.outputs))}' will be serialized over IPC. " +
            "TODO: extend capnp schema for multi-output models."
          )
        # Pick the first output for serialization
        first_key = next(iter(result.outputs))
        output_arr = result.outputs[first_key]
        if isinstance(output_arr, np.ndarray):
          job.output_data = output_arr.tobytes()
          job.output_shape = output_arr.shape
          job.output_dtype = str(output_arr.dtype)
        elif isinstance(output_arr, bytes):
          job.output_data = output_arr
          job.output_shape = (len(output_arr),)
          job.output_dtype = 'uint8'

      return True, ""

    except ValueError:
      return False, f"Invalid backend type: {job.backend_type}"
    except Exception as e:
      return False, f"Execution error: {str(e)}"

  def run(self) -> int:
    """Main daemon loop."""
    if not self.initialize():
      return 1

    self._running = True
    rk = Ratekeeper(10)  # 10 Hz

    try:
      while self._running and not self._stop_event.is_set():
        try:
          # Receive job submissions
          self.sm.update(0)
          if self.sm.updated['inferenceJobRequest']:
            self._process_job_request(self.sm['inferenceJobRequest'])

          # Drain the priority heap under lock, then execute without holding it.
          # Releasing the lock during execution lets _process_job_request
          # enqueue new jobs while this batch runs.
          with self._queue_lock:
            pending = []
            while self._job_queue:
              pending.append(heapq.heappop(self._job_queue)[1])

          for job in pending:
            success = False
            error_reason = ""
            exec_time_ms = 0.0
            start_time = time.monotonic()

            try:
              cloudlog.debug(f"InferenceD: Processing job {job.job_id} on backend {job.backend_type} (model: {job.model_name})")

              try:
                success, error_reason = self._execute_job(job)
              except Exception as e:
                success = False
                error_reason = f"Job execution error: {str(e)}"
                cloudlog.exception(f"InferenceD: Job execution failed: {e}")

              exec_time_ms = (time.monotonic() - start_time) * 1000

              # timeout_ms == 0 means unlimited; only check when a limit is set
              if job.timeout_ms > 0 and exec_time_ms > job.timeout_ms:
                cloudlog.warning(f"InferenceD: Job {job.job_id} exceeded timeout ({exec_time_ms:.1f}ms > {job.timeout_ms}ms)")
                # Don't override success — job completed; caller checks executionTimeMs

              self._submit_job_result(
                  job.job_id, success=success,
                  exec_time_ms=exec_time_ms,
                  error_reason=error_reason if not success else "",
                  output_data=job.output_data,
                  output_shape=job.output_shape,
                  output_dtype=job.output_dtype,
              )

              if success:
                self._tasks_completed += 1
                self._total_exec_time_ms += exec_time_ms
              else:
                self._tasks_failed += 1
                cloudlog.warning(f"InferenceD: Job {job.job_id} failed: {error_reason}")

            except Exception as e:
              exec_time_ms = (time.monotonic() - start_time) * 1000
              cloudlog.exception(f"InferenceD: Unexpected error in job {job.job_id}: {e}")
              self._submit_job_result(job.job_id, success=False,
                                     exec_time_ms=exec_time_ms,
                                     error_reason=str(e))
              self._tasks_failed += 1

          # Publish status
          self._publish_status()

          rk.keep_time()
        except Exception as e:
          cloudlog.exception(f"InferenceD: Loop error: {e}")
          time.sleep(0.1)

    except KeyboardInterrupt:
      cloudlog.info("InferenceD: Interrupted")
    finally:
      self.stop()

    return 0

  def _publish_status(self) -> None:
    """Publish inference daemon status."""
    try:
      msg = self._messaging.new_message('inferencedStatus', valid=True)
      status = msg.inferencedStatus
      status.timestamp = int(time.monotonic() * 1e9)
      status.enabled = self._initialized
      status.npuAvailable = self.hal.get_backend(BackendType.NPU) is not None
      status.gpuAvailable = self.hal.get_backend(BackendType.ACL) is not None
      status.fault = not self._initialized
      status.faultReason = "initialization_failed" if not self._initialized else ""
      status.tasksCompleted = self._tasks_completed
      status.tasksFailed = self._tasks_failed
      status.avgExecTimeMs = (self._total_exec_time_ms / self._tasks_completed
                              if self._tasks_completed > 0 else 0.0)

      # Capability discovery: advertise backends and loadable models so daemons
      # can decide whether to schedule optional/enhancement workloads.
      try:
        available_backends = self.hal.get_available_backends()
        status.availableBackends = [bt.name for bt in available_backends]
        status.availableModels = list(self.hal._models_cache.keys())
        health_report = self.hal.get_backend_health_report()
        status.backendHealth = json.dumps(health_report, default=str)
      except Exception as e:
        cloudlog.debug(f"InferenceD: capability snapshot error: {e}")
        status.availableBackends = []
        status.availableModels = []
        status.backendHealth = ""

      self.pm.send('inferencedStatus', msg)
    except Exception as e:
      cloudlog.debug(f"InferenceD: Status publish error: {e}")

  def stop(self) -> None:
    """Stop daemon and release resources."""
    self._running = False
    self._stop_event.set()
    try:
      self.hal.release()
    except Exception:
      pass
    cloudlog.info("InferenceD: Stopped")


def main() -> int:
  """Entry point."""
  try:
    daemon = InferenceD()
    return daemon.run()
  except Exception as e:
    cloudlog.exception(f"InferenceD: Fatal error: {e}")
    return 1


if __name__ == "__main__":
  exit(main())
