import numpy as np

from openpilot.selfdrive.controls.radar4d import Radar4DD, _is_crossing_ghost


def _track_stub(azimuth_deg: float, elevation_deg: float):
    """Minimal Track-shaped object for testing the calibration helper."""
    class _T:
        pass

    t = _T()
    t.azimuth_deg = azimuth_deg
    t.elevation_deg = elevation_deg
    return t


def _motion_stub(x: float, y: float, vx: float, vy: float):
    """Minimal Track-shaped object for the crossing-yaw ghost filter."""
    class _T:
        pass

    t = _T()
    t.x, t.y, t.vx, t.vy = x, y, vx, vy
    return t


class TestCrossingGhostFilter:
    def test_tangential_fast_track_is_ghost(self):
        """Ego-turn ghost: fast motion perpendicular to the bearing."""
        # Object dead ahead (bearing +x), moving purely laterally at 3 m/s.
        assert _is_crossing_ghost(_motion_stub(10.0, 0.0, 0.0, 3.0))

    def test_radial_fast_track_kept(self):
        """A genuine closing target moves along its bearing — not a ghost."""
        assert not _is_crossing_ghost(_motion_stub(10.0, 0.0, -5.0, 0.0))

    def test_slow_tangential_track_kept(self):
        """Below the speed gate even pure tangential motion is kept
        (pedestrians crossing in front are slow and real)."""
        assert not _is_crossing_ghost(_motion_stub(5.0, 0.0, 0.0, 1.0))

    def test_diagonal_motion_kept(self):
        """Motion 45 deg to the bearing is plausible — not tangential enough."""
        assert not _is_crossing_ghost(_motion_stub(10.0, 0.0, -3.0, 3.0))

    def test_zero_range_no_crash(self):
        assert not _is_crossing_ghost(_motion_stub(0.0, 0.0, 0.0, 5.0))


def test_apply_calibration_identity_when_none():
    t = _track_stub(10.0, 5.0)
    az, el = Radar4DD._apply_calibration(t, None)
    assert az == 10.0
    assert el == 5.0


def test_apply_calibration_pitch_up_corrects_elevation():
    """If the device is pitched up by 5 deg (rpyCalib pitch = -5 deg),
    a forward point is 5 deg below the calibrated horizon."""
    t = _track_stub(0.0, 0.0)
    rpy = np.array([0.0, np.radians(-5.0), 0.0])
    from openpilot.common.transformations.orientation import rot_from_euler
    calib_from_device = rot_from_euler(rpy).T
    az, el = Radar4DD._apply_calibration(t, calib_from_device)
    assert abs(az) < 0.01
    assert abs(el - (-5.0)) < 0.01


def test_apply_calibration_yaw_left_corrects_azimuth():
    """If the device is yawed left by 3 deg, a forward point is 3 deg right
    of calibrated boresight, so calibrated azimuth = device_az - yaw."""
    t = _track_stub(0.0, 0.0)
    rpy = np.array([0.0, 0.0, np.radians(3.0)])
    from openpilot.common.transformations.orientation import rot_from_euler
    calib_from_device = rot_from_euler(rpy).T
    az, el = Radar4DD._apply_calibration(t, calib_from_device)
    assert abs(az - (-3.0)) < 0.01
    assert abs(el) < 0.01


class TestRadarEgoVelocity:
    """Radar-Doppler ego velocity via the shared HAL estimator."""

    @staticmethod
    def _static_scene(v_ego_mps: float, n: int = 12):
        """n static returns for an ego moving forward at v_ego_mps.

        Static model: measured Doppler d = -u . v_ego, with u the bearing
        unit vector — so a forward-moving ego sees d = -v_ego*cos(az).
        """
        class _D:
            pass

        dets = []
        for i in range(n):
            az = -40.0 + 80.0 * i / max(1, n - 1)
            d = _D()
            d.azimuth_deg = az
            d.elevation_deg = 0.0
            d.vel_mps = -v_ego_mps * float(np.cos(np.radians(az)))
            dets.append(d)
        return dets

    def test_too_few_points_returns_none(self):
        assert Radar4DD._estimate_radar_ego_velocity(self._static_scene(10.0, n=2)) is None

    def test_recovers_known_forward_speed(self):
        try:
            from hal.drivers.radar import estimate_ego_velocity_gnc, estimate_ego_velocity  # noqa: F401
        except ImportError:
            # HAL not installed on this test runner; skip.
            return

        vx = Radar4DD._estimate_radar_ego_velocity(self._static_scene(10.0))
        assert vx is not None
        assert abs(vx - 10.0) < 0.5

    def test_stationary_scene_near_zero(self):
        try:
            from hal.drivers.radar import estimate_ego_velocity_gnc, estimate_ego_velocity  # noqa: F401
        except ImportError:
            return

        vx = Radar4DD._estimate_radar_ego_velocity(self._static_scene(0.0))
        assert vx is not None
        assert abs(vx) < 0.5
