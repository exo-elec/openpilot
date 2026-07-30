#!/usr/bin/env python3
"""
ModelD — Neural Network Inference Daemon

Uses centralized InferenceD HAL for NPU inference.
All hardware access goes through system.inferenced.

Usage:
    python3 -m openpilot.selfdrive.modeld.modeld
"""

import os
import time
import pickle
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import cereal.messaging as messaging
from cereal import car, log
from msgq.visionipc import VisionIpcClient, VisionStreamType, VisionBuf

from openpilot.common.swaglog import cloudlog
from openpilot.common.params import Params
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import config_realtime_process, DT_MDL
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.transformations.camera import DEVICE_CAMERAS
from openpilot.common.transformations.model import get_warp_matrix
from openpilot.system.inferenced.compute import ModelConfig

from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
from openpilot.selfdrive.controls.lib.drive_helpers import (
    get_accel_from_plan, smooth_value, get_curvature_from_plan
)
from openpilot.selfdrive.modeld.parse_model_outputs import Parser
from openpilot.selfdrive.modeld.fill_model_msg import fill_model_msg, fill_pose_msg, PublishState
from openpilot.selfdrive.modeld.constants import ModelConstants, Plan
from openpilot.selfdrive.modeld.models.commonmodel_pyx import DrivingModelFrame, CLContext

# Centralized inference HAL - ONLY way to access NPU
from openpilot.system.inferenced import InferenceClient


PROCESS_NAME = "selfdrive.modeld.modeld"
SEND_RAW_PRED = os.getenv('SEND_RAW_PRED')

# Repo-level models directory (relative to openpilot root)
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_REPO_MODELS = _REPO_ROOT / "models"

def _resolve_model(name: str) -> Path | None:
    """Find a model file by name, preferring the right format for the platform.

    On ARM/RK platforms RKNN is preferred; on x86/dev PC ONNX is preferred.
    """
    search_bases = [
        Path("/data/openpilot/models"),
        _REPO_MODELS,
        Path(__file__).parent / "models",
    ]
    # Platform-aware extension order (get_os_version() is None on PC)
    from openpilot.system.hardware import HARDWARE
    os_version = HARDWARE.get_os_version() or ""
    is_arm = "aarch64" in os_version or "arm" in os_version
    exts = (".rknn", ".onnx") if is_arm else (".onnx", ".rknn")

    for base in search_bases:
        for subdir, ext in (("rknn", ".rknn"), ("onnx", ".onnx"), ("", exts[0]), ("", exts[1])):
            p = (base / subdir / f"{name}{ext}") if subdir else (base / f"{name}{ext}")
            if p.exists():
                return p
    return None

VISION_MODEL_PATH = _resolve_model("driving_vision") or Path("/data/openpilot/models/rknn/driving_vision.rknn")
POLICY_MODEL_PATH = _resolve_model("driving_policy") or Path("/data/openpilot/models/rknn/driving_policy.rknn")

# Metadata paths — prefer repo-local onnx copy, then modeld/models/
def _resolve_metadata(name: str) -> Path:
    for candidate in [
        _REPO_MODELS / "onnx" / f"{name}_metadata.pkl",
        Path(__file__).parent / "models" / f"{name}_metadata.pkl",
    ]:
        if candidate.exists():
            return candidate
    return Path(__file__).parent / "models" / f"{name}_metadata.pkl"

VISION_METADATA_PATH = _resolve_metadata("driving_vision")
POLICY_METADATA_PATH = _resolve_metadata("driving_policy")

LAT_SMOOTH_SECONDS = 0.1
LONG_SMOOTH_SECONDS = 0.3
MIN_LAT_CONTROL_SPEED = 0.3


@dataclass
class VisionModelConfig:
    """Vision model configuration from metadata."""
    input_shapes: dict[str, tuple[int, ...]]
    output_shapes: dict[str, tuple[int, ...]]
    output_slices: dict[str, slice]


class FrameMeta:
    """Frame metadata from VisionIPC."""
    frame_id: int = 0
    timestamp_sof: int = 0
    timestamp_eof: int = 0

    def __init__(self, vipc=None):
        if vipc is not None:
            self.frame_id, self.timestamp_sof, self.timestamp_eof = \
                vipc.frame_id, vipc.timestamp_sof, vipc.timestamp_eof


class ModelState:
    """
    Model state using centralized InferenceD HAL.

    All NPU access goes through InferenceClient.
    """

    def __init__(self, context: CLContext, client: InferenceClient):
        self.client = client

        # Get best available inference backend (NPU on ARM, ONNX on x86 dev PC)
        self.npu = client.inference_backend()

        # Load metadata
        self._load_metadata()

        # Load models through HAL
        self._load_models()

        # Initialize frames (for preprocessing)
        self.frames = {
            name: DrivingModelFrame(context, ModelConstants.TEMPORAL_SKIP)
            for name in self.vision_input_names
        }

        # State buffers
        self.prev_desire = np.zeros(ModelConstants.DESIRE_LEN, dtype=np.float32)
        self.full_features_buffer = np.zeros(
            (1, ModelConstants.FULL_HISTORY_BUFFER_LEN, ModelConstants.FEATURE_LEN),
            dtype=np.float32
        )
        self.full_desire = np.zeros(
            (1, ModelConstants.FULL_HISTORY_BUFFER_LEN, ModelConstants.DESIRE_LEN),
            dtype=np.float32
        )
        self.temporal_idxs = slice(
            -1 - (ModelConstants.TEMPORAL_SKIP * (ModelConstants.INPUT_HISTORY_BUFFER_LEN - 1)),
            None,
            ModelConstants.TEMPORAL_SKIP
        )

        # Policy inputs
        self.numpy_inputs = {
            'desire': np.zeros(
                (1, ModelConstants.INPUT_HISTORY_BUFFER_LEN, ModelConstants.DESIRE_LEN),
                dtype=np.float32
            ),
            'traffic_convention': np.zeros((1, ModelConstants.TRAFFIC_CONVENTION_LEN), dtype=np.float32),
            'features_buffer': np.zeros(
                (1, ModelConstants.INPUT_HISTORY_BUFFER_LEN, ModelConstants.FEATURE_LEN),
                dtype=np.float32
            ),
        }

        # Output buffers
        self.vision_output = np.zeros(self.vision_output_size, dtype=np.float32)
        self.policy_output = np.zeros(self.policy_output_size, dtype=np.float32)

        # Parser for output decoding
        self.parser = Parser()

        cloudlog.info("ModelState initialized via InferenceD HAL")

    def _load_metadata(self):
        """Load model metadata (shapes, slices)."""
        # Vision metadata
        if VISION_METADATA_PATH.exists():
            with open(VISION_METADATA_PATH, 'rb') as f:
                vision_metadata = pickle.load(f)
                self.vision_input_shapes = vision_metadata['input_shapes']
                self.vision_input_names = list(self.vision_input_shapes.keys())
                self.vision_output_slices = vision_metadata['output_slices']
                self.vision_output_size = vision_metadata['output_shapes']['outputs'][1]
        else:
            cloudlog.warning("Vision metadata not found — using defaults")
            self.vision_input_shapes = {
                'input_imgs': (1, 12, 128, 256),
                'big_input_imgs': (1, 12, 128, 256),
            }
            self.vision_input_names = list(self.vision_input_shapes.keys())
            self.vision_output_slices = {}
            self.vision_output_size = 6116

        # Policy metadata
        if POLICY_METADATA_PATH.exists():
            with open(POLICY_METADATA_PATH, 'rb') as f:
                policy_metadata = pickle.load(f)
                self.policy_input_shapes = policy_metadata['input_shapes']
                self.policy_output_slices = policy_metadata['output_slices']
                self.policy_output_size = policy_metadata['output_shapes']['outputs'][1]
        else:
            cloudlog.warning("Policy metadata not found — using defaults")
            self.policy_input_shapes = {
                'desire': (1, 100, 8),
                'traffic_convention': (1, 2),
                'features_buffer': (1, 100, 128),
            }
            self.policy_output_slices = {}
            self.policy_output_size = 176

    def _load_models(self):
        """Load models through HAL."""
        # Check model files exist
        if not VISION_MODEL_PATH.exists():
            raise FileNotFoundError(f"Vision model not found: {VISION_MODEL_PATH}")
        if not POLICY_MODEL_PATH.exists():
            raise FileNotFoundError(f"Policy model not found: {POLICY_MODEL_PATH}")

        cloudlog.info(f"Loading vision model: {VISION_MODEL_PATH}")
        # Load via HAL
        vision_config = ModelConfig(
            name='driving_vision',
            path=str(VISION_MODEL_PATH),
            input_shapes=self.vision_input_shapes,
            output_shapes={'outputs': (1, self.vision_output_size)},
            npu_cores=0
        )
        if not self.npu.load_model(vision_config):
            raise RuntimeError("Failed to load vision model")

        cloudlog.info(f"Loading policy model: {POLICY_MODEL_PATH}")
        policy_config = ModelConfig(
            name='driving_policy',
            path=str(POLICY_MODEL_PATH),
            input_shapes=self.policy_input_shapes,
            output_shapes={'outputs': (1, self.policy_output_size)},
            npu_cores=0
        )
        if not self.npu.load_model(policy_config):
            raise RuntimeError("Failed to load policy model")

        cloudlog.info("Models loaded via HAL")

    def slice_outputs(self, model_outputs: np.ndarray, output_slices: dict[str, slice]) -> dict[str, np.ndarray]:
        """Parse model outputs using slices."""
        return {k: model_outputs[np.newaxis, v] for k, v in output_slices.items()}

    def run(self, bufs: dict[str, VisionBuf], transforms: dict[str, np.ndarray],
            inputs: dict[str, np.ndarray], prepare_only: bool) -> dict[str, np.ndarray] | None:
        """
        Run inference through HAL.
        """
        # Handle desire pulse
        inputs['desire'][0] = 0
        new_desire = np.where(inputs['desire'] - self.prev_desire > 0.99, inputs['desire'], 0)
        self.prev_desire[:] = inputs['desire']

        # Update desire buffer
        self.full_desire[0, :-1] = self.full_desire[0, 1:]
        self.full_desire[0, -1] = new_desire
        self.numpy_inputs['desire'][:] = self.full_desire.reshape(
            (1, ModelConstants.INPUT_HISTORY_BUFFER_LEN, ModelConstants.TEMPORAL_SKIP, -1)
        ).max(axis=2)

        self.numpy_inputs['traffic_convention'][:] = inputs['traffic_convention']

        # Prepare image inputs
        imgs_cl = {
            name: self.frames[name].prepare(bufs[name], transforms[name].flatten())
            for name in self.vision_input_names
        }

        # Convert CL buffers to numpy for NPU
        vision_inputs = {}
        for name in self.vision_input_names:
            frame_input = self.frames[name].buffer_from_cl(imgs_cl[name])
            expected_shape = self.vision_input_shapes[name]
            vision_inputs[name] = frame_input.reshape(expected_shape).astype(np.uint8)

        if prepare_only:
            return None

        # Run vision model through HAL
        t0 = time.perf_counter()
        vision_result = self.npu.infer('driving_vision', vision_inputs)
        vision_time = (time.perf_counter() - t0) * 1000

        if not vision_result.success:
            cloudlog.error(f"Vision inference failed: {vision_result.error_message}")
            return None

        self.vision_output = vision_result.outputs.get('outputs', np.zeros(self.vision_output_size))
        vision_outputs_dict = self.parser.parse_vision_outputs(
            self.slice_outputs(self.vision_output, self.vision_output_slices)
        )

        # Update features buffer
        self.full_features_buffer[0, :-1] = self.full_features_buffer[0, 1:]
        self.full_features_buffer[0, -1] = vision_outputs_dict['hidden_state'][0, :]
        self.numpy_inputs['features_buffer'][:] = self.full_features_buffer[0, self.temporal_idxs]

        # Prepare policy inputs
        # ONNX policy model names the desire input 'desire_pulse'; RKNN uses 'desire'
        policy_inputs = {
            'traffic_convention': self.numpy_inputs['traffic_convention'],
            'features_buffer': self.numpy_inputs['features_buffer'],
        }
        if self.npu.backend_type.name == 'ONNX':
            policy_inputs['desire_pulse'] = self.numpy_inputs['desire']
        else:
            policy_inputs['desire'] = self.numpy_inputs['desire']

        # Run policy model through HAL
        t0 = time.perf_counter()
        policy_result = self.npu.infer('driving_policy', policy_inputs)
        policy_time = (time.perf_counter() - t0) * 1000

        if not policy_result.success:
            cloudlog.error(f"Policy inference failed: {policy_result.error_message}")
            return None

        self.policy_output = policy_result.outputs.get('outputs', np.zeros(self.policy_output_size))
        policy_outputs_dict = self.parser.parse_policy_outputs(
            self.slice_outputs(self.policy_output, self.policy_output_slices)
        )

        # Log performance
        cloudlog.debug(f"Inference times: vision={vision_time:.1f}ms, policy={policy_time:.1f}ms")

        # Combine outputs
        combined_outputs = {**vision_outputs_dict, **policy_outputs_dict}

        if SEND_RAW_PRED:
            combined_outputs['raw_pred'] = np.concatenate([
                self.vision_output.copy(),
                self.policy_output.copy()
            ])

        return combined_outputs

    def release(self):
        """Release resources."""
        self.client.release()


def get_action_from_model(model_output: dict[str, np.ndarray], prev_action: log.ModelDataV2.Action,
                          lat_action_t: float, long_action_t: float, v_ego: float) -> log.ModelDataV2.Action:
    """Extract action from model output."""
    plan = model_output['plan'][0]
    desired_accel, should_stop = get_accel_from_plan(
        plan[:, Plan.VELOCITY][:, 0],
        plan[:, Plan.ACCELERATION][:, 0],
        ModelConstants.T_IDXS,
        action_t=long_action_t
    )
    desired_accel = smooth_value(desired_accel, prev_action.desiredAcceleration, LONG_SMOOTH_SECONDS)

    desired_curvature = get_curvature_from_plan(
        plan[:, Plan.T_FROM_CURRENT_EULER][:, 2],
        plan[:, Plan.ORIENTATION_RATE][:, 2],
        ModelConstants.T_IDXS,
        v_ego,
        lat_action_t
    )
    if v_ego > MIN_LAT_CONTROL_SPEED:
        desired_curvature = smooth_value(desired_curvature, prev_action.desiredCurvature, LAT_SMOOTH_SECONDS)
    else:
        desired_curvature = prev_action.desiredCurvature

    return log.ModelDataV2.Action(
        desiredCurvature=float(desired_curvature),
        desiredAcceleration=float(desired_accel),
        shouldStop=bool(should_stop)
    )


def main(demo=False) -> int:
    """Main entry point."""
    cloudlog.warning("modeld starting")

    # Create InferenceD client - ONLY way to access NPU
    client = InferenceClient("modeld")

    # Check inference backend available (NPU on ARM, ONNX on x86 dev PC)
    try:
        backend = client.inference_backend()
        cloudlog.info(f"Inference backend acquired via HAL: {backend.backend_type.name}")
    except RuntimeError as e:
        cloudlog.error(f"No inference backend available: {e}")
        return 1

    # Check models exist
    if not VISION_MODEL_PATH.exists():
        cloudlog.error(f"Vision model not found: {VISION_MODEL_PATH}")
        return 1
    if not POLICY_MODEL_PATH.exists():
        cloudlog.error(f"Policy model not found: {POLICY_MODEL_PATH}")
        return 1

    # Setup process
    set_daemon_affinity("modeld")
    config_realtime_process(DT_MDL, 54)

    # Initialize CL context — mock on dev PC (no real OpenCL); used for frame preprocessing
    st = time.monotonic()
    cl_context = CLContext()
    use_cl = cl_context.context is not None  # True only when real OpenCL context was created
    cloudlog.warning(f"CL context: {'GPU' if use_cl else 'dev-PC numpy mock'}; loading models via HAL")

    # Initialize model state with HAL client
    try:
        model = ModelState(cl_context, client)
    except Exception as e:
        cloudlog.error(f"Failed to initialize model: {e}")
        return 1

    cloudlog.warning(f"Models loaded in {time.monotonic() - st:.1f}s, modeld starting")

    # Setup VisionIPC
    while True:
        available_streams = VisionIpcClient.available_streams("v4l2d", block=False)
        if available_streams:
            use_extra_client = VisionStreamType.VISION_STREAM_WIDE_ROAD in available_streams and \
                              VisionStreamType.VISION_STREAM_ROAD in available_streams
            main_wide_camera = VisionStreamType.VISION_STREAM_ROAD not in available_streams
            break
        time.sleep(0.1)

    vipc_client_main_stream = VisionStreamType.VISION_STREAM_WIDE_ROAD if main_wide_camera else VisionStreamType.VISION_STREAM_ROAD
    # exo msgq fork signature: (name, stream, conflate) — no CL context arg
    # (CL preprocessing is handled by inferenced/RGA; cl_context only feeds
    # the python DrivingModelFrame implementation)
    vipc_client_main = VisionIpcClient("v4l2d", vipc_client_main_stream, True)
    vipc_client_extra = VisionIpcClient("v4l2d", VisionStreamType.VISION_STREAM_WIDE_ROAD, False)

    while not vipc_client_main.connect(False):
        time.sleep(0.1)
    while use_extra_client and not vipc_client_extra.connect(False):
        time.sleep(0.1)

    cloudlog.warning(f"Vision stream ready, main_wide_camera={main_wide_camera}")

    # Setup messaging
    pm = messaging.PubMaster(["modelV2", "drivingModelData", "cameraOdometry"])
    sm = messaging.SubMaster(["deviceState", "carState", "roadCameraState", "liveCalibration", "carControl", "liveDelay", "blindSpotAlert"])

    publish_state = PublishState()
    params = Params()

    # Filters
    frame_dropped_filter = FirstOrderFilter(0., 10., 1. / ModelConstants.MODEL_FREQ)
    frame_id = 0
    last_vipc_frame_id = 0
    run_count = 0

    # Calibration
    model_transform_main = np.zeros((3, 3), dtype=np.float32)
    model_transform_extra = np.zeros((3, 3), dtype=np.float32)
    live_calib_seen = False

    # Desire helper
    DH = DesireHelper()

    # Get car params
    if demo:
        # EOP: opendbc removed; use minimal CarParams for demo mode
        CP = car.CarParams.new_message()
        CP.brand = "tesla"
    else:
        CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
    cloudlog.info("Model got CarParams: %s", CP.brand)

    is_rhd = params.get_bool("EOPDeviceIsRhd")
    cloudlog.info("Model RHD: %s", is_rhd)

    long_delay = CP.longitudinalActuatorDelay + LONG_SMOOTH_SECONDS
    prev_action = log.ModelDataV2.Action()

    cloudlog.warning("modeld main loop starting")

    try:
        while True:
            # Receive frames
            buf_main = None
            buf_extra = None
            meta_main = FrameMeta()
            meta_extra = FrameMeta()

            while meta_main.timestamp_sof < meta_extra.timestamp_sof + 25000000:
                buf_main = vipc_client_main.recv()
                meta_main = FrameMeta(vipc_client_main)
                if buf_main is None:
                    break

            if buf_main is None:
                continue

            if use_extra_client:
                while True:
                    buf_extra = vipc_client_extra.recv()
                    meta_extra = FrameMeta(vipc_client_extra)
                    if buf_extra is None or meta_main.timestamp_sof < meta_extra.timestamp_sof + 25000000:
                        break
                if buf_extra is None:
                    continue
            else:
                buf_extra = buf_main
                meta_extra = meta_main

            # Update submaster
            sm.update(0)
            desire = DH.desire
            frame_id = sm["roadCameraState"].frameId
            v_ego = max(sm["carState"].vEgo, 0.)
            lat_delay = sm["liveDelay"].lateralDelay + LAT_SMOOTH_SECONDS

            # Update calibration
            if sm.updated["liveCalibration"] and sm.seen['roadCameraState'] and sm.seen['deviceState']:
                device_from_calib_euler = np.array(sm["liveCalibration"].rpyCalib, dtype=np.float32)
                dc = DEVICE_CAMERAS[(str(sm['deviceState'].deviceType), str(sm['roadCameraState'].sensor))]
                model_transform_main = get_warp_matrix(
                    device_from_calib_euler,
                    dc.ecam.intrinsics if main_wide_camera else dc.fcam.intrinsics,
                    False
                ).astype(np.float32)
                model_transform_extra = get_warp_matrix(
                    device_from_calib_euler,
                    dc.ecam.intrinsics,
                    True
                ).astype(np.float32)
                live_calib_seen = True

            # Traffic convention
            traffic_convention = np.zeros(2)
            traffic_convention[int(is_rhd)] = 1

            # Desire vector
            vec_desire = np.zeros(ModelConstants.DESIRE_LEN, dtype=np.float32)
            if desire >= 0 and desire < ModelConstants.DESIRE_LEN:
                vec_desire[desire] = 1

            # Track dropped frames
            vipc_dropped_frames = max(0, meta_main.frame_id - last_vipc_frame_id - 1)
            frames_dropped = frame_dropped_filter.update(min(vipc_dropped_frames, 10))
            if run_count < 10:
                frame_dropped_filter.x = 0.
                frames_dropped = 0.
            run_count += 1

            frame_drop_ratio = frames_dropped / (1 + frames_dropped)
            prepare_only = vipc_dropped_frames > 0

            # Prepare inputs
            bufs = {name: buf_extra if 'big' in name else buf_main for name in model.vision_input_names}
            transforms = {
                name: model_transform_extra if 'big' in name else model_transform_main
                for name in model.vision_input_names
            }
            inputs = {
                'desire': vec_desire,
                'traffic_convention': traffic_convention,
            }

            # Run inference
            mt1 = time.perf_counter()
            model_output = model.run(bufs, transforms, inputs, prepare_only)
            mt2 = time.perf_counter()
            model_execution_time = mt2 - mt1

            # Publish results
            if model_output is not None:
                modelv2_send = messaging.new_message('modelV2')
                drivingdata_send = messaging.new_message('drivingModelData')
                posenet_send = messaging.new_message('cameraOdometry')

                action = get_action_from_model(
                    model_output, prev_action,
                    lat_delay + DT_MDL, long_delay + DT_MDL, v_ego
                )
                prev_action = action

                fill_model_msg(
                    drivingdata_send, modelv2_send, model_output, action,
                    publish_state, meta_main.frame_id, meta_extra.frame_id, frame_id,
                    frame_drop_ratio, meta_main.timestamp_eof, model_execution_time, live_calib_seen
                )

                desire_state = modelv2_send.modelV2.meta.desireState
                l_lane_change_prob = desire_state[log.Desire.laneChangeLeft]
                r_lane_change_prob = desire_state[log.Desire.laneChangeRight]
                lane_change_prob = l_lane_change_prob + r_lane_change_prob
                bsa = sm['blindSpotAlert'] if sm.valid.get('blindSpotAlert') else None
                DH.update(sm['carState'], sm['carControl'].latActive, lane_change_prob, blind_spot_alert=bsa)
                modelv2_send.modelV2.meta.laneChangeState = DH.lane_change_state
                modelv2_send.modelV2.meta.laneChangeDirection = DH.lane_change_direction
                drivingdata_send.drivingModelData.meta.laneChangeState = DH.lane_change_state
                drivingdata_send.drivingModelData.meta.laneChangeDirection = DH.lane_change_direction

                fill_pose_msg(posenet_send, model_output, meta_main.frame_id, vipc_dropped_frames, meta_main.timestamp_eof, live_calib_seen)
                pm.send('modelV2', modelv2_send)
                pm.send('drivingModelData', drivingdata_send)
                pm.send('cameraOdometry', posenet_send)

            last_vipc_frame_id = meta_main.frame_id

    except KeyboardInterrupt:
        cloudlog.warning("Interrupted")
    finally:
        model.release()
        cloudlog.warning("modeld exiting")

    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true', help='Demo mode')
    args = parser.parse_args()

    exit(main(demo=args.demo))
