"""Tests for radar4d calibration wizard LUT building."""

import json
import math
import os
import tempfile

import pytest

from openpilot.selfdrive.controls.radar4d_calibrate import (
    CapturePoint,
    IntrinsicLUT,
    RadarCalibrator,
    RANGE_BAND_EDGES_M,
    NUM_RANGE_BANDS,
    NUM_ANGLE_BINS,
)


def _capture(x, y, z, range_m, az, el, snr=20.0):
    return CapturePoint(x_m=x, y_m=y, z_m=z,
                        range_m=range_m, azimuth_deg=az,
                        elevation_deg=el, snr_db=snr)


class TestRadarCalibrator:
    def test_true_range(self):
        cal = RadarCalibrator(mock=True)
        cap = _capture(3.0, 4.0, 0.0, 5.1, 53.0, 0.0)
        assert abs(cal._true_range(cap) - 5.0) < 0.01

    def test_true_azimuth(self):
        cal = RadarCalibrator(mock=True)
        cap = _capture(3.0, 4.0, 0.0, 5.1, 53.0, 0.0)
        assert abs(cal._true_azimuth(cap) - 53.13) < 0.1

    def test_true_elevation(self):
        cal = RadarCalibrator(mock=True)
        cap = _capture(3.0, 0.0, 4.0, 5.1, 0.0, 53.0)
        assert abs(cal._true_elevation(cap) - 53.13) < 0.1

    def test_range_band_index(self):
        cal = RadarCalibrator(mock=True)
        assert cal._range_band_index(1.0) == 0
        assert cal._range_band_index(4.0) == 1
        assert cal._range_band_index(7.0) == 2
        assert cal._range_band_index(10.0) == 3
        assert cal._range_band_index(15.0) == 3

    def test_build_lut_empty_bands_get_identity(self):
        """Bands with no captures should get identity mapping."""
        cal = RadarCalibrator(mock=True)
        # Only capture in band 0 (range < 3m)
        cal.captures = [
            _capture(2.0, 0.0, 0.0, 2.1, 0.0, 0.0),
            _capture(2.5, 0.5, 0.0, 2.6, 11.0, 0.0),
        ]
        lut = cal._build_lut()
        assert lut.range_committed
        # Band 0 has data
        assert lut.range_band_meas[0] != RANGE_BAND_EDGES_M[0]
        # Bands 1-3 are identity
        for i in range(1, NUM_RANGE_BANDS):
            assert lut.range_band_meas[i] == RANGE_BAND_EDGES_M[i]
            assert lut.range_band_true[i] == RANGE_BAND_EDGES_M[i]

    def test_build_lut_azimuth_grid(self):
        cal = RadarCalibrator(mock=True)
        # Captures across azimuth at one range
        cal.captures = [
            _capture(5.0, -2.0, 0.0, 5.4, -21.8, 0.0),
            _capture(5.0, 0.0, 0.0, 5.0, 0.0, 0.0),
            _capture(5.0, 2.0, 0.0, 5.4, 21.8, 0.0),
            _capture(5.0, -1.0, 0.0, 5.1, -11.3, 0.0),
            _capture(5.0, 1.0, 0.0, 5.1, 11.3, 0.0),
            _capture(5.0, -0.5, 0.0, 5.05, -5.7, 0.0),
            _capture(5.0, 0.5, 0.0, 5.05, 5.7, 0.0),
            _capture(5.0, -1.5, 0.0, 5.2, -16.7, 0.0),
        ]
        lut = cal._build_lut()
        assert lut.az_committed
        assert len(lut.az_col_meas) == NUM_ANGLE_BINS
        assert len(lut.az_cell_true) == NUM_RANGE_BANDS
        assert len(lut.az_cell_true[0]) == NUM_ANGLE_BINS

    def test_write_lut_creates_valid_json(self):
        cal = RadarCalibrator(mock=True)
        with tempfile.TemporaryDirectory() as tmp:
            cal.output_path = os.path.join(tmp, "cal.json")
            cal.captures = [
                _capture(2.0, 0.0, 0.0, 2.1, 0.0, 0.0),
                _capture(5.0, 0.0, 0.0, 5.1, 0.0, 0.0),
                _capture(8.0, 0.0, 0.0, 8.1, 0.0, 0.0),
                _capture(11.0, 0.0, 0.0, 11.1, 0.0, 0.0),
                _capture(5.0, 1.0, 0.0, 5.1, 11.3, 0.0),
                _capture(5.0, -1.0, 0.0, 5.1, -11.3, 0.0),
                _capture(5.0, 0.0, 1.0, 5.1, 0.0, 11.3),
                _capture(5.0, 0.0, -1.0, 5.1, 0.0, -11.3),
            ]
            lut = cal._build_lut()
            cal._write_lut(lut)

            assert os.path.exists(cal.output_path)
            with open(cal.output_path) as f:
                data = json.load(f)
            assert data["range_committed"]
            assert data["az_committed"]
            assert data["el_committed"]
            assert len(data["range_band_meas"]) == NUM_RANGE_BANDS
            assert len(data["az_cell_true"]) == NUM_RANGE_BANDS
            assert len(data["az_cell_true"][0]) == NUM_ANGLE_BINS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
