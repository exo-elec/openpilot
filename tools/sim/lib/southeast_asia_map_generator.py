"""Generate CARLA maps from Southeast / South Asian OpenStreetMap data.

CARLA has no built-in Bangkok/Manila/Jakarta maps, but its Osm2Odr converter
creates drivable OpenDRIVE worlds from any OSM export. This script downloads
preset city districts and converts them for sim testing.

Usage:
    # Download and convert Bangkok Sukhumvit area
    python tools/sim/lib/southeast_asia_map_generator.py --city bangkok_sukhumvit

    # Custom OSM file
    python tools/sim/lib/southeast_asia_map_generator.py --osm ~/my_map.osm --out ~/my_map.xodr

    # Load into CARLA
    python PythonAPI/util/config.py --osm-path=bangkok_sukhumvit.osm

Presets (OpenStreetMap bounding boxes):
  bangkok_sukhumvit   — Busy arterial with motorbike lanes
  bangkok_silom       — Dense CBD intersection
  manila_makati       — Grid CBD with jeepney-like traffic
  jakarta_sudirman    — Wide avenue + chaotic merge
  dhaka_gulshan       — Narrow lanes, rickshaw density
  ho_chi_minh_d1      — French-grid CBD, dense scooter flow
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request

# Lazy carla import — only needed for conversion

CITY_PRESETS: dict[str, dict] = {
  "bangkok_sukhumvit": {
    "bbox": "100.5593,13.7370,100.5670,13.7430",
    "description": "Sukhumvit Rd: 3-lane arterial with motorbike lanes",
  },
  "bangkok_silom": {
    "bbox": "100.5320,13.7230,100.5380,13.7280",
    "description": "Silom/Sathorn CBD intersection cluster",
  },
  "manila_makati": {
    "bbox": "121.0180,14.5520,121.0280,14.5600",
    "description": "Makati CBD grid with Ayala triangle",
  },
  "jakarta_sudirman": {
    "bbox": "106.8050,-6.2150,106.8150,-6.2050",
    "description": "Jalan Sudirman: wide avenue, heavy merge",
  },
  "dhaka_gulshan": {
    "bbox": "90.4050,23.7950,90.4150,23.8050",
    "description": "Gulshan narrow lanes, high rickshaw density",
  },
  "ho_chi_minh_d1": {
    "bbox": "106.6900,10.7700,106.7000,10.7800",
    "description": "District 1 French-grid CBD",
  },
}

OSM_API_URL = "https://api.openstreetmap.org/api/0.6/map?bbox={bbox}"


def download_osm(bbox: str, output_path: str) -> bool:
  """Download OSM data for a bounding box."""
  url = OSM_API_URL.format(bbox=bbox)
  print(f"Downloading OSM data for bbox {bbox} ...")
  print(f"  URL: {url}")
  try:
    urllib.request.urlretrieve(url, output_path)
    size_kb = os.path.getsize(output_path) / 1024.0
    print(f"  Saved {output_path} ({size_kb:.1f} KB)")
    return True
  except Exception as e:
    print(f"  ERROR: {e}")
    return False


def convert_osm_to_xodr(osm_path: str, xodr_path: str, lane_width: float = 3.2) -> bool:
  """Convert .osm → .xodr using CARLA's Osm2Odr."""
  try:
    import carla
  except ImportError as e:
    print(f"ERROR: carla Python API not installed: {e}")
    print("Install: pip install /opt/CARLA_0.9.15/PythonAPI/carla/dist/carla-*.whl")
    return False

  print(f"Converting {osm_path} → {xodr_path} ...")
  try:
    with open(osm_path, encoding="utf-8") as f:
      osm_data = f.read()

    settings = carla.Osm2OdrSettings()
    settings.set_osm_way_types([
      "motorway", "motorway_link", "trunk", "trunk_link",
      "primary", "primary_link", "secondary", "secondary_link",
      "tertiary", "tertiary_link", "unclassified", "residential",
      "living_street", "service",
    ])
    settings.default_lane_width = lane_width
    settings.generate_traffic_lights = True
    settings.all_junctions_with_traffic_lights = False
    settings.center_map = True

    xodr_data = carla.Osm2Odr.convert(osm_data, settings)

    with open(xodr_path, "w", encoding="utf-8") as f:
      f.write(xodr_data)

    size_kb = os.path.getsize(xodr_path) / 1024.0
    print(f"  Saved {xodr_path} ({size_kb:.1f} KB)")
    return True
  except Exception as e:
    print(f"  ERROR: {e}")
    return False


def load_into_carla(xodr_path: str, host: str = "localhost", port: int = 2000) -> bool:
  """Load an .xodr map into a running CARLA server."""
  try:
    import carla
  except ImportError:
    print("ERROR: carla Python API not installed")
    return False

  print(f"Loading {xodr_path} into CARLA ({host}:{port}) ...")
  try:
    client = carla.Client(host, port)
    client.set_timeout(30.0)

    with open(xodr_path, encoding="utf-8") as f:
      xodr_xml = f.read()

    params = carla.OpendriveGenerationParameters(
      vertex_distance=2.0,
      max_road_length=500.0,
      wall_height=0.0,          # 0 strongly recommended for OSM maps
      additional_width=0.6,
      smooth_junctions=True,
      enable_mesh_visibility=True,
    )
    world = client.generate_opendrive_world(xodr_xml, params)
    print(f"  Map loaded: {world.get_map().name}")
    return True
  except Exception as e:
    print(f"  ERROR: {e}")
    return False


def main():
  parser = argparse.ArgumentParser(description="Generate CARLA maps from SEA/OSM data")
  parser.add_argument("--city", choices=list(CITY_PRESETS.keys()),
                      default="bangkok_sukhumvit",
                      help="Preset city district to download (default: bangkok_sukhumvit)")
  parser.add_argument("--bbox", help="Custom OSM bbox: min_lon,min_lat,max_lon,max_lat")
  parser.add_argument("--osm", help="Path to existing .osm file (skip download)")
  parser.add_argument("--out", default=".", help="Output directory")
  parser.add_argument("--lane-width", type=float, default=3.2,
                      help="Default lane width in meters (default 3.2 for Asian roads)")
  parser.add_argument("--load", action="store_true",
                      help="Load generated map into running CARLA server")
  parser.add_argument("--host", default="localhost")
  parser.add_argument("--port", type=int, default=2000)
  parser.add_argument("--list", action="store_true", help="List available presets")
  args = parser.parse_args()

  if args.list:
    print("Available SEA city presets:")
    for name, info in CITY_PRESETS.items():
      print(f"  {name:20s}  {info['bbox']}  — {info['description']}")
    return 0

  if args.city:
    preset = CITY_PRESETS[args.city]
    bbox = preset["bbox"]
    base_name = args.city
    print(f"Preset: {args.city} — {preset['description']}")
  elif args.bbox:
    bbox = args.bbox
    base_name = "custom"
  elif args.osm:
    bbox = None
    base_name = os.path.splitext(os.path.basename(args.osm))[0]
  else:
    parser.print_help()
    return 1

  osm_path = args.osm or os.path.join(args.out, f"{base_name}.osm")
  xodr_path = os.path.join(args.out, f"{base_name}.xodr")

  # Download if needed
  if not args.osm:
    if not download_osm(bbox, osm_path):
      return 1

  # Convert
  if not convert_osm_to_xodr(osm_path, xodr_path, lane_width=args.lane_width):
    return 1

  # Load into CARLA
  if args.load:
    if not load_into_carla(xodr_path, args.host, args.port):
      return 1

  print("\nDone. Map files:")
  print(f"  OSM:  {osm_path}")
  print(f"  XODR: {xodr_path}")
  print("\nTo load into CARLA later:")
  print(f"  python PythonAPI/util/config.py --osm-path={osm_path}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
