"""Tests for radar-stereo geometry compatibility."""

import math

import numpy as np
import pytest

from openpilot.selfdrive.controls.radar4d_geometry import (
    RadarMounting,
    radar_polar_to_world,
    world_to_radar_polar,
    RadarStereoGeometry,
)


class TestRadarPolarConversions:
    def test_forward_point(self):
        P = radar_polar_to_world(10.0, 0.0, 0.0)
        assert np.allclose(P, [10.0, 0.0, 0.0])

    def test_left_point(self):
        P = radar_polar_to_world(10.0, 90.0, 0.0)
        assert np.allclose(P, [0.0, 10.0, 0.0], atol=1e-6)

    def test_up_point(self):
        P = radar_polar_to_world(10.0, 0.0, 90.0)
        assert np.allclose(P, [0.0, 0.0, 10.0], atol=1e-6)

    def test_round_trip(self):
        r, az, el = 12.5, 15.0, -3.0
        P = radar_polar_to_world(r, az, el)
        r2, az2, el2 = world_to_radar_polar(P)
        assert abs(r - r2) < 1e-6
        assert abs(az - az2) < 1e-6
        assert abs(el - el2) < 1e-6

    def test_mounting_offset(self):
        mount = RadarMounting(x_m=0.5, y_m=0.1, z_m=0.2)
        P = radar_polar_to_world(10.0, 0.0, 0.0, mount)
        assert np.allclose(P, [10.5, 0.1, 0.2])


class TestRadarMountingStorage:
    def test_save_load_roundtrip(self, tmp_path):
        mount = RadarMounting(x_m=0.12, y_m=-0.02, z_m=0.65, yaw_deg=1.5, pitch_deg=-0.5)
        path = str(tmp_path / "radar_extrinsics.json")
        assert mount.save(path) == path

        loaded = RadarMounting.load(path)
        assert loaded == mount

    def test_load_missing_returns_default(self, tmp_path):
        loaded = RadarMounting.load(str(tmp_path / "nope.json"))
        assert loaded == RadarMounting()

    def test_load_malformed_returns_default(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert RadarMounting.load(str(bad)) == RadarMounting()


class TestRadarStereoGeometry:
    def test_geometry_creation(self):
        geo = RadarStereoGeometry()
        assert geo.cam_geo is not None

    def test_radar_to_stereo_depth_forward(self):
        geo = RadarStereoGeometry()
        pt = geo.radar_to_stereo_depth(10.0, 0.0, 0.0)
        assert pt is not None
        x_cam, y_cam, z_cam = pt
        assert abs(z_cam - 10.0) < 1e-6
        assert abs(x_cam) < 1e-6
        assert abs(y_cam) < 1e-6

    def test_radar_to_stereo_depth_left(self):
        geo = RadarStereoGeometry()
        pt = geo.radar_to_stereo_depth(10.0, 45.0, 0.0)
        assert pt is not None
        x_cam, y_cam, z_cam = pt
        # Radar +azimuth = left = camera -X (right)
        assert x_cam < 0
        assert z_cam > 0

    def test_stereo_depth_to_radar_round_trip(self):
        geo = RadarStereoGeometry()
        cam_pt = geo.radar_to_stereo_depth(8.0, 10.0, 5.0)
        assert cam_pt is not None
        r, az, el = geo.stereo_depth_to_radar(*cam_pt)
        assert abs(r - 8.0) < 1e-6
        assert abs(az - 10.0) < 1e-6
        assert abs(el - 5.0) < 1e-6

    def test_associate_radar_with_stereo(self):
        geo = RadarStereoGeometry()
        # Stereo point at (right=0, down=0, forward=10)
        stereo_pts = np.array([[0.0, 0.0, 10.0]])
        matches = geo.associate_radar_with_stereo(
            [(10.0, 0.0, 0.0)], stereo_pts, gate_m=0.5
        )
        assert matches == [0]

    def test_associate_radar_with_stereo_no_match(self):
        geo = RadarStereoGeometry()
        stereo_pts = np.array([[100.0, 0.0, 100.0]])
        matches = geo.associate_radar_with_stereo(
            [(10.0, 0.0, 0.0)], stereo_pts, gate_m=0.5
        )
        assert matches == [-1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
