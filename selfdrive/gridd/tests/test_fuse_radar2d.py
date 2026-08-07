import math

import cereal.messaging as messaging
from openpilot.selfdrive.gridd.gridd import GridD


def _radar2d_msg(returns: list[dict] | None = None, objects: list[dict] | None = None):
    msg = messaging.new_message('radar2d')
    returns = returns or []
    ret_out = msg.radar2d.init('returns', len(returns))
    for i, r in enumerate(returns):
        ret_out[i].side = r.get('side', 0)
        ret_out[i].present = r.get('present', False)
        ret_out[i].vRel = r.get('vRel', 0.0)

    objects = objects or []
    obj_out = msg.radar2d.init('objects', len(objects))
    for i, o in enumerate(objects):
        obj_out[i].trackId = o.get('trackId', 1)
        obj_out[i].corner = o.get('corner', 0)
        obj_out[i].rangM = o['rangM']
        obj_out[i].azimuthDeg = o.get('azimuthDeg', 0.0)
        obj_out[i].vRel = o.get('vRel', 0.0)
        obj_out[i].aRel = o.get('aRel', 0.0)
        obj_out[i].snrDb = o.get('snrDb', 20.0)
        obj_out[i].existenceProb = o.get('existenceProb', 100.0)
        obj_out[i].measured = o.get('measured', True)
        obj_out[i].dynProp = o.get('dynProp', 0)
        obj_out[i].lengthM = o.get('lengthM', 0.0)
        obj_out[i].widthM = o.get('widthM', 0.0)
    return msg.radar2d


class _FuseHost:
    """Minimal stand-in exposing only what _fuse_radar2d reads — avoids
    constructing a full GridD (heavy messaging/costmap setup in __init__)."""
    _R2D_ZONE_POS = GridD._R2D_ZONE_POS
    _R2D_ZONE_RADIUS = GridD._R2D_ZONE_RADIUS
    _R2D_PROB = GridD._R2D_PROB
    _R2D_CORNER_POSE = GridD._R2D_CORNER_POSE
    _R4D_SNR_REF_DB = GridD._R4D_SNR_REF_DB
    _R4D_CONFIDENCE_BOOST = GridD._R4D_CONFIDENCE_BOOST
    _active_costmap = None

    _fuse_radar2d = GridD._fuse_radar2d
    _fuse_radar2d_objects = GridD._fuse_radar2d_objects
    _fuse_radar2d_returns = GridD._fuse_radar2d_returns


class _Costmap:
    def __init__(self):
        self.calls = []

    def add_obstacle(self, d, y, radius, cost):
        self.calls.append((d, y, radius, cost))


class TestFuseRadar2DObjects:
    """Tests for the on-node tracked Radar2DObject fusion path (ESP32-S3
    corner radars with occlusion coasting)."""

    def test_measured_object_lands_at_pose_transformed_position(self):
        """FL corner (pose x=1.8, y=0.8, yaw=45 deg), target 2 m on boresight:
        the vehicle-frame position must be the pose rotated by the corner yaw."""
        host = _FuseHost()
        host._active_costmap = _Costmap()
        msg = _radar2d_msg(objects=[{
            'trackId': 42, 'corner': 0, 'rangM': 2.0, 'azimuthDeg': 0.0, 'vRel': -3.0,
        }])
        objects = host._fuse_radar2d([], msg)
        assert len(objects) == 1
        sqrt2 = math.sqrt(2.0)
        assert abs(objects[0]['dRel'] - (1.8 + sqrt2)) < 1e-6
        assert abs(objects[0]['yRel'] - (0.8 + sqrt2)) < 1e-6
        assert objects[0]['trackId'] == 42
        assert objects[0]['vRel'] == -3.0
        # Measured object stamps a costmap blob at its real position.
        assert len(host._active_costmap.calls) == 1
        d, y, radius, _ = host._active_costmap.calls[0]
        assert abs(d - (1.8 + sqrt2)) < 1e-6
        assert abs(y - (0.8 + sqrt2)) < 1e-6

    def test_object_radius_from_shape_when_sane(self):
        host = _FuseHost()
        host._active_costmap = _Costmap()
        msg = _radar2d_msg(objects=[{
            'corner': 1, 'rangM': 3.0, 'azimuthDeg': 0.0,
            'lengthM': 3.0, 'widthM': 4.4,
        }])
        host._fuse_radar2d([], msg)
        assert abs(host._active_costmap.calls[0][2] - 2.2) < 1e-6  # max(3.0, 4.4) / 2, not _R2D_ZONE_RADIUS

    def test_coasted_object_reduced_confidence_no_costmap(self):
        """A predict-only (measured=false) coasted track stays in the objects
        list with halved confidence but must not create a hard obstacle."""
        host = _FuseHost()
        host._active_costmap = _Costmap()
        coasted = _radar2d_msg(objects=[{
            'corner': 0, 'rangM': 2.0, 'azimuthDeg': 0.0,
            'snrDb': 20.0, 'existenceProb': 100.0, 'measured': False,
        }])
        measured = _radar2d_msg(objects=[{
            'corner': 0, 'rangM': 2.0, 'azimuthDeg': 0.0,
            'snrDb': 20.0, 'existenceProb': 100.0, 'measured': True,
        }])
        coasted_objects = host._fuse_radar2d([], coasted)
        measured_objects = host._fuse_radar2d([], measured)
        assert len(coasted_objects) == 1
        assert abs(coasted_objects[0]['confidence'] - measured_objects[0]['confidence'] * 0.5) < 1e-9
        # One costmap blob from the measured twin only — the coasted one added none.
        assert len(host._active_costmap.calls) == 1

    def test_unknown_corner_skipped(self):
        """0xFF = unresolved corner strap — no mounting pose, so the track is dropped."""
        host = _FuseHost()
        msg = _radar2d_msg(objects=[{'corner': 0xFF, 'rangM': 2.0, 'azimuthDeg': 0.0}])
        assert host._fuse_radar2d([], msg) == []

    def test_legacy_returns_fallback_when_no_objects(self):
        """A presence-only message (empty objects list) still flows through the
        legacy zone path unchanged."""
        host = _FuseHost()
        host._active_costmap = _Costmap()
        msg = _radar2d_msg(returns=[{'side': 0, 'present': True, 'vRel': -2.0}])
        objects = host._fuse_radar2d([], msg)
        assert len(objects) == 1
        d_rel, y_rel = _FuseHost._R2D_ZONE_POS[0]
        assert objects[0]['dRel'] == d_rel
        assert objects[0]['yRel'] == y_rel
        assert objects[0]['trackId'] == 0
        assert objects[0]['confidence'] == _FuseHost._R2D_PROB
        assert len(host._active_costmap.calls) == 1
