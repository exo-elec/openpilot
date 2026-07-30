#!/usr/bin/env python3
"""
RadarZoneMonitor — adjacent-lane and rear-zone threat assessment.

Reads unified stereoObjects published by gridd (which already fuses
radar4d/radar2d/camera data) and classifies objects into left-side,
right-side, and rear zones.  Camera sideDetections/rearDetections are
used as a fallback when radar hardware is absent.

This is purely a zone classifier + alert state machine.
All sensor fusion lives in gridd.py — this module has zero raw-sensor logic.
"""

from dataclasses import dataclass
from enum import Enum

from openpilot.common.swaglog import cloudlog

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


class RadarZoneMonitor:
    """
    Adjacent-lane and rear zone monitor.

    Consumes stereoObjects (already fused by gridd from radar4d + radar2d + camera)
    and classifies objects by zone geometry.

    Radar2d zone-presence objects and radar4d adjacent-lane objects arrive in
    stereoObjects already converted to vehicle-frame (dRel, yRel, vRel) — this
    class only needs to filter by position bounds.
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

    # Camera-side detection timeout (fallback path)
    CAM_TIMEOUT_S      =  1.0

    def __init__(self):
        self.left_state  = ZoneState.empty(ZoneSide.LEFT)
        self.right_state = ZoneState.empty(ZoneSide.RIGHT)
        self.rear_state  = ZoneState.empty(ZoneSide.REAR)

        self._left_seen   = 0.0
        self._right_seen  = 0.0
        self._rear_seen   = 0.0
        self._last_chime  = -999.0

        self.lca_blocked_left  = False
        self.lca_blocked_right = False

        # Camera fallback caches (used when no radar2d/4d hardware)
        self._side_dets: list[dict] = []
        self._side_det_t = 0.0
        self._rear_dets: list[dict] = []
        self._rear_det_t = 0.0

        self._n_updates = 0

    # ------------------------------------------------------------------
    # Camera fallback input (populated by controlsd before update())
    # ------------------------------------------------------------------

    def cache_side_detections(self, msg, t: float) -> None:
        """Cache sideDetections for camera fallback path."""
        if msg is None:
            return
        self._side_det_t = t
        self._side_dets = [
            {'dRel': d.x, 'yRel': d.y, 'vRel': 0.0, 'confidence': d.confidence}
            for d in msg.detections
        ]

    def cache_rear_detections(self, msg, t: float) -> None:
        """Cache rearDetections for camera fallback path."""
        if msg is None:
            return
        self._rear_det_t = t
        self._rear_dets = [
            {'dRel': d.x, 'yRel': d.y, 'vRel': 0.0, 'confidence': d.confidence}
            for d in msg.detections
        ]

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    def update(self, stereo_objects_msg, carstate, side_detections_msg,
               rear_detections_msg, t: float) -> tuple[ZoneState, ZoneState, ZoneState]:
        """
        Classify stereoObjects into zones and update alert states.

        stereo_objects_msg: StereoObjects from gridd (already fused:
            radar4d velocity-annotated + radar2d zone-presence + camera)
        carstate: CarState — for native vehicle BSM (leftBlindspot/rightBlindspot)
        side_detections_msg, rear_detections_msg: camera fallback sources
        t: monotonic time (s)
        """
        self._n_updates += 1

        # Refresh camera caches (fallback only when no radar HW)
        self.cache_side_detections(side_detections_msg, t)
        self.cache_rear_detections(rear_detections_msg, t)

        # Build object list from stereoObjects
        all_objs = list(stereo_objects_msg.objects) if stereo_objects_msg else []

        # Partition by zone
        left_objs  = [o for o in all_objs
                      if  self.SIDE_Y_MIN <= o.yRel <= self.SIDE_Y_MAX
                      and self.SIDE_D_MIN <= o.dRel <= self.SIDE_D_MAX]
        right_objs = [o for o in all_objs
                      if  self.SIDE_Y_MIN <= -o.yRel <= self.SIDE_Y_MAX
                      and self.SIDE_D_MIN <=  o.dRel <= self.SIDE_D_MAX]
        rear_objs  = [o for o in all_objs
                      if  self.REAR_D_MIN <= o.dRel <= self.REAR_D_MAX
                      and abs(o.yRel) <= self.REAR_Y_MAX]

        # Native car BSM always wins (CAN bus hardware detection)
        if carstate.leftBlindspot:
            left_objs.append(type('_Obj', (), {'dRel': -5.0, 'yRel': 3.0,
                                               'vRel': 0.0, 'prob': 1.0})())
        if carstate.rightBlindspot:
            right_objs.append(type('_Obj', (), {'dRel': -5.0, 'yRel': -3.0,
                                                'vRel': 0.0, 'prob': 1.0})())

        # Camera fallback: only when no radar objects for that zone
        if not left_objs:
            left_objs  += self._cam_side_objs(left=True, t=t)
        if not right_objs:
            right_objs += self._cam_side_objs(left=False, t=t)
        if not rear_objs:
            rear_objs  += self._cam_rear_objs(t=t)

        self.left_state  = self._classify(ZoneSide.LEFT,  left_objs,  self._left_seen,  t)
        self.right_state = self._classify(ZoneSide.RIGHT, right_objs, self._right_seen, t)
        self.rear_state  = self._classify(ZoneSide.REAR,  rear_objs,  self._rear_seen,  t)

        if self.left_state.detected:   self._left_seen  = t
        if self.right_state.detected:  self._right_seen = t
        if self.rear_state.detected:   self._rear_seen  = t

        self.lca_blocked_left, self.lca_blocked_right = self._compute_lca_blocked(all_objs)

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

    def _compute_lca_blocked(self, objects) -> tuple[bool, bool]:
        """TTC-based lane change block from any adjacent object in the wide LCA zone.

        Source of objects and what each covers:
          radar3d (forward bumper):  dRel > 0 only — forward adjacent (cut-in, merging ahead)
          radar4d (front FMCW ±40°): dRel > 0 mostly — forward + slight lateral
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
        for st in (self.left_state, self.right_state, self.rear_state):
            if st.alert_level == ZoneAlertLevel.WARNING:
                self._last_chime = t
                return True, st.side
        return False, None

    def alert_message(self) -> str | None:
        lw = self.left_state.alert_level  == ZoneAlertLevel.WARNING
        rw = self.right_state.alert_level == ZoneAlertLevel.WARNING
        bw = self.rear_state.alert_level  == ZoneAlertLevel.WARNING
        # Side zones take priority over rear: an object in the overlap region
        # (behind ego AND in the adjacent lane, e.g. an overtaking car) is a
        # blind-spot threat when driving forward.  Pure rear-zone objects
        # (|yRel| below SIDE_Y_MIN) still report as rear cross traffic.
        if lw and rw:       return "Vehicles in both blind spots"
        if lw:              return "Vehicle in left blind spot"
        if rw:              return "Vehicle in right blind spot"
        if bw:              return "Rear cross traffic"
        lc = self.left_state.alert_level  == ZoneAlertLevel.CAUTION
        rc = self.right_state.alert_level == ZoneAlertLevel.CAUTION
        bc = self.rear_state.alert_level  == ZoneAlertLevel.CAUTION
        if lc and rc:       return "Check blind spots"
        if lc:              return "Vehicle left"
        if rc:              return "Vehicle right"
        if bc:              return "Check rear cross traffic"
        return None
