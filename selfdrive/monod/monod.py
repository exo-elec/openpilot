#!/usr/bin/env python3
"""
monod.py - Front mono object detection (road camera, + telephoto on 02M)

Everything here runs on the SoC's RKNN NPU (last core; 01M core 2, 02M core 1):
  * road (8mm): YOLOv8 multi-object detection -- every road user in view
    (cars, trucks, buses, bikes, pedestrians) with class and road-frame
    position, where openpilot's modelV2 carries only three lead hypotheses.
  * tele (16mm, 02M mono_tele with EOPTeleEnabled): the same detector at long
    range, 20 Hz.
  * traffic lights (road camera only): lamp colour by HSV, published in
    monoDetections for gridd to hand to TLSC through stereoObjects.

Object detection stays on RKNN because RKNN is on the die: it cannot be
unfitted or drop off a bus. Semantic segmentation of the front cameras (and
every other camera) runs on the PCIe card, Hailo-8 or DX-M1M, in segd, which
also publishes monoSegments; monod no longer segments anything.

FUSION (sources on monoDetections.cameraSource):
- road : 40° FOV, 0-100m
- tele : ~20° FOV, 20-200m; confirms a road track or adds a far one
"""
import logging
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum

import numpy as np

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.modeld.runners.rknn_platform import get_platform_npu_config
from openpilot.selfdrive.monod.traffic_light_classifier import classify as classify_traffic_light
from openpilot.selfdrive.sided.yolo_detector import ROAD_COCO_CLASSES, TRAFFIC_LIGHT, YoloDetector
from openpilot.system.hardware import HARDWARE

# VisionIPC integration (optional - falls back to zero frames if unavailable)
try:
    from msgq.visionipc import VisionIpcClient, VisionStreamType
    HAS_VISIONIPC = True
except ImportError:
    HAS_VISIONIPC = False

RATE = 20  # 20 Hz


@dataclass
class FusedDetection:
    """Fused detection from multiple camera sources."""
    track_id: int
    class_name: str
    confidence: float
    # Position in road frame (car coordinates)
    x: float  # forward (meters)
    y: float  # left (meters)
    z: float  # up (meters)
    # Source cameras that detected this object
    sources: list[str]
    # Source-specific data
    road_detection: dict | None = None
    # Velocity
    vx: float = 0.0
    vy: float = 0.0


class CameraLens(Enum):
    """Front cameras (last field: equidistant fisheye projection)."""
    ROAD_8MM = ("road", 8.0, 40.0, 0.0, 100.0, False)
    WIDE_1_7MM = ("wide_road", 1.7, 150.0, 0.0, 30.0, True)
    # 02M mono_tele: 16mm on OX03C10 (3.0um pixels) at 1920 px wide gives
    # f = 5333 px, HFOV = 2*atan(960/5333) = 20.4 deg
    # (hal.platform.rk3576_camera_geometry).
    TELE_16MM = ("tele_road", 16.0, 20.4, 20.0, 200.0, False)

    def __init__(self, camera_name, focal_mm, fov_deg, min_range_m, max_range_m, equidistant):
        self.camera_name = camera_name
        self.focal_mm = focal_mm
        self.fov_deg = fov_deg
        self.min_range_m = min_range_m
        self.max_range_m = max_range_m
        self.equidistant = equidistant


def objects_to_road_frame(objects: list, frame_hw: tuple[int, int], lens: CameraLens) -> list[dict]:
    """Image-space detections → road-frame dicts (x forward, y left).

    Pinhole for the narrow lenses (road 8mm, tele 16mm); equidistant for the
    1.7mm wide, whose 150° field a pinhole cannot describe (bearing linear in
    pixel column). The focal length follows from the lens's horizontal FOV at
    this frame width, range from the class height prior over the apparent box
    height. Detections outside the lens's working range are dropped.
    """
    h, w = frame_hw
    if h <= 0 or w <= 0:
        return []
    half_fov = np.radians(lens.fov_deg) / 2.0
    focal_px = (w / 2.0) / (half_fov if lens.equidistant else np.tan(half_fov))
    out = []
    for obj in objects:
        x1, y1, x2, y2 = obj.bbox_2d
        box_h = y2 - y1
        if box_h <= 0:
            continue
        # A signal head can hang horizontally: range it by its long side
        extent = max(box_h, x2 - x1) if obj.label == TRAFFIC_LIGHT else box_h
        rng = obj.height_m * focal_px / extent
        if not lens.min_range_m <= rng <= lens.max_range_m:
            continue
        offset_px = (x1 + x2) / 2.0 - w / 2.0
        bearing = offset_px / focal_px if lens.equidistant else np.arctan(offset_px / focal_px)  # +right
        out.append({
            'class': obj.label,
            'confidence': obj.confidence,
            'distance_m': float(rng * np.cos(bearing)),
            'lateral_m': float(-rng * np.sin(bearing)),  # +left
            'bbox': obj.bbox_2d,
        })
    return out


def split_traffic_lights(frame: np.ndarray, dets: list[dict]) -> tuple[list[dict], list[dict]]:
    """Road users and traffic lights, the lights with their lamp colour.

    Traffic lights are not road users: they skip fusion and tracking and are
    published as they are seen, for gridd to pass to TLSC through
    stereoObjects. Colour: 0 unknown, 1 red, 2 yellow, 3 green.
    """
    users, lights = [], []
    for det in dets:
        if det['class'] != TRAFFIC_LIGHT:
            users.append(det)
            continue
        state, conf = classify_traffic_light(frame, det['bbox'])
        lights.append({**det, 'tl_state': state, 'tl_conf': conf})
    return users, lights


def board_has_tele() -> bool:
    """True on a board with a telephoto camera (02M), from the running board."""
    try:
        return bool(HARDWARE.get_camera_array_config().get('has_tele_road', False))
    except Exception:
        return False


class RKNNMonoProcessor:
    """Road (and 02M telephoto) YOLOv8 on the RKNN NPU."""

    FAULT_THRESHOLD = YoloDetector.FAULT_THRESHOLD

    def __init__(self, core_id: int | None = None, has_tele: bool = False, detector_factory=YoloDetector):
        npu_config = get_platform_npu_config()
        if core_id is None or not npu_config.is_core_available(core_id):
            # Last core: 01M core 2, 02M core 1. It used to be picked with
            # is_rk3588 and passed to RKNNLite as the index, but core_mask is a
            # bit mask, so "2" meant core 1 on 01M. YoloDetector passes 1 << core.
            core_id = max(0, npu_config.core_count - 1)
        self.core_id = core_id
        self._road = detector_factory("monod", core_id=core_id, name="road", classes=ROAD_COCO_CLASSES)
        self._tele = detector_factory("monod", core_id=core_id, name="tele") if has_tele else None

    @property
    def is_available(self) -> bool:
        return self._road.is_available

    @property
    def is_fault(self) -> bool:
        return self._road.is_fault

    @property
    def fault_reason(self) -> str:
        if not self._road.is_available:
            return "npu_unavailable"
        return "npu_consecutive_failures" if self._road.is_fault else ""

    @property
    def consecutive_failures(self) -> int:
        return self._road.consecutive_failures

    @property
    def tele_available(self) -> bool:
        return self._tele is not None and self._tele.is_available

    def infer_yolo_road(self, frame: np.ndarray) -> list[dict]:
        """Every road user on the road camera, in the road frame."""
        return objects_to_road_frame(self._road.detect(frame), frame.shape[:2], CameraLens.ROAD_8MM)

    def infer_yolo_tele(self, frame: np.ndarray | None) -> list[dict]:
        """Long-range objects on the 02M telephoto, in the road frame."""
        if frame is None or not self.tele_available:
            return []
        return objects_to_road_frame(self._tele.detect(frame), frame.shape[:2], CameraLens.TELE_16MM)


class MultiCameraFusion:
    """Fuses road-camera detections with the telephoto's (02M)."""

    def __init__(self):
        self._next_track_id = 1000
        self._active_tracks: dict[int, FusedDetection] = {}
        self._track_history: deque = deque(maxlen=100)

    def fuse_detections(self, road_dets: list[dict],
                        tele_dets: list[dict] | None = None) -> list[FusedDetection]:
        """Road tracks first; telephoto detections confirm them or add far ones."""
        fused: list[FusedDetection] = []

        for road_det in road_dets:
            x = road_det.get('distance_m', 50.0)
            y = road_det.get('lateral_m', 0.0)
            class_name = road_det.get('class', 'unknown')
            existing = self._find_matching_track(x, y, class_name)
            if existing:
                # Keep the track id; a matched track used to drop out of the
                # output until it was re-created.
                existing.road_detection = road_det
                existing.x, existing.y = x, y
                existing.sources = ['road']
                existing.confidence = road_det.get('confidence', existing.confidence)
                if all(t is not existing for t in fused):
                    fused.append(existing)
            else:
                fused.append(FusedDetection(
                    track_id=self._next_track_id,
                    class_name=class_name,
                    confidence=road_det.get('confidence', 0.5),
                    x=x, y=y, z=0.0,
                    sources=['road'],
                    road_detection=road_det,
                ))
                self._next_track_id += 1

        self._fuse_secondary('tele', tele_dets or [], fused)

        self._active_tracks = {t.track_id: t for t in fused}
        self._track_history.extend(fused)
        return fused

    def _fuse_secondary(self, source: str, dets: list[dict], fused: list[FusedDetection]) -> None:
        """A second camera: confirm a track fused this frame, else keep/start its own."""
        for det in dets:
            x = det.get('distance_m', 0.0)
            y = det.get('lateral_m', 0.0)
            class_name = det.get('class', 'unknown')
            match = self._find_matching_track(x, y, class_name, candidates=fused)
            if match is not None:
                if source not in match.sources:
                    match.sources.append(source)
                # Boost confidence with a second, independent detection
                match.confidence = min(1.0, match.confidence + 0.1)
                continue
            previous = self._find_matching_track(x, y, class_name)
            if previous is not None and all(t is not previous for t in fused):
                track = previous
                track.x, track.y = x, y
                track.sources = [source]
                track.confidence = det.get('confidence', 0.5)
                track.road_detection = None
            else:
                track = FusedDetection(
                    track_id=self._next_track_id,
                    class_name=class_name,
                    confidence=det.get('confidence', 0.5),
                    x=x, y=y, z=0.0,
                    sources=[source],
                )
                self._next_track_id += 1
            fused.append(track)

    def _find_matching_track(self, x: float, y: float, class_name: str,
                             candidates: list[FusedDetection] | None = None) -> FusedDetection | None:
        """Find a track matching the given position and class.

        Searches the previous frame's tracks unless candidates is given.
        """
        tracks = self._active_tracks.values() if candidates is None else candidates
        for track in tracks:
            if track.class_name != class_name:
                continue
            if np.hypot(track.x - x, track.y - y) < 5.0:  # 5 meter threshold
                return track
        return None

    def get_active_tracks(self) -> list[FusedDetection]:
        return list(self._active_tracks.values())


class MonoD:
    """Front mono object detection daemon (RKNN)."""

    def __init__(self) -> None:
        set_daemon_affinity("monod")

        # monoSegments is segd's (card segmentation for every camera)
        self.pm = messaging.PubMaster(['monoDetections', 'monoStatus'])

        self.params = Params()
        self.enabled = self.params.get_bool("EOPMonoDEnabled")

        # Telephoto: only on a board that has one (02M's mono_tele) and has it on.
        self.has_tele = board_has_tele() and self.params.get_bool("EOPTeleEnabled")

        # (SubMaster: this used to call messaging.SubManager, which does not
        # exist, so monod died in __init__.)
        services = ['roadCameraState', 'livePose', 'liveCalibration']
        if self.has_tele:
            services.append('teleRoadCameraState')
        self.sm = messaging.SubMaster(services)

        self.rk = Ratekeeper(RATE, print_delay_threshold=None)
        self.fusion = MultiCameraFusion()
        self.rknn_processor = RKNNMonoProcessor(has_tele=self.has_tele)

        self.frame_id = 0
        self._vipc_road = None
        self._vipc_tele = None
        self._init_visionipc()

        npu_config = get_platform_npu_config()
        cloudlog.info(f"MonoD initialized: enabled={self.enabled}, rknn={self.rknn_processor.is_available}, " +
                      f"tele={self.rknn_processor.tele_available}, " +
                      f"{npu_config.platform.value} NPU core {self.rknn_processor.core_id}")

    def _connect(self, stream_name: str):
        stream = getattr(VisionStreamType, stream_name, None) if HAS_VISIONIPC else None
        if stream is None:
            return None
        try:
            client = VisionIpcClient("v4l2d", stream, False)
            if client.connect(False):
                return client
            cloudlog.warning(f"MonoD: VisionIPC {stream_name} not available")
        except Exception as e:
            cloudlog.warning(f"MonoD: VisionIPC {stream_name} init failed: {e}")
        return None

    def _init_visionipc(self) -> None:
        self._vipc_road = self._connect('VISION_STREAM_ROAD')
        if self.has_tele:
            self._vipc_tele = self._connect('VISION_STREAM_TELE_ROAD')

    def _get_frame(self, vipc_client, shape: tuple[int, int, int]) -> np.ndarray | None:
        """Latest BGR frame from VisionIPC, or None."""
        if vipc_client is None:
            return None
        try:
            buf = vipc_client.recv()
            if buf is None:
                return None
            h, w = shape[:2]
            return np.frombuffer(buf.data, dtype=np.uint8).reshape((h, w, 3))
        except Exception as e:
            cloudlog.debug(f"MonoD: VisionIPC frame retrieval failed: {e}")
            return None

    def _publish(self, fused_tracks: list[FusedDetection], ts: int, lights: list[dict] | None = None) -> None:
        lights = lights or []

        msg = messaging.new_message('monoDetections')
        msg.monoDetections.frameId = self.frame_id
        msg.monoDetections.timestamp = ts
        msg.monoDetections.numTracks = len(fused_tracks)
        if fused_tracks or lights:
            items = msg.monoDetections.init('detections', len(fused_tracks) + len(lights))
            for i, track in enumerate(fused_tracks):
                items[i].trackId = track.track_id
                items[i].className = track.class_name
                items[i].confidence = track.confidence
                items[i].x = track.x
                items[i].y = track.y
                items[i].cameraSource = '+'.join(track.sources)
            for i, light in enumerate(lights, start=len(fused_tracks)):
                items[i].trackId = 0  # untracked
                items[i].className = TRAFFIC_LIGHT
                items[i].confidence = light['confidence']
                items[i].x = light['distance_m']
                items[i].y = light['lateral_m']
                items[i].cameraSource = 'road'
                items[i].trafficLightState = light['tl_state']
                items[i].trafficLightConfidence = light['tl_conf']
        self.pm.send('monoDetections', msg)

        status_msg = messaging.new_message('monoStatus')
        ss = status_msg.monoStatus
        ss.enabled = self.enabled
        ss.hailoActive = False  # monod runs no card work; segd reports the card
        ss.hasTeleRoad = self.has_tele
        ss.numTracks = len(fused_tracks)
        ss.fault = self.rknn_processor.is_fault
        ss.faultReason = self.rknn_processor.fault_reason
        ss.consecutiveFailures = self.rknn_processor.consecutive_failures
        self.pm.send('monoStatus', status_msg)

    def run(self) -> None:
        if not self.enabled:
            cloudlog.info("MonoD disabled - exiting")
            return

        cloudlog.info("MonoD running (RKNN road YOLO" + (" + telephoto)" if self.has_tele else ")"))
        while True:
            self.sm.update(0)

            road_dets: list[dict] = []
            lights: list[dict] = []
            if self.sm.updated['roadCameraState']:
                road_frame = self._get_frame(self._vipc_road, (1080, 1920, 3))
                if road_frame is not None:
                    road_dets, lights = split_traffic_lights(road_frame, self.rknn_processor.infer_yolo_road(road_frame))

            tele_dets: list[dict] = []
            if self.has_tele and self.sm.updated['teleRoadCameraState']:
                tele_dets = self.rknn_processor.infer_yolo_tele(self._get_frame(self._vipc_tele, (1080, 1920, 3)))

            fused_tracks = self.fusion.fuse_detections(road_dets, tele_dets)
            self._publish(fused_tracks, int(time.monotonic() * 1e9), lights)

            self.frame_id += 1
            self.rk.keep_time()


def main() -> int:
    try:
        logging.basicConfig(level=logging.INFO)
        MonoD().run()
        return 0
    except Exception as e:
        cloudlog.exception(f"MonoD fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
