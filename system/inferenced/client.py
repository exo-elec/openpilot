#!/usr/bin/env python3
"""
InferenceClient - Interface to centralized inference backends

Daemons use this client to access compute resources (NPU, GPU, CPU, RGA, MPP).
The client manages resource access and caches backend instances.

Two modes:
  1. Direct HAL (default) — client owns HAL, calls backends directly.
     Use this on dev PC or when inferenced daemon is not running.
  2. IPC via inferenced daemon — client submits jobs via cereal messages.
     Use this on ARM hardware for centralized scheduling and resource conflict
     prevention. Falls back to direct HAL if daemon is unavailable.

Usage:
    from openpilot.system.inferenced.client import InferenceClient

    # Direct HAL mode (default)
    client = InferenceClient("modeld")
    npu = client.npu()
    result = npu.infer(model_name="driving_model", inputs={'input': data})

    # IPC mode (submit to inferenced daemon)
    client = InferenceClient("modeld", use_ipc=True)
    result = client.submit_job(
        backend_type=BackendType.NPU,
        model_name="driving_model",
        input_array=data,
        timeout_ms=1000,
    )
"""

from __future__ import annotations

import os
import time
import threading
from dataclasses import dataclass

import numpy as np
from openpilot.common.swaglog import cloudlog
from openpilot.system.inferenced.compute import (
    get_hal, BackendType, HardwareBackend, InferenceResult
)

# Cereal IPC is optional — falls back to direct HAL on dev PC or if unavailable
try:
    import cereal.messaging as messaging
    _CEREAL_AVAILABLE = True
except ImportError:
    _CEREAL_AVAILABLE = False


@dataclass
class ClientConfig:
    """Client configuration."""
    daemon_name: str
    npu_cores: str = "0"


class InferenceClient:
    """
    Client for accessing inference hardware backends.

    Supports two execution paths:
      - Direct HAL (default): fastest, no IPC overhead, works on dev PC.
      - Daemon IPC: centralized scheduling, prevents resource conflicts,
        required for multi-daemon concurrent NPU access on ARM.
    """

    _job_id_counter = threading.Lock()
    _job_sequence = 1

    def __init__(self, daemon_name: str, config: ClientConfig | None = None,
                 use_ipc: bool = False):
        """
        Initialize inference client.

        Args:
            daemon_name: Name of the daemon (e.g., "modeld", "stereod")
            config: Optional client configuration
            use_ipc: If True, submit jobs to inferenced daemon via cereal IPC.
                     Falls back to direct HAL if daemon/cereal unavailable.
        """
        self.daemon_name = daemon_name
        self.config = config or ClientConfig(daemon_name=daemon_name)
        self.use_ipc = use_ipc and _CEREAL_AVAILABLE

        # Direct HAL (fallback path) — lazily created on first actual use.
        # Constructing InferenceClient must never eagerly touch hardware: several
        # daemons on the same box each create one of these, and eager get_hal()
        # here used to initialize every enabled backend (including exclusive-owner
        # devices like Hailo-8's VDevice) in every one of those processes at
        # startup, racing for single-owner hardware. See hailo() below.
        self.__hal = None

        # IPC state (lazy-initialized)
        self._pm = None
        self._sm = None
        self._ipc_initialized = False

        # Cache backends
        self._npu: HardwareBackend | None = None
        self._acl: HardwareBackend | None = None
        self._rga: HardwareBackend | None = None
        self._mpp: HardwareBackend | None = None
        self._hailo: HardwareBackend | None = None

        if self.use_ipc:
            cloudlog.info(f"InferenceClient initialized for {daemon_name} (IPC mode)")
        else:
            cloudlog.info(f"InferenceClient initialized for {daemon_name} (direct HAL)")

    @property
    def _hal(self):
        """Local HAL singleton, created on first actual use (not at construction)."""
        if self.__hal is None:
            self.__hal = get_hal()
        return self.__hal

    def _init_ipc(self) -> bool:
        """Initialize cereal PubMaster/SubMaster for IPC."""
        if self._ipc_initialized:
            return True
        if not _CEREAL_AVAILABLE:
            return False
        try:
            self._pm = messaging.PubMaster(['inferenceJobRequest'])
            self._sm = messaging.SubMaster(['inferenceJobResult'])
            self._ipc_initialized = True
            return True
        except Exception as e:
            cloudlog.warning(f"InferenceClient IPC init failed: {e}")
            self.use_ipc = False
            return False

    def _init_status_ipc(self) -> bool:
        """Lazy SubMaster for inferencedStatus capability discovery."""
        if not _CEREAL_AVAILABLE:
            return False
        try:
            if not hasattr(self, '_sm_status') or self._sm_status is None:
                self._sm_status = messaging.SubMaster(['inferencedStatus'])
            return True
        except Exception as e:
            cloudlog.debug(f"InferenceClient status IPC init failed: {e}")
            return False

    def _query_status(self, timeout: float = 0.2) -> object | None:
        """Return latest inferencedStatus message, or None if unavailable."""
        if not self._init_status_ipc():
            return None
        try:
            self._sm_status.update(int(timeout * 1000))
            if self._sm_status.updated['inferencedStatus']:
                return self._sm_status['inferencedStatus']
        except Exception as e:
            cloudlog.debug(f"InferenceClient status query failed: {e}")
        return None

    def _next_job_id(self) -> int:
        """Generate a process-qualified UInt32 job ID.

        Multiple daemon processes publish onto the same request/result services.
        A process-local counter alone lets sided and reard both issue job 1 and
        accept each other's result. Reserve the high 16 bits for the PID and
        use the low 16 bits as the per-process sequence.
        """
        with InferenceClient._job_id_counter:
            sequence = InferenceClient._job_sequence & 0xFFFF
            if sequence == 0:
                sequence = 1
            jid = ((os.getpid() & 0xFFFF) << 16) | sequence
            InferenceClient._job_sequence = (sequence + 1) & 0xFFFF
            return jid

    def submit_job(
        self,
        backend_type: BackendType,
        model_name: str,
        input_array: np.ndarray | None = None,
        priority: int = 2,
        timeout_ms: int = 0,
        wait_result: bool = True,
        poll_timeout_ms: float = 5000.0,
        allow_direct_fallback: bool = True,
    ) -> InferenceResult:
        """
        Submit an inference job to the centralized inferenced daemon.

        Serializes input_array into the cereal InferenceJobRequest message,
        publishes it, and optionally waits for the matching InferenceJobResult.

        Args:
            backend_type: Which backend to use (NPU, ACL, RGA, MPP, etc.)
            model_name: Model or operation name
            input_array: Input numpy array (optional for ops like h264_encode)
            priority: 0=critical, 1=high, 2=normal, 3=low
            timeout_ms: Max execution time (0 = unlimited)
            wait_result: If True, block until result received or poll_timeout_ms
            poll_timeout_ms: Max time to wait for result
            allow_direct_fallback: If False, fail closed when daemon IPC is
              unavailable. Required for exclusive-owner devices such as the
              USB eGPU, which must only be opened by inferenced.

        Returns:
            InferenceResult with success flag and outputs dict.
            On IPC failure, falls back to direct HAL execution.
        """
        if not self.use_ipc or not self._init_ipc():
            if allow_direct_fallback:
                return self._direct_infer(backend_type, model_name, input_array)
            return InferenceResult(
                backend_type=backend_type,
                model_name=model_name,
                success=False,
                error_message="Inference daemon IPC unavailable; direct fallback disabled",
            )

        job_id = self._next_job_id()

        try:
            msg = messaging.new_message('inferenceJobRequest', valid=True)
            req = msg.inferenceJobRequest
            req.timestamp = int(time.monotonic() * 1e9)
            req.jobId = job_id
            req.daemonName = self.daemon_name
            req.backendType = backend_type.value
            req.modelName = model_name
            req.priority = priority
            req.timeoutMs = timeout_ms

            if input_array is not None:
                req.inputData = input_array.tobytes()
                req.inputShape = list(input_array.shape)
                req.inputDtype = str(input_array.dtype)

            self._pm.send('inferenceJobRequest', msg)
            cloudlog.debug(f"InferenceClient: Submitted job {job_id} ({model_name})")

            if not wait_result:
                return InferenceResult(success=True, outputs={},
                                       error_message="Async job submitted")

            # Poll for result
            deadline = time.monotonic() + (poll_timeout_ms / 1000.0)
            while time.monotonic() < deadline:
                self._sm.update(100)  # 100ms timeout
                if self._sm.updated['inferenceJobResult']:
                    result_msg = self._sm['inferenceJobResult']
                    if result_msg.jobId == job_id:
                        if result_msg.success and result_msg.outputData:
                            out_arr = np.frombuffer(
                                result_msg.outputData,
                                dtype=np.dtype(result_msg.outputDtype)
                            ).reshape(tuple(result_msg.outputShape))
                            return InferenceResult(
                                success=True,
                                outputs={'output': out_arr},
                            )
                        else:
                            return InferenceResult(
                                success=False,
                                error_message=result_msg.errorReason or "Daemon returned failure",
                            )
                time.sleep(0.01)

            return InferenceResult(
                success=False,
                error_message=f"IPC timeout: no result for job {job_id} within {poll_timeout_ms}ms",
            )

        except Exception as e:
            if allow_direct_fallback:
                cloudlog.warning(f"InferenceClient IPC error: {e}, falling back to direct HAL")
                return self._direct_infer(backend_type, model_name, input_array)
            cloudlog.warning(f"InferenceClient IPC error: {e}; direct fallback disabled")
            return InferenceResult(
                backend_type=backend_type,
                model_name=model_name,
                success=False,
                error_message=f"Inference daemon IPC error: {e}",
            )

    def _direct_infer(self, backend_type: BackendType, model_name: str,
                      input_array: np.ndarray | None) -> InferenceResult:
        """Execute inference directly via HAL (fallback path)."""
        try:
            backend = self._hal.get_backend(backend_type)
            if backend is None:
                return InferenceResult(
                    success=False,
                    error_message=f"Backend {backend_type.name} not available",
                )
            inputs = {'input': input_array} if input_array is not None else {}
            return backend.infer(model_name=model_name, inputs=inputs)
        except Exception as e:
            return InferenceResult(
                success=False,
                error_message=f"Direct HAL inference failed: {e}",
            )

    def npu(self) -> HardwareBackend:
        """Get NPU backend (RKNN)."""
        if self._npu is None:
            self._npu = self._hal.get_backend(BackendType.NPU)
            if self._npu is None:
                raise RuntimeError(f"[{self.daemon_name}] NPU not available")
        return self._npu

    def acl(self) -> HardwareBackend:
        """Get ACL backend (ARM Compute Library - GPU preferred, falls back to CPU)."""
        if self._acl is None:
            self._acl = self._hal.get_backend(BackendType.ACL)
            if self._acl is None:
                raise RuntimeError(f"[{self.daemon_name}] ACL backend not available")
        return self._acl

    def rga(self) -> HardwareBackend:
        """Get RGA backend (2D accelerator)."""
        if self._rga is None:
            self._rga = self._hal.get_backend(BackendType.RGA)
            if self._rga is None:
                raise RuntimeError(f"[{self.daemon_name}] RGA not available")
        return self._rga

    def mpp(self) -> HardwareBackend:
        """Get MPP backend (media processing - video encode/decode)."""
        if self._mpp is None:
            self._mpp = self._hal.get_backend(BackendType.MPP)
            if self._mpp is None:
                raise RuntimeError(f"[{self.daemon_name}] MPP not available")
        return self._mpp

    def hailo(self) -> HardwareBackend:
        """Get Hailo-8 backend (optional - camera inference tier)."""
        if self._hailo is None:
            self._hailo = self._hal.get_backend(BackendType.HAILO_8)
            if self._hailo is None:
                raise RuntimeError(f"[{self.daemon_name}] Hailo not available")
        return self._hailo

    def onnx(self) -> HardwareBackend:
        """Get ONNX Runtime backend (dev PC / x86_64 fallback)."""
        from openpilot.system.inferenced.compute import BackendType as BT
        backend = self._hal.get_backend(BT.ONNX)
        if backend is None:
            raise RuntimeError(f"[{self.daemon_name}] ONNX Runtime not available")
        return backend

    def inference_backend(self) -> HardwareBackend:
        """
        Return the best available neural-network backend.

        Priority order:
          1. RKNN NPU when running on real ARM hardware (not mock)
          2. ONNX Runtime (dev PC / x86_64, or explicit override via EOP_BACKEND=onnx)
          3. RKNN NPU in mock mode (last resort — returns random outputs)

        Set EOP_BACKEND=onnx to force ONNX on any platform (useful in CI).
        """
        force_onnx = os.getenv("EOP_BACKEND", "").lower() == "onnx"

        if not force_onnx:
            try:
                npu = self.npu()
                if not getattr(npu, '_use_mock', False):
                    return npu
            except RuntimeError:
                pass

        try:
            return self.onnx()
        except RuntimeError:
            pass

        try:
            return self.npu()
        except RuntimeError:
            pass

        raise RuntimeError(f"[{self.daemon_name}] No inference backend available (tried NPU, ONNX)")

    def best_compute(self) -> HardwareBackend:
        """Get best available compute backend (ACL with GPU/CPU auto-select)."""
        return self.acl()

    def infer_mpp_h264_encode(
        self,
        frame: object,
        width: int,
        height: int,
        bitrate: int = 4000,
        fps: int = 20,
        priority: int = 2,
        timeout_ms: float = 50.0,
    ) -> object:
        """
        Encode frame to H.264 using MPP backend.

        Args:
            frame: NV12 frame data (numpy array)
            width: Frame width
            height: Frame height
            bitrate: Bitrate in kbps (default 4000)
            fps: Frames per second (default 20)
            priority: Job priority (reserved for IPC-based scheduling)
            timeout_ms: Timeout in milliseconds (reserved for IPC-based timeout)

        Returns:
            InferenceResult with success and outputs['data'] containing encoded bytes
        """
        try:
            mpp = self.mpp()
            result = mpp.infer(
                model_name='h264_encode',
                inputs={
                    'frame': frame,
                    'width': width,
                    'height': height,
                    'bitrate': bitrate,
                    'fps': fps,
                }
            )
            return result
        except Exception as e:
            cloudlog.exception(f"MPP H.264 encoding failed: {e}")
            return InferenceResult(
                success=False,
                error_message=str(e),
                outputs={}
            )

    def get_available_backends(self) -> list[str]:
        """Return backend names advertised by inferenced, or direct HAL fallback."""
        status = self._query_status(timeout=0.2)
        if status is not None and status.availableBackends:
            return list(status.availableBackends)
        # Direct HAL fallback (dev PC / no daemon running)
        try:
            return [bt.name for bt in self._hal.get_available_backends()]
        except Exception as e:
            cloudlog.debug(f"InferenceClient direct HAL backend list failed: {e}")
            return []

    def get_available_models(self) -> list[str]:
        """Return model IDs advertised as loaded/loadable by inferenced."""
        status = self._query_status(timeout=0.2)
        if status is not None and status.availableModels:
            return list(status.availableModels)
        try:
            return list(self._hal._models_cache.keys())
        except Exception as e:
            cloudlog.debug(f"InferenceClient direct HAL model list failed: {e}")
            return []

    def can_run_model(self, model_name: str) -> bool:
        """Check if a model is currently loaded or loadable."""
        return model_name in self.get_available_models()

    def wait_for_backend(self, backend_type: BackendType, timeout: float = 5.0) -> bool:
        """Block until a backend is advertised by inferenced (or direct HAL)."""
        deadline = time.monotonic() + timeout
        name = backend_type.name
        while time.monotonic() < deadline:
            if name in self.get_available_backends():
                return True
            time.sleep(0.05)
        return False

    def release(self) -> None:
        """Release client resources."""
        cloudlog.info(f"InferenceClient released for {self.daemon_name}")
