#!/usr/bin/env python3
"""
Valhalla Tile Manager for ExoPilot 01M (RK3588)
Manages regional OSM tile downloads and Valhalla tile builds.

This is NOT a git submodule - tiles are downloaded at runtime
to /data/media/0/valhalla/ (SD card storage).

Usage:
    python tile_manager.py download thailand
    python tile_manager.py build thailand
    python tile_manager.py status
"""

import os
import sys
import json
import hashlib
import urllib.request
import subprocess
from pathlib import Path
from dataclasses import dataclass

# Regional OSM extracts from Geofabrik
TILE_SOURCES = {
    # Asia
    "thailand": {
        "url": "https://download.geofabrik.de/asia/thailand-latest.osm.pbf",
        "size_mb": 150,
        "tiles_size_mb": 300,
    },
    "malaysia-singapore": {
        "url": "https://download.geofabrik.de/asia/malaysia-singapore-brunei-latest.osm.pbf",
        "size_mb": 80,
        "tiles_size_mb": 150,
    },
    "japan": {
        "url": "https://download.geofabrik.de/asia/japan-latest.osm.pbf",
        "size_mb": 1200,
        "tiles_size_mb": 2500,
    },
    "south-korea": {
        "url": "https://download.geofabrik.de/asia/south-korea-latest.osm.pbf",
        "size_mb": 100,
        "tiles_size_mb": 200,
    },
    "taiwan": {
        "url": "https://download.geofabrik.de/asia/taiwan-latest.osm.pbf",
        "size_mb": 60,
        "tiles_size_mb": 120,
    },
    # Europe
    "germany": {
        "url": "https://download.geofabrik.de/europe/germany-latest.osm.pbf",
        "size_mb": 3500,
        "tiles_size_mb": 8000,
    },
    "france": {
        "url": "https://download.geofabrik.de/europe/france-latest.osm.pbf",
        "size_mb": 4000,
        "tiles_size_mb": 9000,
    },
    "uk": {
        "url": "https://download.geofabrik.de/europe/great-britain-latest.osm.pbf",
        "size_mb": 1800,
        "tiles_size_mb": 4000,
    },
    "italy": {
        "url": "https://download.geofabrik.de/europe/italy-latest.osm.pbf",
        "size_mb": 2000,
        "tiles_size_mb": 4500,
    },
    "spain": {
        "url": "https://download.geofabrik.de/europe/spain-latest.osm.pbf",
        "size_mb": 1500,
        "tiles_size_mb": 3500,
    },
    # North America
    "california": {
        "url": "https://download.geofabrik.de/north-america/us/california-latest.osm.pbf",
        "size_mb": 500,
        "tiles_size_mb": 1200,
    },
    "texas": {
        "url": "https://download.geofabrik.de/north-america/us/texas-latest.osm.pbf",
        "size_mb": 400,
        "tiles_size_mb": 1000,
    },
    "florida": {
        "url": "https://download.geofabrik.de/north-america/us/florida-latest.osm.pbf",
        "size_mb": 250,
        "tiles_size_mb": 600,
    },
    "new-york": {
        "url": "https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf",
        "size_mb": 200,
        "tiles_size_mb": 500,
    },
}

# Installation paths
TILE_DIR = Path("/data/media/0/valhalla")
# Valhalla binaries built from submodule (third_party/valhalla/valhalla_repo)
VALHALLA_BIN_DIR = Path("/home/vcar/pilot/openpilot/third_party/valhalla/bin")


@dataclass
class TileStatus:
    region: str
    pbf_exists: bool
    tar_exists: bool
    pbf_size_mb: float
    tar_size_mb: float
    
    @property
    def is_ready(self) -> bool:
        return self.tar_exists
    
    @property
    def needs_build(self) -> bool:
        return self.pbf_exists and not self.tar_exists


class TileManager:
    """Manages OSM tile downloads and Valhalla tile builds."""
    
    def __init__(self, tile_dir: Path | None = None):
        self.tile_dir = tile_dir or TILE_DIR
        self.tile_dir.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        (self.tile_dir / "tiles").mkdir(exist_ok=True)
        (self.tile_dir / "logs").mkdir(exist_ok=True)
    
    def get_status(self, region: str) -> TileStatus:
        """Get status of a regional tile set."""
        pbf_file = self.tile_dir / f"{region}.osm.pbf"
        tar_file = self.tile_dir / "valhalla_tiles.tar"
        
        pbf_size = pbf_file.stat().st_size / (1024 * 1024) if pbf_file.exists() else 0
        tar_size = tar_file.stat().st_size / (1024 * 1024) if tar_file.exists() else 0
        
        return TileStatus(
            region=region,
            pbf_exists=pbf_file.exists(),
            tar_exists=tar_file.exists(),
            pbf_size_mb=round(pbf_size, 1),
            tar_size_mb=round(tar_size, 1),
        )
    
    def download(self, region: str, force: bool = False) -> bool:
        """
        Download OSM PBF for a region.
        
        Args:
            region: Region key from TILE_SOURCES
            force: Re-download even if file exists
            
        Returns:
            True if successful
        """
        if region not in TILE_SOURCES:
            print(f"ERROR: Unknown region '{region}'")
            print(f"Available: {', '.join(TILE_SOURCES.keys())}")
            return False
        
        source = TILE_SOURCES[region]
        pbf_file = self.tile_dir / f"{region}.osm.pbf"
        
        if pbf_file.exists() and not force:
            print(f"✓ {region}: PBF already exists ({pbf_file.stat().st_size / (1024*1024):.1f} MB)")
            return True
        
        print(f"Downloading {region} OSM data...")
        print(f"  URL: {source['url']}")
        print(f"  Expected size: ~{source['size_mb']} MB")
        print(f"  Destination: {pbf_file}")
        
        try:
            # Download with progress
            def report_progress(block_num, block_size, total_size):
                downloaded = block_num * block_size
                percent = min(100, downloaded * 100 / total_size)
                print(f"\r  Progress: {percent:.1f}% ({downloaded / (1024*1024):.1f} MB)", end="")
            
            urllib.request.urlretrieve(
                source["url"],
                pbf_file,
                reporthook=report_progress
            )
            print()  # Newline after progress
            
            print(f"✓ Download complete: {pbf_file.stat().st_size / (1024*1024):.1f} MB")
            return True
            
        except Exception as e:
            print(f"\nERROR: Download failed: {e}")
            if pbf_file.exists():
                pbf_file.unlink()  # Remove partial download
            return False
    
    def build_tiles(self, region: str) -> bool:
        """
        Build Valhalla tiles from OSM PBF.
        
        Args:
            region: Region key from TILE_SOURCES
            
        Returns:
            True if successful
        """
        pbf_file = self.tile_dir / f"{region}.osm.pbf"
        tar_file = self.tile_dir / "valhalla_tiles.tar"
        config_file = self.tile_dir / "valhalla.json"
        
        if not pbf_file.exists():
            print(f"ERROR: PBF file not found: {pbf_file}")
            print(f"Run: python tile_manager.py download {region}")
            return False
        
        # Find Valhalla binaries
        valhalla_build_config = VALHALLA_BIN_DIR / "valhalla_build_config"
        valhalla_build_tiles = VALHALLA_BIN_DIR / "valhalla_build_tiles"
        
        if not valhalla_build_tiles.exists():
            print(f"ERROR: Valhalla binaries not found at {VALHALLA_BIN_DIR}")
            print("Build with: scons -j4 --with-valhalla")
            return False
        
        print(f"Building Valhalla tiles for {region}...")
        print(f"  Input: {pbf_file}")
        print(f"  Output: {tar_file}")
        print(f"  This will take 5-30 minutes depending on region size...")
        
        try:
            # Generate config
            print("\n[1/4] Generating Valhalla configuration...")
            config_cmd = [
                str(valhalla_build_config),
                "--mjolnir-tile-dir", str(self.tile_dir / "tiles"),
                "--mjolnir-tile-extract", str(tar_file),
                "--httpd-listen", "tcp://127.0.0.1:8002",
            ]
            
            with open(config_file, "w") as f:
                result = subprocess.run(config_cmd, stdout=f, stderr=subprocess.PIPE, text=True)
                if result.returncode != 0:
                    print(f"ERROR: Config generation failed: {result.stderr}")
                    return False
            print(f"  Config: {config_file}")
            
            # Build tiles
            print("\n[2/4] Building routing tiles...")
            build_cmd = [
                str(valhalla_build_tiles),
                "-c", str(config_file),
                str(pbf_file),
            ]
            
            result = subprocess.run(build_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"ERROR: Tile build failed:\n{result.stderr}")
                return False
            print("  Tiles built successfully")
            
            # Create tar archive
            print("\n[3/4] Creating tile archive...")
            tiles_dir = self.tile_dir / "tiles"
            tar_cmd = [
                "tar", "-cf", str(tar_file),
                "-C", str(tiles_dir), "."
            ]
            
            result = subprocess.run(tar_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"ERROR: Tar creation failed: {result.stderr}")
                return False
            
            tar_size = tar_file.stat().st_size / (1024 * 1024)
            print(f"  Archive: {tar_file} ({tar_size:.1f} MB)")
            
            # Cleanup temporary tiles directory
            print("\n[4/4] Cleaning up...")
            subprocess.run(["rm", "-rf", str(tiles_dir)], check=False)
            
            print(f"\n✓ Tile build complete!")
            print(f"  Region: {region}")
            print(f"  Tiles: {tar_file} ({tar_size:.1f} MB)")
            print(f"  Config: {config_file}")
            return True
            
        except Exception as e:
            print(f"\nERROR: Build failed: {e}")
            return False
    
    def ensure_tiles(self, region: str) -> bool:
        """Download and build tiles if needed."""
        status = self.get_status(region)
        
        if status.is_ready:
            print(f"✓ {region}: Tiles ready ({status.tar_size_mb} MB)")
            return True
        
        if not status.pbf_exists:
            if not self.download(region):
                return False
        
        return self.build_tiles(region)
    
    def list_regions(self):
        """List available regions."""
        print("\nAvailable regions:")
        print(f"{'Region':<20} {'PBF Size':<12} {'Tiles Size':<12} {'Status':<10}")
        print("-" * 60)
        
        for region, info in sorted(TILE_SOURCES.items()):
            status = self.get_status(region)
            
            if status.is_ready:
                status_str = f"✓ Ready ({status.tar_size_mb} MB)"
            elif status.pbf_exists:
                status_str = f"⚠ Needs build"
            else:
                status_str = "✗ Not downloaded"
            
            print(f"{region:<20} {info['size_mb']:<12} {info['tiles_size_mb']:<12} {status_str}")
    
    def cleanup(self, region: str | None = None):
        """Remove downloaded files to free space."""
        if region:
            pbf_file = self.tile_dir / f"{region}.osm.pbf"
            if pbf_file.exists():
                print(f"Removing {pbf_file}...")
                pbf_file.unlink()
        else:
            # Clean all PBF files (keep tiles)
            for pbf in self.tile_dir.glob("*.osm.pbf"):
                print(f"Removing {pbf}...")
                pbf.unlink()
        
        print("✓ Cleanup complete")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Valhalla Tile Manager for ExoPilot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list                  # List available regions
  %(prog)s status                # Show current tile status
  %(prog)s download thailand     # Download Thailand OSM data
  %(prog)s build thailand        # Build Valhalla tiles for Thailand
  %(prog)s ensure thailand       # Download and build if needed
  %(prog)s cleanup               # Remove PBF files (keep tiles)
        """
    )
    
    parser.add_argument("command", choices=[
        "list", "status", "download", "build", "ensure", "cleanup"
    ], help="Command to execute")
    parser.add_argument("region", nargs="?", help="Region name (for download/build/ensure)")
    parser.add_argument("--force", "-f", action="store_true", help="Force re-download")
    
    args = parser.parse_args()
    
    manager = TileManager()
    
    if args.command == "list":
        manager.list_regions()
    
    elif args.command == "status":
        print("\nTile Status:")
        print(f"{'Region':<20} {'PBF':<10} {'Tiles':<10} {'Size':<15}")
        print("-" * 60)
        
        for region in sorted(TILE_SOURCES.keys()):
            status = manager.get_status(region)
            pbf_str = f"{status.pbf_size_mb} MB" if status.pbf_exists else "-"
            tar_str = f"{status.tar_size_mb} MB" if status.tar_exists else "-"
            print(f"{region:<20} {pbf_str:<10} {tar_str:<10}")
    
    elif args.command == "download":
        if not args.region:
            print("ERROR: Please specify a region")
            print(f"Available: {', '.join(TILE_SOURCES.keys())}")
            sys.exit(1)
        success = manager.download(args.region, force=args.force)
        sys.exit(0 if success else 1)
    
    elif args.command == "build":
        if not args.region:
            print("ERROR: Please specify a region")
            sys.exit(1)
        success = manager.build_tiles(args.region)
        sys.exit(0 if success else 1)
    
    elif args.command == "ensure":
        if not args.region:
            print("ERROR: Please specify a region")
            sys.exit(1)
        success = manager.ensure_tiles(args.region)
        sys.exit(0 if success else 1)
    
    elif args.command == "cleanup":
        manager.cleanup(args.region)


if __name__ == "__main__":
    main()
