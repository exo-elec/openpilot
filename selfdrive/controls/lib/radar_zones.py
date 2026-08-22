#!/usr/bin/env python3
"""
RadarZoneMonitor — adjacent-lane and rear-zone threat assessment.

Associates BLE Radar2D tracks from stereoObjects with side/rear YOLO tracks,
then classifies the fused objects into left-side, right-side, and rear zones.
Radar supplies range and Doppler; cameras supply class and visual continuity.

This remains advisory-only. It does not feed longitudinal actuation or AEB.
"""

from dataclasses import dataclass
from enum import Enum

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.radar_corner_geometry import corner_id_from_track_id, is_corner_track_id

# LaneZone integer constants — must match CameraObject.LaneZone in log.capnp
_LZ_UNKNOWN        = 0
_LZ_EGO            = 1
_LZ_ADJ_LEFT       = 2
_LZ_ADJ_RIGHT      = 3
_LZ_FAR_LEFT       = 4
_LZ_FAR_RIGHT      = 5
_LZ_SHOULDER_LEFT  = 6
_LZ_SHOULDER_RIGHT = 7


class ZoneSide(Enum):
    LEFT  = "left"
    RIGHT = "right"
    REAR  = "rear"


class ZoneAlertLevel(Enum):
    OFF     = 0
    CAUTION = 1
    WARNING = 2


class ThreatKind(Enum):
    FCW = "front_collision"
    NEAR_FRONT = "near_front_obstacle"
    RCW = "rear_collision"
    FCTA = "front_cross_traffic"
    RCTA = "rear_cross_traffic"


@dataclass
class ZoneState:
    side: ZoneSide
    alert_level: ZoneAlertLevel
    detected: bool
    distance_m: float
    vRel_ms: float        # negative = approaching
    ttc_s: float
    confidence: float

    @staticmethod
    def empty(side: ZoneSide) -> "ZoneState":
        return ZoneState(side=side, alert_level=ZoneAlertLevel.OFF, detected=False,
                         distance_m=float('inf'), vRel_ms=0.0,
                         ttc_s=float('inf'), confidence=0.0)

    @staticmethod
    def from_object(side: ZoneSide, alert_level: ZoneAlertLevel,
                    d: float, v: float, ttc: float, conf: float) -> "ZoneState":
        return ZoneState(side=side, alert_level=alert_level, detected=True,
                         distance_m=abs(d), vRel_ms=v, ttc_s=ttc, confidence=conf)


@dataclass
class AdvisoryThreat:
    kind: ThreatKind
    alert_level: ZoneAlertLevel
    detected: bool
    side: ZoneSide | None
    distance_m: float
    vRel_ms: float
    ttc_s: float
    confidence: float

    @staticmethod
    def empty(kind: ThreatKind) -> "AdvisoryThreat":
        return AdvisoryThreat(kind, ZoneAlertLevel.OFF, False, None,
                              float('inf'), 0.0, float('inf'), 0.0)


class RadarZoneMonitor:
    """
    Adjacent-lane and rear zone monitor.

    Consumes vehicle-frame stereoObjects and camera tracks. BLE Radar2D and
    YOLO objects are associated here because their complementary attributes
    are needed by the alert state machine, not by the driving controller.
    """

    # Immediate BSD alert zone (mirror icon + chime)
    SIDE_Y_MIN  =  1.5   # inner edge of adjacent lane
    SIDE_Y_MAX  =  5.0   # outer edge
    SIDE_D_MIN  = -15.0  # behind ego
    SIDE_D_MAX  =  8.0   # ahead (radar4d catches overtaking scenario)

    REAR_D_MIN  = -30.0  # RCTA: 30m behind
    REAR_D_MAX  =  -3.0  # not within 3m (bumper)
    REAR_Y_MAX  =  6.0   # lateral width of rear zone

    # LCA gate zone — wider, TTC-based, no alert/chime
    # radar3d provides the long-range coverage that makes this meaningful
    LCA_Y_MIN   =  1.2   # slightly wider than BSD zone
    LCA_Y_MAX   =  6.0
    LCA_D_MIN   = -100.0  # 100m behind — radar3d range at highway speeds
    LCA_D_MAX   =  30.0   # 30m ahead (car partially alongside / cutting in)
    LCA_TTC_S   =  5.0    # block if time-to-collision below this (seconds)

    # Alert thresholds
    FAST_APPROACH_MS   = -8.0   # m/s — triggers WARNING (28.8 km/h faster than ego)
    TTC_WARNING_S      =  3.0   # seconds — triggers WARNING
    MIN_CONFIDENCE     =  0.35

    # Hysteresis
    PERSIST_S          =  0.5   # keep alert briefly after object disappears
    CHIME_COOLDOWN_S   =  3.0

    # Camera/radar object association. Camera BEV range is deliberately a
    # loose gate; accepted matches retain authoritative radar position/speed.
    CAM_TIMEOUT_S      =  1.0
    SIDE_ASSOC_M       =  3.5
    REAR_ASSOC_M       =  5.0

    COLLISION_Y_MAX    =  1.8
    FCW_D_MIN          =  2.0
    FCW_D_MAX          =  30.0
    RCW_D_MIN          = -30.0
    RCW_D_MAX          = -2.0
    CROSS_Y_MIN        =  1.5
    CROSS_TTC_MAX_S    =  5.0
    CROSS_WARNING_S    =  3.0
    CROSS_MIN_VY_MS    =  0.4
    CROSS_MAX_EGO_MS   =  4.2
    NEAR_FRONT_D_MIN   =  0.3
    NEAR_FRONT_D_MAX   =  6.0
    NEAR_FRONT_Y_MAX   =  2.5
    NEAR_FRONT_EGO_MAX_MS = 4.2

    def __init__(self):
        self.left_state  = ZoneState.empty(ZoneSide.LEFT)
        self.right_state = ZoneState.empty(ZoneSide.RIGHT)
        self.rear_state  = ZoneState.empty(ZoneSide.REAR)
        self.fcw_state = AdvisoryThreat.empty(ThreatKind.FCW)
        self.near_front_state = AdvisoryThreat.empty(ThreatKind.NEAR_FRONT)
        self.rcw_state = AdvisoryThreat.empty(ThreatKind.RCW)
        self.fcta_state = AdvisoryThreat.empty(ThreatKind.FCTA)
        self.rcta_state = AdvisoryThreat.empty(ThreatKind.RCTA)

        self._left_seen   = 0.0
        self._right_seen  = 0.0
        self._rear_seen   = 0.0
        self._last_chime  = -999.0

        self.lca_blocked_left  = False
        self.lca_blocked_right = False

        # Camera caches used for object-level association and camera-only
        # degradation when a corner radar is unavailable.
        self._side_dets: list[dict] = []
        self._side_det_t = 0.0
        self._rear_dets: list[dict] = []
        self._rear_det_t = 0.0

        self._n_updates = 0
        self.last_fused_objects: list[dict] = []
        self._motion_tracks: dict[tuple[str, int], tuple[float, float, float, float, float, int]] = {}

    # ------------------------------------------------------------------
    # Camera fallback input (populated by controlsd before update())
    # ------------------------------------------------------------------

    def cache_side_detections(self, msg, t: float) -> None:
        """Cache side-camera tracks in the common vehicle frame."""
        if msg is None:
            return
        self._side_det_t = t
        self._side_dets = [
            {
                'dRel': float(d.x), 'yRel': float(d.y), 'vRel': 0.0,
                'cameraVRel': float(d.vx),
                'confidence': float(d.confidence), 'trackId': int(d.trackId),
                'className': str(d.className), 'cameraSource': str(d.cameraSource),
                'sigmaX': max(float(d.sigmaX), 0.1), 'sigmaY': max(float(d.sigmaY), 0.1),
                'source': 'side_camera',
            }
            for d in msg.detections
        ]

    def cache_rear_detections(self, msg, t: float) -> None:
        """Cache rear-camera tracks in the common vehicle frame."""
        if msg is None:
            return
        self._rear_det_t = t
        self._rear_dets = [
            {
                'dRel': float(d.x), 'yRel': float(d.y), 'vRel': 0.0,
                'cameraVRel': float(d.vx),
                'confidence': float(d.confidence), 'trackId': int(d.trackId),
                'className': str(d.className), 'cameraSource': str(d.cameraSource),
                'sigmaX': max(float(d.sigmaX), 0.1), 'sigmaY': max(float(d.sigmaY), 0.1),
                'source': 'rear_camera',
            }
            for d in msg.detections
        ]

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    def update(self, stereo_objects_msg, carstate, side_detections_msg,
               rear_detections_msg, t: float) -> tuple[ZoneState, ZoneState, ZoneState]:
        """
        Classify stereoObjects into zones and update alert states.

        stereo_objects_msg: StereoObjects from gridd (front perception,
            built-in 77 GHz radar, and BLE Radar2D corner tracks)
        carstate: CarState — for native vehicle BSM (leftBlindspot/rightBlindspot)
        side_detections_msg, rear_detections_msg: camera fallback sources
        t: monotonic time (s)
        """
        self._n_updates += 1

        # Refresh camera caches, then associate every current camera object
        # with at most one BLE track. Camera-only objects remain available as
        # degraded evidence when a corner node is unavailable.
        self.cache_side_detections(side_detections_msg, t)
        self.cache_rear_detections(rear_detections_msg, t)

        # Build object list from stereoObjects
        all_objs = list(stereo_objects_msg.objects) if stereo_objects_msg else []
        all_objs = self._associate_camera_objects(all_objs, t)
        self._update_track_motion(all_objs, t)
        self.last_fused_objects = all_objs

        # Partition by zone
        left_objs  = [o for o in all_objs
                      if  self.SIDE_Y_MIN <= o['yRel'] <= self.SIDE_Y_MAX
                      and self.SIDE_D_MIN <= o['dRel'] <= self.SIDE_D_MAX]
        right_objs = [o for o in all_objs
                      if  self.SIDE_Y_MIN <= -o['yRel'] <= self.SIDE_Y_MAX
                      and self.SIDE_D_MIN <=  o['dRel'] <= self.SIDE_D_MAX]
        rear_objs  = [o for o in all_objs
                      if  self.REAR_D_MIN <= o['dRel'] <= self.REAR_D_MAX
                      and abs(o['yRel']) <= self.REAR_Y_MAX]

        # Native car BSM always wins (CAN bus hardware detection)
        if carstate.leftBlindspot:
            left_objs.append(type('_Obj', (), {'dRel': -5.0, 'yRel': 3.0,
                                               'vRel': 0.0, 'prob': 1.0})())
        if carstate.rightBlindspot:
            right_objs.append(type('_Obj', (), {'dRel': -5.0, 'yRel': -3.0,
                                                'vRel': 0.0, 'prob': 1.0})())

        self.left_state  = self._classify(ZoneSide.LEFT,  left_objs,  self._left_seen,  t)
        self.right_state = self._classify(ZoneSide.RIGHT, right_objs, self._right_seen, t)
        self.rear_state  = self._classify(ZoneSide.REAR,  rear_objs,  self._rear_seen,  t)

        if self.left_state.detected:
          self._left_seen  = t
        if self.right_state.detected:
          self._right_seen = t
        if self.rear_state.detected:
          self._rear_seen  = t

        self.lca_blocked_left, self.lca_blocked_right = self._compute_lca_blocked(all_objs)
        # Canonical forward FCW is owned by radard/longitudinalPlan using the
        # built-in forward 77 GHz radar (with the existing model warning path).
        # Short-range corner tracks must not create a duplicate forward FCW.
        self.fcw_state, self.rcw_state = self._collision_warnings(all_objs, carstate)
        self.near_front_state = self._near_front_warning(all_objs, carstate)
        self.fcta_state, self.rcta_state = self._cross_traffic_warnings(all_objs, carstate)

        if self._n_updates % 60 == 0:
            for st in (self.left_state, self.right_state, self.rear_state):
                if st.alert_level != ZoneAlertLevel.OFF:
                    cloudlog.debug("zone %s: %s d=%.1fm v=%.1fm/s",
                                   st.side.value, st.alert_level.name,
                                   st.distance_m, st.vRel_ms)

        return self.left_state, self.right_state, self.rear_state

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _object_dict(obj) -> dict:
        if isinstance(obj, dict):
            return dict(obj)
        return {
            'dRel': float(obj.dRel), 'yRel': float(obj.yRel),
            'vRel': float(obj.vRel), 'confidence': float(obj.prob),
            'prob': float(obj.prob), 'trackId': int(obj.trackId),
            'laneZone': int(obj.laneZone),
        }

    def _associate_camera_objects(self, objects, t: float) -> list[dict]:
        """Fuse YOLO class/continuity onto authoritative BLE kinematics."""
        fused = [self._object_dict(o) for o in objects]
        cameras: list[dict] = []
        if t - self._side_det_t <= self.CAM_TIMEOUT_S:
            cameras.extend(self._side_dets)
        if t - self._rear_det_t <= self.CAM_TIMEOUT_S:
            cameras.extend(self._rear_dets)

        used_camera: set[int] = set()
        for index, radar in enumerate(fused):
            if not is_corner_track_id(radar.get('trackId', 0)):
                continue
            best_index = None
            best_distance = float('inf')
            for camera_index, camera in enumerate(cameras):
                if camera_index in used_camera:
                    continue
                # Do not let a forward side-camera estimate match a rear-only
                # track, or cross the vehicle centreline during association.
                if radar['yRel'] * camera['yRel'] < 0.0:
                    continue
                is_rear_camera = camera['source'] == 'rear_camera'
                if is_rear_camera and radar['dRel'] > 1.0:
                    continue
                gate = self.REAR_ASSOC_M if is_rear_camera else self.SIDE_ASSOC_M
                dx = radar['dRel'] - camera['dRel']
                dy = radar['yRel'] - camera['yRel']
                distance = (dx * dx + dy * dy) ** 0.5
                if distance < gate and distance < best_distance:
                    best_index = camera_index
                    best_distance = distance

            if best_index is None:
                continue
            camera = cameras[best_index]
            used_camera.add(best_index)
            radar_conf = radar.get('confidence', radar.get('prob', 0.0))
            camera_conf = camera['confidence']
            radar['confidence'] = 1.0 - (1.0 - radar_conf) * (1.0 - camera_conf)
            radar['prob'] = radar['confidence']
            radar['className'] = camera['className']
            radar['cameraTrackId'] = camera['trackId']
            radar['cameraSource'] = camera['cameraSource']
            radar['cameraAssociated'] = True
            radar['associationDistanceM'] = best_distance

        for camera_index, camera in enumerate(cameras):
            if camera_index not in used_camera:
                fused.append(dict(camera))
        return fused

    def _update_track_motion(self, objects: list[dict], t: float) -> None:
        """Estimate vehicle-frame XY velocity from stable radar/camera tracks."""
        live_keys: set[tuple[str, int]] = set()
        for obj in objects:
            track_id = int(obj.get('trackId', 0))
            if track_id == 0:
                continue
            source = 'corner_radar' if is_corner_track_id(track_id) else obj.get('source', 'other')
            key = (source, track_id)
            live_keys.add(key)
            previous = self._motion_tracks.get(key)
            vx = vy = 0.0
            samples = 1
            if previous is not None:
                px, py, pt, pvx, pvy, psamples = previous
                dt = t - pt
                if 0.02 <= dt <= 0.5:
                    alpha = 0.35
                    vx = alpha * ((obj['dRel'] - px) / dt) + (1.0 - alpha) * pvx
                    vy = alpha * ((obj['yRel'] - py) / dt) + (1.0 - alpha) * pvy
                    samples = psamples + 1
            obj['vxRel'] = vx
            obj['vyRel'] = vy
            obj['motionSamples'] = samples
            self._motion_tracks[key] = (obj['dRel'], obj['yRel'], t, vx, vy, samples)

        stale = [key for key, value in self._motion_tracks.items()
                 if key not in live_keys and t - value[2] > self.CAM_TIMEOUT_S]
        for key in stale:
            del self._motion_tracks[key]

    @staticmethod
    def _in_reverse(carstate) -> bool:
        return str(getattr(carstate, 'gearShifter', '')).lower().endswith('reverse')

    def _collision_warnings(self, objects: list[dict], carstate) -> tuple[AdvisoryThreat, AdvisoryThreat]:
        """Compute corner-radar RCW; forward FCW is owned by radard."""
        rear_candidates = [o for o in objects
                           if self.RCW_D_MIN <= o['dRel'] <= self.RCW_D_MAX
                           and abs(o['yRel']) <= self.COLLISION_Y_MAX]
        return (
            AdvisoryThreat.empty(ThreatKind.FCW),
            self._collision_state(ThreatKind.RCW, rear_candidates),
        )

    def _collision_state(self, kind: ThreatKind, objects: list[dict]) -> AdvisoryThreat:
        best = None
        for obj in objects:
            confidence = obj.get('confidence', obj.get('prob', 0.0))
            if confidence < self.MIN_CONFIDENCE:
                continue
            v_rel = obj.get('vRel', 0.0)
            ttc = abs(obj['dRel']) / abs(v_rel) if v_rel < -0.1 else float('inf')
            score = ttc if ttc != float('inf') else 1000.0 + abs(obj['dRel'])
            if best is None or score < best[0]:
                best = (score, obj, confidence, ttc)
        if best is None:
            return AdvisoryThreat.empty(kind)
        _, obj, confidence, ttc = best
        radar_valid = is_corner_track_id(obj.get('trackId', 0))
        warning = radar_valid and ttc < self.TTC_WARNING_S
        level = ZoneAlertLevel.WARNING if warning else ZoneAlertLevel.CAUTION
        return AdvisoryThreat(kind, level, True, None, abs(obj['dRel']),
                              obj.get('vRel', 0.0), ttc, confidence)

    def _near_front_warning(self, objects: list[dict], carstate) -> AdvisoryThreat:
        """Low-speed bumper-blind-zone warning from the two front corners.

        This is deliberately separate from FCW and has no braking authority.
        A single unconfirmed corner return is caution-only; a warning/chime
        requires two front corners or one corner associated with a camera track.
        """
        if self._in_reverse(carstate) or abs(float(getattr(carstate, 'vEgo', 0.0))) > self.NEAR_FRONT_EGO_MAX_MS:
            return AdvisoryThreat.empty(ThreatKind.NEAR_FRONT)

        candidates = []
        for obj in objects:
            corner_id = corner_id_from_track_id(obj.get('trackId', 0))
            if corner_id not in (0, 1):
                continue
            if not (self.NEAR_FRONT_D_MIN <= obj['dRel'] <= self.NEAR_FRONT_D_MAX):
                continue
            if abs(obj['yRel']) > self.NEAR_FRONT_Y_MAX:
                continue
            confidence = obj.get('confidence', obj.get('prob', 0.0))
            if confidence >= self.MIN_CONFIDENCE:
                candidates.append((obj, corner_id, confidence))

        if not candidates:
            return AdvisoryThreat.empty(ThreatKind.NEAR_FRONT)

        obj, _, confidence = min(candidates, key=lambda item: item[0]['dRel'])
        independent_corners = {item[1] for item in candidates}
        confirmed = len(independent_corners) >= 2 or bool(obj.get('cameraAssociated', False))
        level = ZoneAlertLevel.WARNING if confirmed else ZoneAlertLevel.CAUTION
        return AdvisoryThreat(ThreatKind.NEAR_FRONT, level, True, None,
                              obj['dRel'], obj.get('vRel', 0.0), float('inf'), confidence)

    def _cross_traffic_warnings(self, objects: list[dict], carstate) -> tuple[AdvisoryThreat, AdvisoryThreat]:
        """Predict front/rear path crossings from successive vehicle-frame tracks."""
        reverse = self._in_reverse(carstate)
        low_speed = abs(float(getattr(carstate, 'vEgo', 0.0))) <= self.CROSS_MAX_EGO_MS
        front: list[tuple[dict, float]] = []
        rear: list[tuple[dict, float]] = []
        if not low_speed:
            return AdvisoryThreat.empty(ThreatKind.FCTA), AdvisoryThreat.empty(ThreatKind.RCTA)

        for obj in objects:
            y = obj['yRel']
            vy = obj.get('vyRel', 0.0)
            if obj.get('motionSamples', 0) < 2 or abs(y) < self.CROSS_Y_MIN:
                continue
            if abs(vy) < self.CROSS_MIN_VY_MS or y * vy >= 0.0:
                continue
            crossing_t = -y / vy
            if not (0.2 <= crossing_t <= self.CROSS_TTC_MAX_S):
                continue
            crossing_x = obj['dRel'] + obj.get('vxRel', 0.0) * crossing_t
            if not reverse and 1.0 <= crossing_x <= 12.0:
                front.append((obj, crossing_t))
            if reverse and -12.0 <= crossing_x <= -1.0:
                rear.append((obj, crossing_t))

        return self._cross_state(ThreatKind.FCTA, front), self._cross_state(ThreatKind.RCTA, rear)

    def _cross_state(self, kind: ThreatKind, candidates: list[tuple[dict, float]]) -> AdvisoryThreat:
        if not candidates:
            return AdvisoryThreat.empty(kind)
        obj, ttc = min(candidates, key=lambda item: item[1])
        confidence = obj.get('confidence', obj.get('prob', 0.0))
        if confidence < self.MIN_CONFIDENCE:
            return AdvisoryThreat.empty(kind)
        radar_valid = is_corner_track_id(obj.get('trackId', 0))
        level = ZoneAlertLevel.WARNING if radar_valid and ttc < self.CROSS_WARNING_S else ZoneAlertLevel.CAUTION
        side = ZoneSide.LEFT if obj['yRel'] > 0.0 else ZoneSide.RIGHT
        return AdvisoryThreat(kind, level, True, side, abs(obj['dRel']),
                              obj.get('vRel', 0.0), ttc, confidence)

    def _compute_lca_blocked(self, objects) -> tuple[bool, bool]:
        """TTC-based lane change block from any adjacent object in the wide LCA zone.

        Source of objects and what each covers:
          radar3d (forward bumper):  dRel > 0 only — forward adjacent (cut-in, merging ahead)
          radar2d (corner sensors):  dRel ≈ -4m — rear-corner zone presence
          carState.leftBlindspot:    handled upstream by _blindspot_blocked() directly

        TTC branches:
          dRel < 0, vRel < 0  → object behind and closing (radar2d rear zone)
          dRel > 0, vRel > 0  → object ahead, ego closing (radar3d forward adjacent)
          |dRel| < 15m         → immediate zone, always block
        """
        left_blocked = False
        right_blocked = False
        for o in objects:
            d = o.dRel if hasattr(o, 'dRel') else o.get('dRel', 0.0)
            y = o.yRel if hasattr(o, 'yRel') else o.get('yRel', 0.0)
            v = o.vRel if hasattr(o, 'vRel') else o.get('vRel', 0.0)

            if not (self.LCA_Y_MIN <= abs(y) <= self.LCA_Y_MAX):
                continue
            if not (self.LCA_D_MIN <= d <= self.LCA_D_MAX):
                continue

            # Skip ego-lane and shoulder objects — they should never block a lane change
            lz = None
            if hasattr(o, 'laneZone'):
                try:
                    lz = int(o.laneZone)
                except (AttributeError, TypeError):
                    lz = None
            elif isinstance(o, dict):
                lz = o.get('laneZone', None)
            if lz in (_LZ_EGO, _LZ_SHOULDER_LEFT, _LZ_SHOULDER_RIGHT):
                continue

            if abs(d) <= 15.0:
                ttc = 0.0                           # immediate zone → always block
            elif d < 0 and v < -0.1:
                ttc = abs(d) / abs(v)               # behind, closing from rear
            elif d > 0 and v > 0.1:
                ttc = d / v                         # ahead, ego closing on them
            else:
                ttc = float('inf')

            if ttc < self.LCA_TTC_S:
                if y > 0:
                    left_blocked = True
                else:
                    right_blocked = True

        return left_blocked, right_blocked

    def _classify(self, side: ZoneSide, objs, last_seen: float, t: float) -> ZoneState:
        if not objs:
            # Hysteresis: briefly keep previous state
            if t - last_seen < self.PERSIST_S:
                prev = getattr(self, f"{'left' if side==ZoneSide.LEFT else ('right' if side==ZoneSide.RIGHT else 'rear')}_state")
                return ZoneState(
                    side=side,
                    alert_level=ZoneAlertLevel.CAUTION if prev.alert_level == ZoneAlertLevel.WARNING else ZoneAlertLevel.OFF,
                    detected=False,
                    distance_m=prev.distance_m,
                    vRel_ms=prev.vRel_ms,
                    ttc_s=prev.ttc_s,
                    confidence=prev.confidence * 0.9,
                )
            return ZoneState.empty(side)

        # Find most threatening object above confidence threshold
        best, best_threat = None, -1.0
        for o in objs:
            conf = getattr(o, 'prob', None) or getattr(o, 'confidence', 0.0)
            if isinstance(o, dict):
                conf = o.get('confidence', o.get('prob', 0.0))
                d, v = o['dRel'], o['vRel']
            else:
                d, v = o.dRel, o.vRel
            if conf < self.MIN_CONFIDENCE:
                continue
            threat = (abs(v) * 2.0 if v < 0 else 0.0) + (max(0.0, 5.0 - abs(d)) * 3.0)
            if threat > best_threat:
                best_threat, best = threat, (d, v, conf)

        if best is None:
            return ZoneState.empty(side)

        d_rel, v_rel, conf = best
        if v_rel < -0.1:
            ttc = abs(d_rel) / abs(v_rel) if d_rel != 0 else float('inf')
            ttc = min(ttc, 999.0)
        else:
            ttc = float('inf')

        is_warning = (v_rel < self.FAST_APPROACH_MS) or (ttc < self.TTC_WARNING_S and v_rel < 0)
        level = ZoneAlertLevel.WARNING if is_warning else ZoneAlertLevel.CAUTION
        return ZoneState.from_object(side, level, d_rel, v_rel, ttc, conf)

    def _cam_side_objs(self, left: bool, t: float) -> list:
        if t - self._side_det_t > self.CAM_TIMEOUT_S:
            return []
        out = []
        for det in self._side_dets:
            y = det['yRel']
            if not (self.SIDE_Y_MIN <= abs(y) <= self.SIDE_Y_MAX):
                continue
            if not (self.SIDE_D_MIN <= det['dRel'] <= self.SIDE_D_MAX):
                continue
            if (left and y > 0) or (not left and y < 0):
                out.append(det)
        return out

    def _cam_rear_objs(self, t: float) -> list:
        if t - self._rear_det_t > self.CAM_TIMEOUT_S:
            return []
        return [d for d in self._rear_dets
                if self.REAR_D_MIN <= d['dRel'] <= self.REAR_D_MAX
                and abs(d['yRel']) <= self.REAR_Y_MAX]

    # ------------------------------------------------------------------
    # Alert output helpers (used by controlsd)
    # ------------------------------------------------------------------

    def chime_request(self, t: float) -> tuple[bool, ZoneSide | None]:
        """Returns (should_chime, side). Enforces cooldown."""
        if t - self._last_chime < self.CHIME_COOLDOWN_S:
            return False, None
        for threat in (self.fcw_state, self.near_front_state, self.rcw_state, self.fcta_state, self.rcta_state):
            if threat.alert_level == ZoneAlertLevel.WARNING:
                self._last_chime = t
                return True, threat.side
        for st in (self.left_state, self.right_state, self.rear_state):
            if st.alert_level == ZoneAlertLevel.WARNING:
                self._last_chime = t
                return True, st.side
        return False, None

    def alert_message(self) -> str | None:
        if self.fcw_state.alert_level == ZoneAlertLevel.WARNING:
          return "Front collision warning"
        if self.near_front_state.alert_level == ZoneAlertLevel.WARNING:
          return "Front obstacle very close"
        if self.rcw_state.alert_level == ZoneAlertLevel.WARNING:
          return "Rear collision warning"
        if self.fcta_state.alert_level == ZoneAlertLevel.WARNING:
          side = self.fcta_state.side.value if self.fcta_state.side is not None else "side"
          return f"Front cross traffic from {side}"
        if self.rcta_state.alert_level == ZoneAlertLevel.WARNING:
          side = self.rcta_state.side.value if self.rcta_state.side is not None else "side"
          return f"Rear cross traffic from {side}"
        lw = self.left_state.alert_level  == ZoneAlertLevel.WARNING
        rw = self.right_state.alert_level == ZoneAlertLevel.WARNING
        bw = self.rear_state.alert_level  == ZoneAlertLevel.WARNING
        # Side zones take priority over rear: an object in the overlap region
        # (behind ego AND in the adjacent lane, e.g. an overtaking car) is a
        # blind-spot threat when driving forward.  Pure rear-zone objects
        # (|yRel| below SIDE_Y_MIN) still report as rear cross traffic.
        if lw and rw:
          return "Vehicles in both blind spots"
        if lw:
          return "Vehicle in left blind spot"
        if rw:
          return "Vehicle in right blind spot"
        if bw:
          return "Vehicle approaching from rear"
        if self.fcw_state.alert_level == ZoneAlertLevel.CAUTION:
          return "Check vehicle ahead"
        if self.near_front_state.alert_level == ZoneAlertLevel.CAUTION:
          return "Check close front corner"
        if self.rcw_state.alert_level == ZoneAlertLevel.CAUTION:
          return "Check vehicle behind"
        if self.fcta_state.alert_level == ZoneAlertLevel.CAUTION:
          return "Check front cross traffic"
        if self.rcta_state.alert_level == ZoneAlertLevel.CAUTION:
          return "Check rear cross traffic"
        lc = self.left_state.alert_level  == ZoneAlertLevel.CAUTION
        rc = self.right_state.alert_level == ZoneAlertLevel.CAUTION
        bc = self.rear_state.alert_level  == ZoneAlertLevel.CAUTION
        if lc and rc:
          return "Check blind spots"
        if lc:
          return "Vehicle left"
        if rc:
          return "Vehicle right"
        if bc:
          return "Check behind"
        return None
