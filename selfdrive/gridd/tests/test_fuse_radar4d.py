import cereal.messaging as messaging
from openpilot.selfdrive.gridd.gridd import GridD


def _radar4d_msg(points: list[dict], objects: list[dict] | None = None,
                 drop_off_hazard: bool = False, drop_off_dist_m: float = 0.0):
    msg = messaging.new_message('radar4d')
    out = msg.radar4d.init('points', len(points))
    for i, p in enumerate(points):
        out[i].trackId = p.get('trackId', 1)
        out[i].rangM = p['rangM']
        out[i].azimuth = p.get('azimuth', 0.0)
        out[i].elevation = p.get('elevation', 0.0)
        out[i].vRel = p.get('vRel', 0.0)
        out[i].snrDb = p.get('snrDb', 20.0)
        out[i].existenceProb = p.get('existenceProb', 100.0)
        out[i].isStatic = p.get('isStatic', False)
        out[i].dynProp = p.get('dynProp', 0)
        out[i].aRel = p.get('aRel', 0.0)

    objects = objects or []
    obj_out = msg.radar4d.init('objects', len(objects))
    for i, o in enumerate(objects):
        obj_out[i].trackId = o.get('trackId', 1)
        obj_out[i].rangM = o['rangM']
        obj_out[i].azimuth = o.get('azimuth', 0.0)
        obj_out[i].elevation = o.get('elevation', 0.0)
        obj_out[i].vRel = o.get('vRel', 0.0)
        obj_out[i].aRel = o.get('aRel', 0.0)
        obj_out[i].snrDb = o.get('snrDb', 20.0)
        obj_out[i].existenceProb = o.get('existenceProb', 100.0)
        obj_out[i].isStatic = o.get('isStatic', False)
        obj_out[i].dynProp = o.get('dynProp', 0)
        obj_out[i].lengthM = o.get('lengthM', 0.0)
        obj_out[i].widthM = o.get('widthM', 0.0)
        obj_out[i].heightM = o.get('heightM', 0.0)
        obj_out[i].yawRad = o.get('yawRad', 0.0)
        obj_out[i].pointCount = o.get('pointCount', 1)
    msg.radar4d.dropOffHazard = drop_off_hazard
    msg.radar4d.dropOffDistM = drop_off_dist_m
    return msg.radar4d


class _FuseHost:
    """Minimal stand-in exposing only what _fuse_radar4d reads — avoids
    constructing a full GridD (heavy messaging/costmap setup in __init__)."""
    _R4D_BLIND_AZ_DEG = GridD._R4D_BLIND_AZ_DEG
    _R4D_ASSOC_M = GridD._R4D_ASSOC_M
    _R4D_ELEV_GATE_DEG = GridD._R4D_ELEV_GATE_DEG
    _R4D_SNR_REF_DB = GridD._R4D_SNR_REF_DB
    _R4D_CONFIDENCE_BOOST = GridD._R4D_CONFIDENCE_BOOST
    _R4D_SKIP_STATIC = GridD._R4D_SKIP_STATIC
    _R4D_USE_FOV_GATE = GridD._R4D_USE_FOV_GATE
    _R4D_VEL_OUTLIER_MPS = GridD._R4D_VEL_OUTLIER_MPS
    _R4D_VEL_ASSOC_GATE_MPS = GridD._R4D_VEL_ASSOC_GATE_MPS
    _R4D_BOX_LEN_M = GridD._R4D_BOX_LEN_M
    _R4D_BOX_WIDTH_M = GridD._R4D_BOX_WIDTH_M
    _R4D_BOX_HEIGHT_M = GridD._R4D_BOX_HEIGHT_M
    _EGO_LANE_FALLBACK_M = GridD._EGO_LANE_FALLBACK_M
    _active_costmap = None
    _lane_cache = {'valid': False}
    radar_geometry = None  # FOV gate disabled in unit tests

    _fuse_radar4d = GridD._fuse_radar4d
    _fuse_radar4d_objects = GridD._fuse_radar4d_objects
    _fuse_radar4d_points = GridD._fuse_radar4d_points
    _merge_radar4d_dropoff = GridD._merge_radar4d_dropoff
    _object_box_m = staticmethod(GridD._object_box_m)
    _estimate_box_kinematics = classmethod(GridD._estimate_box_kinematics)
    _radar4d_in_camera_fov = GridD._radar4d_in_camera_fov
    _ego_lane_bounds = GridD._ego_lane_bounds


class TestFuseRadar4D:
    def test_in_gate_point_creates_object(self):
        host = _FuseHost()
        msg = _radar4d_msg([{'rangM': 8.0, 'azimuth': 0.0, 'elevation': 0.0, 'vRel': -3.0}])
        objects = host._fuse_radar4d([], msg)
        assert len(objects) == 1
        assert objects[0]['dRel'] == 8.0
        assert objects[0]['vRel'] == -3.0

    def test_out_of_elevation_gate_point_rejected(self):
        host = _FuseHost()
        elev = _FuseHost._R4D_ELEV_GATE_DEG + 5.0
        msg = _radar4d_msg([{'rangM': 8.0, 'azimuth': 0.0, 'elevation': elev}])
        objects = host._fuse_radar4d([], msg)
        assert objects == []

    def test_confidence_boost_scales_with_existence_prob(self):
        host = _FuseHost()
        low_msg = _radar4d_msg([{'rangM': 8.0, 'snrDb': 20.0, 'existenceProb': 0.0}])
        high_msg = _radar4d_msg([{'rangM': 8.0, 'snrDb': 20.0, 'existenceProb': 100.0}])

        low_objects = host._fuse_radar4d([], low_msg)
        high_objects = host._fuse_radar4d([], high_msg)

        assert high_objects[0]['confidence'] > low_objects[0]['confidence']

    def test_matched_object_gets_velocity_annotation_not_duplicated(self):
        host = _FuseHost()
        existing = [{'dRel': 8.0, 'yRel': 0.0, 'vRel': 0.0, 'confidence': 0.5, 'prob': 0.5,
                     'trackId': 99, 'obstacleType': 0}]
        msg = _radar4d_msg([{'rangM': 8.0, 'azimuth': 0.0, 'vRel': -5.0}])
        objects = host._fuse_radar4d(existing, msg)
        assert len(objects) == 1  # matched, not appended as a new entry
        assert objects[0]['vRel'] == -5.0
        assert objects[0]['trackId'] == 99  # identity preserved from the stereo object

    def test_dyn_prop_forwarded_to_object(self):
        host = _FuseHost()
        msg = _radar4d_msg([{'rangM': 8.0, 'azimuth': 0.0, 'vRel': -3.0, 'dynProp': 1}])
        objects = host._fuse_radar4d([], msg)
        assert objects[0]['dynProp'] == 1

    def test_acceleration_forwarded_to_object(self):
        host = _FuseHost()
        msg = _radar4d_msg([{'rangM': 8.0, 'azimuth': 0.0, 'vRel': -3.0, 'aRel': -2.5}])
        objects = host._fuse_radar4d([], msg)
        assert objects[0]['aRel'] == -2.5

    def test_out_of_lane_object_kept(self):
        """Radar fusion maps with stereo/wide/road cameras, not just the ego
        lane: forward objects far outside the lane corridor (parked scooters,
        roadside obstacles) must be kept, not rejected as clutter."""
        host = _FuseHost()
        host._lane_cache = {
            'x': [0.0, 10.0, 20.0, 30.0],
            'left_y': [2.0, 2.0, 2.0, 2.0],
            'right_y': [-2.0, -2.0, -2.0, -2.0],
            'far_left_y': None, 'far_right_y': None,
            'left_edge_y': None, 'right_edge_y': None,
            'valid': True,
        }
        # Forward point well left of the lane corridor (y_rel ≈ 6.8 m at 20 m).
        msg = _radar4d_msg([{'rangM': 20.0, 'azimuth': 20.0, 'vRel': 0.0}])
        objects = host._fuse_radar4d([], msg)
        assert len(objects) == 1

    def test_fov_gate_union_of_road_and_wide(self):
        """The FOV gate must keep returns visible to ANY fused camera — a
        point outside the narrow road FOV but inside the wide FOV is kept."""
        class _Geo:
            class cam_geo:
                cameras = {'road': object(), 'wide_road': object()}

            @staticmethod
            def radar_in_camera_fov(name, rang_m, az, el):
                # Road camera sees |az| <= 20 deg, wide sees |az| <= 75 deg.
                limit = 20.0 if name == 'road' else 75.0
                return abs(az) <= limit

        host = _FuseHost()
        host.radar_geometry = _Geo()
        assert host._radar4d_in_camera_fov(10.0, 45.0, 0.0)   # wide-only zone: kept
        assert not host._radar4d_in_camera_fov(10.0, 80.0, 0.0)  # outside all: dropped

    def test_fov_gate_fail_open_without_geometry(self):
        host = _FuseHost()
        host.radar_geometry = None
        assert host._radar4d_in_camera_fov(10.0, 80.0, 0.0)

    def test_box_velocity_rejects_outlier(self):
        """A clutter point far from the median should not corrupt the object's vRel."""
        host = _FuseHost()
        existing = [{'dRel': 10.0, 'yRel': 0.0, 'vRel': 0.0, 'confidence': 0.5, 'prob': 0.5,
                     'trackId': 1, 'obstacleType': 'car'}]
        msg = _radar4d_msg([
            {'rangM': 10.0, 'azimuth': 0.0, 'vRel': -5.0, 'snrDb': 20.0, 'existenceProb': 100.0},
            {'rangM': 10.2, 'azimuth': 0.0, 'vRel': -5.2, 'snrDb': 20.0, 'existenceProb': 100.0},
            {'rangM': 9.8, 'azimuth': 0.0, 'vRel': -0.5, 'snrDb': 20.0, 'existenceProb': 100.0},  # outlier
        ])
        objects = host._fuse_radar4d(existing, msg)
        # Median of [-5.0, -5.2, -0.5] is -5.0; outlier -0.5 is >3 m/s away, so rejected.
        assert abs(objects[0]['vRel'] - (-5.0)) < 0.3

    def test_static_point_skipped_when_configured(self):
        host = _FuseHost()
        host._R4D_SKIP_STATIC = True
        msg = _radar4d_msg([
            {'rangM': 8.0, 'azimuth': 0.0, 'vRel': 0.0, 'isStatic': True},
            {'rangM': 9.0, 'azimuth': 0.0, 'vRel': -4.0, 'isStatic': False},
        ])
        objects = host._fuse_radar4d([], msg)
        assert len(objects) == 1
        assert objects[0]['vRel'] == -4.0

    def test_object_box_defaults_for_classes(self):
        host = _FuseHost()
        assert host._object_box_m({'obstacleType': 'person'})[1] < 1.0  # narrow
        assert host._object_box_m({'obstacleType': 'truck'})[0] > 6.0   # long
        assert host._object_box_m({'obstacleType': 0})[0] == GridD._R4D_BOX_LEN_M

    def test_object_box_uses_radar_shape(self):
        """Radar-estimated shape should override class defaults."""
        host = _FuseHost()
        obj = {'obstacleType': 'car', 'length': 5.2, 'width': 2.1, 'height': 1.6}
        assert host._object_box_m(obj) == (5.2, 2.1, 1.6)


class TestFuseRadar4DObjects:
    """Tests for the lidar-style Radar4DObject fusion path."""

    def test_object_fusion_creates_object_with_shape(self):
        host = _FuseHost()
        msg = _radar4d_msg([], objects=[{
            'rangM': 8.0, 'azimuth': 0.0, 'vRel': -3.0,
            'lengthM': 4.2, 'widthM': 1.8, 'heightM': 1.5, 'yawRad': 0.1,
        }])
        objects = host._fuse_radar4d([], msg)
        assert len(objects) == 1
        assert objects[0]['dRel'] == 8.0
        assert objects[0]['vRel'] == -3.0
        assert abs(objects[0]['length'] - 4.2) < 1e-3
        assert abs(objects[0]['width'] - 1.8) < 1e-3
        assert abs(objects[0]['height'] - 1.5) < 1e-3
        assert abs(objects[0]['yawRad'] - 0.1) < 1e-3

    def test_object_fusion_shape_aware_gate_matches_stereo(self):
        """A stereo object inside the radar object's oriented box should match."""
        host = _FuseHost()
        # Prior vRel within the velocity-consistency gate of the radar cluster.
        existing = [{'dRel': 8.0, 'yRel': 0.0, 'vRel': -3.5, 'confidence': 0.5,
                     'prob': 0.5, 'trackId': 99, 'obstacleType': 'car'}]
        msg = _radar4d_msg([], objects=[{
            'rangM': 8.0, 'azimuth': 0.0, 'vRel': -4.0,
            'lengthM': 4.0, 'widthM': 2.0, 'heightM': 1.5, 'yawRad': 0.0,
        }])
        objects = host._fuse_radar4d(existing, msg)
        assert len(objects) == 1  # matched, not duplicated
        assert objects[0]['vRel'] == -4.0
        assert objects[0]['trackId'] == 99
        assert objects[0]['length'] == 4.0

    def test_object_fusion_gate_rejects_far_object(self):
        """A stereo object outside the radar object's box should not match."""
        host = _FuseHost()
        existing = [{'dRel': 12.0, 'yRel': 0.0, 'vRel': 0.0, 'confidence': 0.5,
                     'prob': 0.5, 'trackId': 99, 'obstacleType': 'car'}]
        msg = _radar4d_msg([], objects=[{
            'rangM': 8.0, 'azimuth': 0.0, 'vRel': -4.0,
            'lengthM': 4.0, 'widthM': 2.0, 'heightM': 1.5, 'yawRad': 0.0,
        }])
        objects = host._fuse_radar4d(existing, msg)
        assert len(objects) == 2  # stereo object + new radar object

    def test_object_fusion_yaw_aware_gate(self):
        """Rotated radar object should still match a stereo object along its long axis."""
        host = _FuseHost()
        # Stereo object 2m ahead of radar object center, along the radar object's
        # long axis (yaw = 90 deg, so long axis is lateral).
        existing = [{'dRel': 8.0, 'yRel': 2.0, 'vRel': 0.0, 'confidence': 0.5,
                     'prob': 0.5, 'trackId': 99, 'obstacleType': 'car'}]
        msg = _radar4d_msg([], objects=[{
            'rangM': 8.0, 'azimuth': 0.0, 'vRel': -4.0,
            'lengthM': 5.0, 'widthM': 2.0, 'heightM': 1.5, 'yawRad': 1.5708,
        }])
        objects = host._fuse_radar4d(existing, msg)
        assert len(objects) == 1  # matched via yaw-aware gate

    def test_points_fallback_when_no_objects(self):
        """When radar4d.objects is empty, the raw points path should still work."""
        host = _FuseHost()
        msg = _radar4d_msg([{'rangM': 8.0, 'azimuth': 0.0, 'vRel': -3.0}])
        objects = host._fuse_radar4d([], msg)
        assert len(objects) == 1
        assert objects[0]['dRel'] == 8.0
        assert 'length' not in objects[0]

    def test_multiple_clusters_best_score_wins(self):
        """Two radar clusters matching one stereo object: the nearest-to-center
        cluster must win, not the last one in the message (Autoware robust pick)."""
        host = _FuseHost()
        existing = [{'dRel': 8.0, 'yRel': 0.0, 'vRel': -4.0, 'confidence': 0.5,
                     'prob': 0.5, 'trackId': 99, 'obstacleType': 'car'}]
        # Both clusters contain the stereo object inside their box and both
        # velocities are within the consistency gate; the second (listed last)
        # is the worse match and must NOT overwrite the first.
        msg = _radar4d_msg([], objects=[
            {'rangM': 8.1, 'azimuth': 0.0, 'vRel': -4.0,
             'lengthM': 4.0, 'widthM': 2.0, 'heightM': 1.5, 'yawRad': 0.0},
            {'rangM': 9.5, 'azimuth': 0.0, 'vRel': -5.5,
             'lengthM': 4.0, 'widthM': 2.0, 'heightM': 1.5, 'yawRad': 0.0},
        ])
        objects = host._fuse_radar4d(existing, msg)
        assert len(objects) == 1
        assert objects[0]['vRel'] == -4.0  # best-scoring cluster won

    def test_velocity_consistency_gate_skips_attach(self):
        """If the stereo object already has a vRel that disagrees with the radar
        cluster by more than the gate, keep the boost but not the velocity."""
        host = _FuseHost()
        gate = _FuseHost._R4D_VEL_ASSOC_GATE_MPS
        existing = [{'dRel': 8.0, 'yRel': 0.0, 'vRel': 0.0, 'confidence': 0.5,
                     'prob': 0.5, 'trackId': 99, 'obstacleType': 'car'}]
        msg = _radar4d_msg([], objects=[{
            'rangM': 8.0, 'azimuth': 0.0, 'vRel': -(gate + 2.0),
            'lengthM': 4.0, 'widthM': 2.0, 'heightM': 1.5, 'yawRad': 0.0,
        }])
        objects = host._fuse_radar4d(existing, msg)
        assert len(objects) == 1
        assert objects[0]['vRel'] == 0.0          # inconsistent velocity not attached
        assert objects[0]['confidence'] > 0.5     # confidence boost still applied

    def test_velocity_consistency_gate_passes_close_velocity(self):
        """Within the gate, the radar velocity is attached normally."""
        host = _FuseHost()
        existing = [{'dRel': 8.0, 'yRel': 0.0, 'vRel': -3.0, 'confidence': 0.5,
                     'prob': 0.5, 'trackId': 99, 'obstacleType': 'car'}]
        msg = _radar4d_msg([], objects=[{
            'rangM': 8.0, 'azimuth': 0.0, 'vRel': -4.0,
            'lengthM': 4.0, 'widthM': 2.0, 'heightM': 1.5, 'yawRad': 0.0,
        }])
        objects = host._fuse_radar4d(existing, msg)
        assert objects[0]['vRel'] == -4.0


class TestRadar4DDropoff:
    def test_dropoff_creates_hard_limit_object(self):
        """A confirmed drop-off hazard must appear as a high-confidence object."""
        host = _FuseHost()
        msg = _radar4d_msg([], drop_off_hazard=True, drop_off_dist_m=7.5)
        objects = host._merge_radar4d_dropoff([], msg)
        assert len(objects) == 1
        assert objects[0]['obstacleType'] == 'dropoff'
        assert objects[0]['dRel'] == 7.5
        assert objects[0]['confidence'] >= 0.9

    def test_dropoff_marks_costmap_hard(self):
        """The hazard must enter the costmap at maximum cost across the corridor."""
        class _Costmap:
            def __init__(self):
                self.calls = []

            def add_obstacle(self, d, y, radius, cost):
                self.calls.append((d, y, radius, cost))

        host = _FuseHost()
        host._active_costmap = _Costmap()
        msg = _radar4d_msg([], drop_off_hazard=True, drop_off_dist_m=7.5)
        host._merge_radar4d_dropoff([], msg)
        assert len(host._active_costmap.calls) >= 5          # corridor-wide
        assert all(c[3] == 1.0 for c in host._active_costmap.calls)  # hard limit

    def test_no_hazard_no_object(self):
        host = _FuseHost()
        msg = _radar4d_msg([], drop_off_hazard=False)
        assert host._merge_radar4d_dropoff([], msg) == []
