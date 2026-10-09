#!/usr/bin/env python3
"""
Auto Tile Manager for OpenPilot (Offline)
Automatically manages tiles based on GPS location and WiFi connectivity.
"""

import os
import time
import threading
from dataclasses import dataclass
from typing import cast

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.navd.tile_manager import TileManager, TILE_SOURCES

# Default WiFi-only download threshold (MB)
WIFI_ONLY_THRESHOLD_MB = 100

# Default check interval (seconds)
CHECK_INTERVAL = 300  # 5 minutes

# Auto-download radius (km) - download regions within this range
AUTO_DOWNLOAD_RADIUS_KM = 200

# Tile update interval (days)
TILE_UPDATE_INTERVAL_DAYS = 30


@dataclass
class GPSPosition:
    lat: float
    lon: float
    accuracy: float


class AutoTileManager:
    """
    Automatically manages offline tiles:
    1. Detects current region from GPS
    2. Downloads tiles when on WiFi (not cellular)
    3. Checks for tile updates weekly
    4. Handles multi-region for long trips
    """

    def __init__(self):
        self.tm = TileManager()
        self.params = Params()
        self.sm = messaging.SubMaster(['gpsLocationExternal', 'fusedPosition'])

        # Current region tracking
        self.current_region: str | None = None
        self.nearby_regions: list[str] = []

        # Download state
        self.download_thread: threading.Thread | None = None
        self.is_downloading = False

        # Last check time
        self.last_check_time = 0

        cloudlog.info("AutoTileManager initialized")

    def get_current_position(self) -> GPSPosition | None:
        """Get current GPS position."""
        self.sm.update(0)

        # Prefer fused position
        if self.sm.updated['fusedPosition']:
            fused = self.sm['fusedPosition']
            if fused.confidence > 0.3:
                return GPSPosition(
                    lat=fused.positionGeodetic.latitude,
                    lon=fused.positionGeodetic.longitude,
                    accuracy=10.0  # fused is more accurate
                )

        # Fallback to raw GPS
        if self.sm.updated['gpsLocationExternal']:
            gps = self.sm['gpsLocationExternal']
            if gps.accuracy > 0:
                return GPSPosition(
                    lat=gps.latitude,
                    lon=gps.longitude,
                    accuracy=gps.accuracy
                )

        return None

    def find_region_for_position(self, pos: GPSPosition) -> str | None:
        """
        Find which region a GPS position belongs to.
        Uses bounding box approximation for major regions.
        """
        # Simple bounding boxes for major regions
        # Format: (min_lat, max_lat, min_lon, max_lon, region_name)
        REGIONS_BBOX = [
            # Asia
            (5.0, 21.0, 97.0, 106.0, "thailand"),
            (1.0, 7.5, 100.0, 119.0, "malaysia-singapore"),
            (24.0, 46.0, 122.0, 146.0, "japan"),
            (33.0, 39.0, 124.0, 132.0, "south-korea"),
            (21.8, 25.4, 119.0, 122.0, "taiwan"),
            # Europe
            (47.0, 55.0, 5.0, 15.0, "germany"),
            (41.0, 51.0, -5.0, 10.0, "france"),
            (49.0, 59.0, -8.0, 2.0, "uk"),
            (36.5, 47.0, 6.5, 18.5, "italy"),
            (35.0, 44.0, -10.0, 4.0, "spain"),
            # North America
            (32.5, 42.0, -124.5, -114.0, "california"),
            (25.8, 36.5, -107.0, -93.5, "texas"),
            (24.5, 31.0, -87.5, -80.0, "florida"),
            (40.5, 45.0, -79.8, -71.8, "new-york"),
        ]

        for min_lat, max_lat, min_lon, max_lon, region in REGIONS_BBOX:
            if min_lat <= pos.lat <= max_lat and min_lon <= pos.lon <= max_lon:
                return region

        return None

    def is_on_wifi(self) -> bool:
        """Check if connected to WiFi (not cellular)."""
        try:
            # Read WiFi SSID directly from /sys to avoid shell spawn
            for iface in ('wlan0', 'wlp2s0', 'wlx'):
                ssid_path = f"/sys/class/net/{iface}/wireless/essid"
                if os.path.exists(ssid_path):
                    with open(ssid_path, 'rb') as f:
                        ssid = f.read().strip().strip(b'\x00')
                        return len(ssid) > 0
            # Fallback: check if iwgetid is available
            import shutil
            if shutil.which("iwgetid"):
                result = os.popen("iwgetid -r 2>/dev/null || echo ''").read().strip()
                return len(result) > 0
        except Exception:
            pass
        return False

    def get_tile_age_days(self, region: str) -> float:
        """Get age of tile file in days."""
        tar_file = self.tm.tile_dir / "valhalla_tiles.tar"
        if not tar_file.exists():
            return float('inf')

        mtime = tar_file.stat().st_mtime
        age_seconds = time.time() - mtime  # noqa: TID251
        return cast(float, age_seconds / (24 * 3600))

    def should_auto_download(self, region: str) -> bool:
        """Check if we should auto-download this region."""
        # Check if auto-tile is enabled
        if not self.params.get_bool("EOPAutoTileEnabled"):
            return False

        if region not in TILE_SOURCES:
            return False

        info = TILE_SOURCES[region]

        # Check if already have recent tiles
        if self.tm.get_status(region).is_ready:
            age = self.get_tile_age_days(region)
            if age < TILE_UPDATE_INTERVAL_DAYS:
                return False  # Have fresh tiles

        # Check WiFi-only setting
        wifi_only = self.params.get_bool("EOPAutoTileWifiOnly")
        tile_size_mb = cast(float, info['tiles_size_mb'])
        if wifi_only and tile_size_mb > WIFI_ONLY_THRESHOLD_MB:
            if not self.is_on_wifi():
                cloudlog.info(f"AutoTile: {region} is large ({tile_size_mb} MB), waiting for WiFi")
                return False

        return True

    def download_in_background(self, region: str):
        """Download tiles in background thread."""
        if self.is_downloading:
            return

        def download():
            self.is_downloading = True
            try:
                cloudlog.info(f"AutoTile: Starting background download for {region}")
                success = self.tm.ensure_tiles(region)
                if success:
                    cloudlog.info(f"AutoTile: Successfully downloaded {region}")
                    self.current_region = region
                else:
                    cloudlog.error(f"AutoTile: Failed to download {region}")
            except Exception as e:
                cloudlog.exception(f"AutoTile: Download error: {e}")
            finally:
                self.is_downloading = False

        self.download_thread = threading.Thread(target=download, daemon=True)
        self.download_thread.start()

    def check_and_update(self):
        """Main check loop - call periodically."""
        # Rate limit checks
        now = time.monotonic()
        if now - self.last_check_time < CHECK_INTERVAL:
            return
        self.last_check_time = now

        # Get current position
        pos = self.get_current_position()
        if not pos:
            return

        # Find current region
        region = self.find_region_for_position(pos)
        if not region:
            cloudlog.debug(f"AutoTile: Position ({pos.lat:.2f}, {pos.lon:.2f}) not in known region")
            return

        # Check if we need to download
        if self.should_auto_download(region):
            if not self.is_downloading:
                cloudlog.info(f"AutoTile: Detected region {region}, starting download")
                self.download_in_background(region)

        # Update current region for UI
        if region != self.current_region and not self.is_downloading:
            self.current_region = region

    def get_status(self) -> dict:
        """Get current status for UI display."""
        pos = self.get_current_position()
        region = self.find_region_for_position(pos) if pos else None

        status = {
            'current_region': region,
            'detected_position': (pos.lat, pos.lon) if pos else None,
            'is_downloading': self.is_downloading,
            'on_wifi': self.is_on_wifi(),
            'tiles_ready': False,
            'tile_age_days': None,
        }

        if region:
            tile_status = self.tm.get_status(region)
            status['tiles_ready'] = tile_status.is_ready
            if tile_status.is_ready:
                status['tile_age_days'] = self.get_tile_age_days(region)

        return status


def main():
    """Run auto tile manager as standalone daemon."""
    import argparse

    parser = argparse.ArgumentParser(description="Auto Tile Manager")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    args = parser.parse_args()

    atm = AutoTileManager()

    if args.daemon:
        cloudlog.info("AutoTileManager daemon started")
        while True:
            atm.check_and_update()
            time.sleep(60)  # Check every minute
    else:
        # One-time status check
        status = atm.get_status()
        print("\nAuto Tile Manager Status:")
        print(f"  Current region: {status['current_region'] or 'Unknown'}")
        print(f"  Position: {status['detected_position']}")
        print(f"  Tiles ready: {status['tiles_ready']}")
        print(f"  On WiFi: {status['on_wifi']}")
        print(f"  Downloading: {status['is_downloading']}")
        if status['tile_age_days']:
            print(f"  Tile age: {status['tile_age_days']:.1f} days")


if __name__ == "__main__":
    main()
