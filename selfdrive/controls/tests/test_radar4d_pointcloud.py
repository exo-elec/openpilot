"""Tests for radar4d_pointcloud — clustering + shape estimation."""

from dataclasses import dataclass

import numpy as np
import pytest

from openpilot.selfdrive.controls.radar4d_pointcloud import (
    RadarPointcloudProcessor,
    detections_to_points,
    filter_ground_points,
    dbscan_cluster_indices,
    estimate_cluster_shape,
    estimate_precipitation,
    detect_dropoff,
    classify_weather_severity,
    detect_vision_blocked,
    WiperMotionDetector,
    WindshieldContaminationDetector,
)


@dataclass
class _Det:
    """RadarDetection-shaped stand-in."""
    range_m: float
    vel_mps: float
    azimuth_deg: float
    elevation_deg: float
    snr_db: float
    is_static: bool = False


def _make_det(range_m, azimuth_deg=0.0, elevation_deg=0.0, vel_mps=-1.0, snr_db=20.0):
    return _Det(range_m=range_m, vel_mps=vel_mps, azimuth_deg=azimuth_deg,
                elevation_deg=elevation_deg, snr_db=snr_db)


class TestRadar4DPointcloud:
    def test_detections_to_points_cartesian_conversion(self):
        det = _make_det(range_m=10.0, azimuth_deg=0.0, elevation_deg=0.0)
        pts = detections_to_points([det])
        assert len(pts) == 1
        assert abs(pts[0].x - 10.0) < 0.01
        assert abs(pts[0].y) < 0.01
        assert abs(pts[0].z) < 0.01

    def test_ground_filter_removes_low_elevation(self):
        pts = detections_to_points([
            _make_det(5.0, elevation_deg=-10.0),   # ground
            _make_det(5.0, elevation_deg=2.0),     # above ground
        ])
        filtered = filter_ground_points(pts)
        assert len(filtered) == 1
        assert filtered[0].elevation_deg > 0.0

    def test_dbscan_clusters_nearby_points(self):
        # Two separate groups of points
        pts = detections_to_points([
            _make_det(5.0, azimuth_deg=0.0),
            _make_det(5.1, azimuth_deg=0.0),
            _make_det(5.2, azimuth_deg=0.0),
            _make_det(10.0, azimuth_deg=10.0),
            _make_det(10.1, azimuth_deg=10.0),
        ])
        clusters = dbscan_cluster_indices(pts, eps_m=0.6, min_samples=2)
        assert len(clusters) == 2
        # Cluster sizes
        sizes = sorted([len(c) for c in clusters])
        assert sizes == [2, 3]

    def test_dbscan_ignores_noise(self):
        pts = detections_to_points([
            _make_det(5.0),
            _make_det(5.1),
            _make_det(20.0),  # noise, far away
        ])
        clusters = dbscan_cluster_indices(pts, eps_m=0.6, min_samples=2)
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_shape_estimation_pca_for_small_cluster(self):
        from openpilot.selfdrive.controls.radar4d_pointcloud import RadarCluster, RadarPoint
        pts = [
            RadarPoint(x=5.0, y=0.0, z=0.0, range_m=5.0, azimuth_deg=0.0,
                       elevation_deg=0.0, vel_mps=-2.0, snr_db=20.0, is_static=False),
            RadarPoint(x=5.3, y=0.1, z=0.1, range_m=5.31, azimuth_deg=1.0,
                       elevation_deg=1.0, vel_mps=-2.1, snr_db=22.0, is_static=False),
            RadarPoint(x=5.6, y=0.0, z=0.0, range_m=5.6, azimuth_deg=0.0,
                       elevation_deg=0.0, vel_mps=-1.9, snr_db=21.0, is_static=False),
        ]
        cluster = RadarCluster(cluster_id=0, points=pts)
        estimate_cluster_shape(cluster)
        assert cluster.length_m > cluster.width_m
        assert cluster.height_m > 0.0
        assert cluster.cz > -0.1

    def test_pointcloud_processor_end_to_end(self):
        # Two objects: one close front, one far left
        detections = [
            _make_det(5.0, azimuth_deg=0.0),
            _make_det(5.1, azimuth_deg=1.0),
            _make_det(5.2, azimuth_deg=-1.0),
            _make_det(8.0, azimuth_deg=15.0),
            _make_det(8.1, azimuth_deg=16.0),
        ]
        proc = RadarPointcloudProcessor(eps_m=0.6, min_samples=2, min_points=2)
        clusters = proc.process(detections)
        assert len(clusters) == 2
        for c in clusters:
            assert c.length_m > 0.0
            assert c.width_m > 0.0
            assert c.height_m > 0.0

    def test_pointcloud_processor_ground_filter_on_by_default(self):
        detections = [
            _make_det(5.0, elevation_deg=-10.0),  # ground
            _make_det(5.0, elevation_deg=2.0),
            _make_det(5.1, elevation_deg=2.0),
        ]
        proc = RadarPointcloudProcessor(eps_m=0.6, min_samples=2, min_points=2,
                                   enable_ground_filter=True)
        clusters = proc.process(detections)
        assert len(clusters) == 1

    def test_pointcloud_processor_disabled_ground_filter_keeps_all(self):
        detections = [
            _make_det(5.0, elevation_deg=-10.0),
            _make_det(5.0, elevation_deg=2.0),
            _make_det(5.1, elevation_deg=2.0),
        ]
        proc = RadarPointcloudProcessor(eps_m=0.6, min_samples=2, min_points=2,
                                   enable_ground_filter=False)
        clusters = proc.process(detections)
        assert len(clusters) == 1  # all three are close enough to cluster

    def test_oversized_cluster_split_at_median(self):
        """A merged blob longer than SPLIT_MAX_EXTENT_M (guardrail + vehicle)
        must be bisected along its principal axis, not tracked as one object."""
        # One connected chain along x with eps=0.6, spanning ~10 m.
        detections = [_make_det(4.0 + 0.5 * i, azimuth_deg=0.0) for i in range(21)]
        proc = RadarPointcloudProcessor(eps_m=0.6, min_samples=2, min_points=2,
                                        enable_ground_filter=False)
        clusters = proc.process(detections)
        assert len(clusters) >= 2
        for c in clusters:
            assert c.length_m < 10.0  # no single giant cluster survives

    def test_normal_cluster_not_split(self):
        """A car-sized cluster must remain one object."""
        detections = [_make_det(5.0 + 0.4 * i, azimuth_deg=0.0) for i in range(8)]
        proc = RadarPointcloudProcessor(eps_m=0.6, min_samples=2, min_points=2,
                                        enable_ground_filter=False)
        clusters = proc.process(detections)
        assert len(clusters) == 1


class TestEnvironmentInference:
    def test_precipitation_clear_scene_low(self):
        """A few strong, compact object returns = no precipitation."""
        detections = [_make_det(5.0 + 0.2 * i, azimuth_deg=1.0 * i, snr_db=25.0)
                      for i in range(10)]
        prob = estimate_precipitation(detections_to_points(detections))
        assert prob == 0.0

    def test_precipitation_weak_scattered_high(self):
        """Many weak returns scattered across the FOV = rain/snow signature."""
        detections = [
            _make_det(3.0 + 0.5 * (i % 10), azimuth_deg=-55.0 + 10.0 * (i % 12),
                      snr_db=8.0)
            for i in range(24)
        ]
        prob = estimate_precipitation(detections_to_points(detections))
        assert prob > 0.9

    def test_precipitation_weak_but_compact_not_rain(self):
        """Weak returns concentrated in one direction are clutter, not rain."""
        detections = [_make_det(6.0 + 0.3 * i, azimuth_deg=5.0, snr_db=8.0)
                      for i in range(12)]
        prob = estimate_precipitation(detections_to_points(detections))
        assert prob < 0.3

    def test_precipitation_too_few_points(self):
        detections = [_make_det(5.0, snr_db=8.0), _make_det(6.0, snr_db=8.0)]
        assert estimate_precipitation(detections_to_points(detections)) == 0.0

    def test_dropoff_flat_road_no_hazard(self):
        """Road-level returns (z ≈ -mount height) must NOT trigger the guard."""
        detections = [_make_det(5.0 + i, azimuth_deg=0.0, elevation_deg=-3.0)
                      for i in range(5)]
        pts = detections_to_points(detections)
        # Sanity: these points sit near the road plane, far above the threshold.
        hazard, dist = detect_dropoff(pts, mount_height_m=0.5)
        assert not hazard
        assert dist == 0.0

    def test_dropoff_below_road_triggers(self):
        """Returns far below the road plane ahead = cliff edge / ditch."""
        # At 8 m, elevation -12 deg → z ≈ -1.66 m, well below -(0.5+0.6).
        detections = [_make_det(8.0 + 0.2 * i, azimuth_deg=-2.0 + i,
                                elevation_deg=-12.0)
                      for i in range(3)]
        hazard, dist = detect_dropoff(detections_to_points(detections),
                                      mount_height_m=0.5)
        assert hazard
        assert 7.5 < dist < 9.0

    def test_dropoff_outside_corridor_ignored(self):
        """Below-road returns far off-axis are not the ego path."""
        detections = [_make_det(8.0 + 0.2 * i, azimuth_deg=45.0,
                                elevation_deg=-12.0)
                      for i in range(3)]
        hazard, _ = detect_dropoff(detections_to_points(detections),
                                   mount_height_m=0.5)
        assert not hazard

    def test_dropoff_single_point_not_enough(self):
        detections = [_make_det(8.0, azimuth_deg=0.0, elevation_deg=-12.0)]
        hazard, _ = detect_dropoff(detections_to_points(detections),
                                   mount_height_m=0.5)
        assert not hazard


class TestWiperMotionDetector:
    @staticmethod
    def _frame(sweep: bool):
        # Wiper blade: 2+ close, moving returns.  Quiet frame: one far object.
        dets = ([_make_det(1.0, azimuth_deg=a, snr_db=15.0) for a in (-10.0, 0.0)]
                if sweep else [_make_det(8.0, azimuth_deg=0.0, snr_db=20.0)])
        return detections_to_points(dets)

    def test_periodic_sweeps_detected(self):
        det = WiperMotionDetector(window_frames=50)
        on = False
        for _ in range(3):
            det.update(self._frame(True))
            det.update(self._frame(True))
            on = det.update(self._frame(False))
            det.update(self._frame(False))
        assert on

    def test_single_burst_not_detected(self):
        """One close-range burst (passerby) is not the wiper."""
        det = WiperMotionDetector(window_frames=50)
        det.update(self._frame(True))
        det.update(self._frame(True))
        on = False
        for _ in range(6):
            on = det.update(self._frame(False))
        assert not on

    def test_sweep_rate_hz_reflects_wipe_speed(self):
        """Fast wipe must report a higher sweep rate than intermittent wipe."""
        def run(period_frames: int) -> float:
            det = WiperMotionDetector(window_frames=100, fps=20.0)
            for i in range(100):
                det.update(self._frame(i % period_frames == 0))
            return det.sweep_rate_hz

        assert run(10) > run(40)  # 2 Hz wipe > 0.5 Hz wipe

    def test_static_near_points_ignored(self):
        """Near-range STATIC returns (bumper, parked wall) are not the blade."""
        det = WiperMotionDetector(window_frames=50)
        static_frame = detections_to_points([
            _Det(range_m=1.0, vel_mps=0.0, azimuth_deg=0.0,
                 elevation_deg=0.0, snr_db=20.0, is_static=True),
            _Det(range_m=1.1, vel_mps=0.0, azimuth_deg=5.0,
                 elevation_deg=0.0, snr_db=20.0, is_static=True),
        ])
        on = False
        for _ in range(6):
            on = det.update(static_frame)
        assert not on


class TestWindshieldContaminationDetector:
    @staticmethod
    def _clean_frame():
        # Far targets at full SNR, nothing near — clean glass.
        return detections_to_points([
            _make_det(5.0 + i, azimuth_deg=2.0 * i, snr_db=25.0) for i in range(4)
        ])

    @staticmethod
    def _contaminated_frame():
        # Near-static hot zone (water film rides with the car) + attenuated far targets.
        pts = [
            _Det(range_m=0.8, vel_mps=0.0, azimuth_deg=-3.0,
                 elevation_deg=0.0, snr_db=22.0, is_static=True),
            _Det(range_m=0.9, vel_mps=0.0, azimuth_deg=3.0,
                 elevation_deg=0.0, snr_db=22.0, is_static=True),
        ] + [_make_det(5.0 + i, azimuth_deg=2.0 * i, snr_db=15.0) for i in range(4)]
        return detections_to_points(pts)

    def test_clean_frames_never_contaminated(self):
        det = WindshieldContaminationDetector()
        on = False
        for _ in range(20):
            on = det.update(self._clean_frame())
        assert not on

    def test_film_plus_attenuation_detected(self):
        det = WindshieldContaminationDetector()
        for _ in range(10):
            det.update(self._clean_frame())      # learn the clean baseline
        on = False
        for _ in range(15):
            on = det.update(self._contaminated_frame())
        assert on

    def test_hot_zone_without_attenuation_not_contaminated(self):
        """A near static object with healthy far SNR is just a near object."""
        det = WindshieldContaminationDetector()
        for _ in range(10):
            det.update(self._clean_frame())
        frame = detections_to_points([
            _Det(range_m=0.8, vel_mps=0.0, azimuth_deg=0.0,
                 elevation_deg=0.0, snr_db=22.0, is_static=True),
            _Det(range_m=0.9, vel_mps=0.0, azimuth_deg=3.0,
                 elevation_deg=0.0, snr_db=22.0, is_static=True),
        ] + [_make_det(5.0 + i, azimuth_deg=2.0 * i, snr_db=25.0) for i in range(4)])
        on = False
        for _ in range(15):
            on = det.update(frame)
        assert not on

    def test_attenuation_without_hot_zone_not_contaminated(self):
        """Weak far returns alone (bad weather scene, no film) are not enough."""
        det = WindshieldContaminationDetector()
        for _ in range(10):
            det.update(self._clean_frame())
        weak_far = detections_to_points([
            _make_det(5.0 + i, azimuth_deg=2.0 * i, snr_db=15.0) for i in range(4)
        ])
        on = False
        for _ in range(15):
            on = det.update(weak_far)
        assert not on

    def test_attenuation_db_reports_depth(self):
        """Contamination depth (dB drop) is exposed for severity grading."""
        det = WindshieldContaminationDetector()
        for _ in range(10):
            det.update(self._clean_frame())   # baseline 25 dB
        det.update(self._contaminated_frame())  # far 15 dB → ~10 dB drop
        assert det.attenuation_db > 6.0


class TestWeatherSeverity:
    def test_clear(self):
        assert classify_weather_severity(0.0, False, 0.0, False, 0.0) == 0

    def test_light_from_clutter(self):
        assert classify_weather_severity(0.4, False, 0.0, False, 0.0) == 1

    def test_light_from_intermittent_wipe(self):
        assert classify_weather_severity(0.0, True, 0.1, False, 0.0) == 1

    def test_moderate_from_fast_wipe(self):
        assert classify_weather_severity(0.2, True, 1.0, False, 0.0) == 2

    def test_moderate_from_contamination(self):
        assert classify_weather_severity(0.0, False, 0.0, True, 8.0) == 2

    def test_heavy_from_deep_attenuation(self):
        assert classify_weather_severity(0.0, False, 0.0, True, 15.0) == 3

    def test_heavy_from_fast_wipe_plus_heavy_clutter(self):
        assert classify_weather_severity(0.8, True, 1.0, False, 0.0) == 3

    def test_levels_monotonic_with_clutter(self):
        levels = [classify_weather_severity(p, False, 0.0, False, 0.0)
                  for p in (0.0, 0.4, 0.7)]
        assert levels == [0, 1, 2]


class TestVisionBlocked:
    def test_clear_not_blocked(self):
        assert not detect_vision_blocked(False, 0.0)

    def test_contaminated_but_seeing_not_blocked(self):
        # heavy attenuation yet far returns still exist → severity 3, not blind
        assert not detect_vision_blocked(True, 15.0)

    def test_far_field_void_is_blocked(self):
        # attenuation_db = 99.0 = far returns vanished entirely
        assert detect_vision_blocked(True, 99.0)

    def test_void_without_contamination_not_blocked(self):
        # empty road (no roadside clutter) is not a blockage
        assert not detect_vision_blocked(False, 99.0)

    def test_full_detector_path_to_blocked(self):
        det = WindshieldContaminationDetector()
        for _ in range(12):
            det.update(TestWindshieldContaminationDetector._clean_frame())  # learn baseline
        # near-static hot zone + far void (no returns past CONTAM_FAR_MIN_M)
        film = detections_to_points([
            _Det(range_m=0.5, vel_mps=0.0, azimuth_deg=-3.0,
                 elevation_deg=0.0, snr_db=25.0, is_static=True),
            _Det(range_m=0.8, vel_mps=0.0, azimuth_deg=3.0,
                 elevation_deg=0.0, snr_db=22.0, is_static=True),
        ])
        contaminated = False
        for _ in range(15):
            contaminated = det.update(film)
        assert contaminated
        assert detect_vision_blocked(contaminated, det.attenuation_db)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
