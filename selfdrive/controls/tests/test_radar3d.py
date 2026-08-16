import math

from openpilot.selfdrive.controls.radar3d import detection_to_point


def _det_stub(track_id, range_m, vel_mps, azimuth_deg, elevation_deg=0.0, snr_db=20.0):
    """Minimal RadarDetection-shaped object for testing detection_to_point()."""
    class _D:
        pass

    d = _D()
    d.track_id = track_id
    d.range_m = range_m
    d.vel_mps = vel_mps
    d.azimuth_deg = azimuth_deg
    d.elevation_deg = elevation_deg
    d.snr_db = snr_db
    d.is_static = False
    return d


def test_dead_ahead_target():
    # azimuth=0 -> yRel=0, dRel=range
    fields = detection_to_point(_det_stub(track_id=5, range_m=20.0, vel_mps=-3.0, azimuth_deg=0.0))
    assert fields['trackId'] == 5
    assert math.isclose(fields['dRel'], 20.0)
    assert math.isclose(fields['yRel'], 0.0, abs_tol=1e-9)
    assert math.isclose(fields['vRel'], -3.0)
    assert fields['measured'] is True


def test_left_target_positive_yrel():
    # GM formula: yRel = sin(azimuth) * range, left positive.
    fields = detection_to_point(_det_stub(track_id=1, range_m=10.0, vel_mps=0.0, azimuth_deg=30.0))
    assert fields['yRel'] > 0.0
    assert math.isclose(fields['yRel'], math.sin(math.radians(30.0)) * 10.0)


def test_right_target_negative_yrel():
    fields = detection_to_point(_det_stub(track_id=2, range_m=10.0, vel_mps=0.0, azimuth_deg=-30.0))
    assert fields['yRel'] < 0.0
    assert math.isclose(fields['yRel'], math.sin(math.radians(-30.0)) * 10.0)


def test_arel_yvrel_are_nan():
    fields = detection_to_point(_det_stub(track_id=1, range_m=10.0, vel_mps=0.0, azimuth_deg=0.0))
    assert math.isnan(fields['aRel'])
    assert math.isnan(fields['yvRel'])


def test_missing_track_id_defaults_to_zero():
    det = _det_stub(track_id=None, range_m=10.0, vel_mps=0.0, azimuth_deg=0.0)
    fields = detection_to_point(det)
    assert fields['trackId'] == 0


def test_measured_always_true():
    fields = detection_to_point(_det_stub(track_id=1, range_m=10.0, vel_mps=0.0, azimuth_deg=0.0))
    assert fields['measured'] is True
