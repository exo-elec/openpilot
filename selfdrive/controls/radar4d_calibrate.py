#!/usr/bin/env python3
"""
BGT60TR13C intrinsic calibration wizard — openpilot port of DISP_RADAR4D.

Guides the operator through placing a corner-reflector / ping-pong ball at
known (x, y, z) positions, captures the strongest radar detection at each
position, bins the measurements into a range/azimuth/elevation LUT, and
writes the JSON file that radar4d.py loads at startup.

Usage:
    python3 selfdrive/controls/radar4d_calibrate.py [--output PATH] [--mock]

The wizard is interactive: it prints instructions and waits for Enter before
each capture.  Positions should cover the radar's field of view; a typical
automotive set is:
    - 4 distances: 3 m, 6 m, 9 m, 12 m
    - 5 azimuths per distance: -30°, -15°, 0°, +15°, +30°
    - 3 elevations at boresight: -5°, 0°, +5°

Output JSON schema (same as radar4d.py's _load_intrinsics):
    {
        "range_committed": true,
        "az_committed": true,
        "el_committed": true,
        "range_band_meas": [r1, r2, r3, r4],
        "range_band_true": [r1, r2, r3, r4],
        "az_col_meas": [az1, ..., az8],
        "az_cell_true": [[...], [...], [...], [...]],
        "el_col_meas": [el1, ..., el8],
        "el_cell_true": [[...], [...], [...], [...]]
    }
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field

import numpy as np

from openpilot.system.hardware.hw import Paths

try:
    from hal.drivers.radar import RadarIntrinsics, save_radar_intrinsics
    HAL_AVAILABLE = True
except ImportError:
    HAL_AVAILABLE = False
    RadarIntrinsics = None  # type: ignore
    save_radar_intrinsics = None  # type: ignore

# Automotive range bands (m): edges at 3/6/9/12 m, bands centered in between.
# Replaces DISP_RADAR4D's indoor 1.5/2.5/3.5 m bands.
RANGE_BAND_EDGES_M = (3.0, 6.0, 9.0, 12.0)
RANGE_BAND_CENTERS_M = (1.5, 4.5, 7.5, 10.5)  # for display only
NUM_RANGE_BANDS = 4
NUM_ANGLE_BINS = 8

# Default output path (same as radar4d.py)
DEFAULT_OUTPUT = os.path.join(
    Paths.eop_data_root(), "calibration", "radar_intrinsics.json"
)


@dataclass
class CapturePoint:
    """One ground-truth capture."""
    x_m: float          # forward
    y_m: float          # left
    z_m: float          # up
    range_m: float      # measured range
    azimuth_deg: float  # measured azimuth
    elevation_deg: float  # measured elevation
    snr_db: float


@dataclass
class IntrinsicLUT:
    """Range/azimuth/elevation correction LUT."""
    range_band_meas: list[float] = field(default_factory=list)
    range_band_true: list[float] = field(default_factory=list)
    az_col_meas: list[float] = field(default_factory=list)
    az_cell_true: list[list[float]] = field(default_factory=list)
    el_col_meas: list[float] = field(default_factory=list)
    el_cell_true: list[list[float]] = field(default_factory=list)
    range_committed: bool = False
    az_committed: bool = False
    el_committed: bool = False


class RadarCalibrator:
    """Interactive intrinsic calibration wizard."""

    def __init__(self, output_path: str = DEFAULT_OUTPUT, mock: bool = False):
        self.output_path = output_path
        self.mock = mock
        self.captures: list[CapturePoint] = []
        self._radar = None

    def _init_radar(self):
        """Initialize radar hardware (skipped in mock mode)."""
        if self.mock:
            print("[mock] radar hardware not initialized")
            return
        try:
            from hal.drivers.radar import BGT60TR13C, BGT60Config
            from hal.platform.rk3588_pins import GPIO as RK3588_GPIO, SPI as RK3588_SPI

            spi = RK3588_SPI["RADAR4D"]
            irq = RK3588_GPIO["BGT60_IRQ"]
            rst = RK3588_GPIO["BGT60_RST"]
            config = BGT60Config.for_20hz()
            config.frame_rate_hz = 20.0
            config.use_gpu = True
            self._radar = BGT60TR13C(
                spi_bus=spi["bus"], spi_device=spi["device"],
                irq_chip='/dev/gpiochip0', irq_line=irq["num"],
                rst_chip='/dev/gpiochip0', rst_line=rst["num"],
                config=config,
            )
            self._radar.reset()
            self._radar.configure()
            print("Radar initialized")
        except Exception as e:
            print(f"Failed to initialize radar: {e}")
            print("Run with --mock to test without hardware")
            sys.exit(1)

    def _capture_detection(self) -> tuple[float, float, float, float] | None:
        """Capture one radar detection (range, az, el, snr).

        Returns None if no detection above threshold.
        """
        if self.mock:
            # Return a synthetic detection near the expected position
            return (5.0, 0.0, 0.0, 25.0)

        raw = self._radar.read_fifo(timeout_s=0.5)
        detections = self._radar.process(raw)
        if not detections:
            return None

        # Pick strongest detection
        det = max(detections, key=lambda d: d.snr_db)
        return (det.range_m, det.azimuth_deg, det.elevation_deg, det.snr_db)

    def _prompt_position(self) -> tuple[float, float, float] | None:
        """Prompt operator for ground-truth position."""
        print("\nEnter target position in meters (or 'q' to finish):")
        try:
            x = input("  x (forward, 0-15m): ").strip()
            if x.lower() == 'q':
                return None
            y = input("  y (left, -5..+5m): ").strip()
            z = input("  z (up, -1..+2m): ").strip()
            return (float(x), float(y), float(z))
        except ValueError:
            print("Invalid input, try again")
            return self._prompt_position()

    def run(self):
        """Run the interactive calibration wizard."""
        print("=" * 60)
        print("BGT60TR13C Intrinsic Calibration Wizard")
        print("=" * 60)
        print("\nPlace a corner reflector or ping-pong ball at each prompted")
        print("position and press Enter to capture.  Cover the full field of")
        print("view: multiple ranges (3/6/9/12m) and azimuths (-30°..+30°).")
        print("\nPress Ctrl+C to abort at any time.")

        self._init_radar()

        try:
            while True:
                pos = self._prompt_position()
                if pos is None:
                    break
                x, y, z = pos

                print(f"Capturing at ({x:.2f}, {y:.2f}, {z:.2f})...")
                det = self._capture_detection()
                if det is None:
                    print("  No detection — check target placement and try again")
                    continue

                range_m, az, el, snr = det
                print(f"  Detected: range={range_m:.2f}m az={az:.1f}° el={el:.1f}° snr={snr:.1f}dB")

                self.captures.append(CapturePoint(
                    x_m=x, y_m=y, z_m=z,
                    range_m=range_m, azimuth_deg=az, elevation_deg=el,
                    snr_db=snr,
                ))
                print(f"  Captured {len(self.captures)} points")

                again = input("Capture another point? [Y/n]: ").strip().lower()
                if again == 'n':
                    break

        except KeyboardInterrupt:
            print("\nAborted by user")
            return

        if len(self.captures) < 8:
            print(f"\nOnly {len(self.captures)} points captured — need at least 8 for a useful LUT")
            return

        print(f"\nBuilding LUT from {len(self.captures)} captures...")
        lut = self._build_lut()
        self._write_lut(lut)
        print(f"Calibration written to {self.output_path}")

    def _build_lut(self) -> IntrinsicLUT:
        """Build the range/azimuth/elevation correction LUT from captures."""
        lut = IntrinsicLUT()

        # ---- Range calibration (1D, 4 bands) ----
        # Bin captures by measured range into 4 bands
        range_bins: list[list[tuple[float, float]]] = [[] for _ in range(NUM_RANGE_BANDS)]
        for cap in self.captures:
            # Find which band this measured range falls into
            for i, edge in enumerate(RANGE_BAND_EDGES_M):
                if cap.range_m < edge:
                    range_bins[i].append((cap.range_m, self._true_range(cap)))
                    break
            else:
                range_bins[-1].append((cap.range_m, self._true_range(cap)))

        for i, bin_pts in enumerate(range_bins):
            if bin_pts:
                meas = np.mean([p[0] for p in bin_pts])
                true = np.mean([p[1] for p in bin_pts])
            else:
                # Empty band: use edge values as identity
                meas = RANGE_BAND_EDGES_M[i]
                true = RANGE_BAND_EDGES_M[i]
            lut.range_band_meas.append(float(meas))
            lut.range_band_true.append(float(true))
        lut.range_committed = True

        # ---- Azimuth calibration (2D: range band × angle bin) ----
        # Adaptive bins spanning captured azimuth range
        az_values = [c.azimuth_deg for c in self.captures]
        az_min, az_max = min(az_values), max(az_values)
        az_edges = np.linspace(az_min, az_max, NUM_ANGLE_BINS + 1)

        lut.az_col_meas = [float((az_edges[i] + az_edges[i+1]) / 2.0) for i in range(NUM_ANGLE_BINS)]
        lut.az_cell_true = []

        for band_idx in range(NUM_RANGE_BANDS):
            row = []
            for bin_idx in range(NUM_ANGLE_BINS):
                # Find captures in this range band and azimuth bin
                bin_lo, bin_hi = az_edges[bin_idx], az_edges[bin_idx+1]
                pts = [
                    c for c in self.captures
                    if self._range_band_index(c.range_m) == band_idx
                    and bin_lo <= c.azimuth_deg < bin_hi
                ]
                if pts:
                    true_az = np.mean([self._true_azimuth(c) for c in pts])
                else:
                    # Nearest-neighbor fill: use bin center as identity
                    true_az = (bin_lo + bin_hi) / 2.0
                row.append(float(true_az))
            lut.az_cell_true.append(row)
        lut.az_committed = True

        # ---- Elevation calibration (2D: range band × angle bin) ----
        el_values = [c.elevation_deg for c in self.captures]
        el_min, el_max = min(el_values), max(el_values)
        el_edges = np.linspace(el_min, el_max, NUM_ANGLE_BINS + 1)

        lut.el_col_meas = [float((el_edges[i] + el_edges[i+1]) / 2.0) for i in range(NUM_ANGLE_BINS)]
        lut.el_cell_true = []

        for band_idx in range(NUM_RANGE_BANDS):
            row = []
            for bin_idx in range(NUM_ANGLE_BINS):
                bin_lo, bin_hi = el_edges[bin_idx], el_edges[bin_idx+1]
                pts = [
                    c for c in self.captures
                    if self._range_band_index(c.range_m) == band_idx
                    and bin_lo <= c.elevation_deg < bin_hi
                ]
                if pts:
                    true_el = np.mean([self._true_elevation(c) for c in pts])
                else:
                    true_el = (bin_lo + bin_hi) / 2.0
                row.append(float(true_el))
            lut.el_cell_true.append(row)
        lut.el_committed = True

        return lut

    def _true_range(self, cap: CapturePoint) -> float:
        """Ground-truth range from Cartesian position."""
        return math.sqrt(cap.x_m**2 + cap.y_m**2 + cap.z_m**2)

    def _true_azimuth(self, cap: CapturePoint) -> float:
        """Ground-truth azimuth from Cartesian position."""
        return math.degrees(math.atan2(cap.y_m, cap.x_m))

    def _true_elevation(self, cap: CapturePoint) -> float:
        """Ground-truth elevation from Cartesian position."""
        r_xy = math.hypot(cap.x_m, cap.y_m)
        return math.degrees(math.atan2(cap.z_m, r_xy))

    def _range_band_index(self, range_m: float) -> int:
        """Which range band does this measured range fall into?"""
        for i, edge in enumerate(RANGE_BAND_EDGES_M):
            if range_m < edge:
                return i
        return NUM_RANGE_BANDS - 1

    def _write_lut(self, lut: IntrinsicLUT):
        """Write the LUT to JSON via the exopilot HAL store (fallback: inline)."""
        if HAL_AVAILABLE:
            cal = RadarIntrinsics(
                range_band_meas=tuple(lut.range_band_meas),
                range_band_true=tuple(lut.range_band_true),
                az_col_meas=tuple(lut.az_col_meas),
                az_cell_true=tuple(tuple(row) for row in lut.az_cell_true),
                el_col_meas=tuple(lut.el_col_meas),
                el_cell_true=tuple(tuple(row) for row in lut.el_cell_true),
                range_committed=lut.range_committed,
                az_committed=lut.az_committed,
                el_committed=lut.el_committed,
            )
            save_radar_intrinsics(cal, self.output_path)
            return
        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
        data = {
            "range_committed": lut.range_committed,
            "az_committed": lut.az_committed,
            "el_committed": lut.el_committed,
            "range_band_meas": lut.range_band_meas,
            "range_band_true": lut.range_band_true,
            "az_col_meas": lut.az_col_meas,
            "az_cell_true": lut.az_cell_true,
            "el_col_meas": lut.el_col_meas,
            "el_cell_true": lut.el_cell_true,
        }
        with open(self.output_path, "w") as f:
            json.dump(data, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="BGT60TR13C intrinsic calibration wizard")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path")
    parser.add_argument("--mock", action="store_true", help="Run without radar hardware")
    args = parser.parse_args()

    cal = RadarCalibrator(output_path=args.output, mock=args.mock)
    cal.run()


if __name__ == "__main__":
    main()
