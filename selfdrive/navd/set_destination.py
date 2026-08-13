#!/usr/bin/env python3
"""
Set Navigation Destination

Usage:
    # From command line
    python3 set_destination.py 32.7116 -117.1256 "Taco Bell"

    # From Google Maps URL
    python3 set_destination.py "https://www.google.com/maps/place/..."

    # Clear destination
    python3 set_destination.py --clear
"""

import json
import argparse

from openpilot.common.params import Params


def parse_google_maps_url(url: str) -> tuple:
    """Extract lat/lon from Google Maps URL."""
    # Format: https://www.google.com/maps/place/.../@32.7116,-117.1256,...
    try:
        if "/@" in url:
            coords_part = url.split("/@")[1].split(",")[:2]
            lat = float(coords_part[0])
            lon = float(coords_part[1])
            return lat, lon
    except (IndexError, ValueError):
        pass
    return None, None


def set_destination(lat: float, lon: float, place_name: str = ""):
    """Set navigation destination."""
    params = Params()

    dest = {
        "latitude": lat,
        "longitude": lon,
        "place_name": place_name
    }

    params.put("NavDestination", json.dumps(dest))
    params.remove("NavDestinationWaypoints")

    print(f"✅ Destination set: {place_name or 'Custom'}")
    print(f"   Lat: {lat}, Lon: {lon}")


def clear_destination():
    """Clear current destination."""
    params = Params()
    params.remove("NavDestination")
    params.remove("NavDestinationWaypoints")
    print("✅ Destination cleared")


def main():
    parser = argparse.ArgumentParser(description="Set navigation destination")
    parser.add_argument("lat_or_url", nargs="?", help="Latitude or Google Maps URL")
    parser.add_argument("lon", nargs="?", type=float, help="Longitude")
    parser.add_argument("place_name", nargs="?", default="", help="Place name (optional)")
    parser.add_argument("--clear", action="store_true", help="Clear destination")

    args = parser.parse_args()

    if args.clear:
        clear_destination()
        return

    if not args.lat_or_url:
        parser.print_help()
        return

    # Check if it's a URL
    if args.lat_or_url.startswith("http"):
        lat, lon = parse_google_maps_url(args.lat_or_url)
        if lat is None:
            print("❌ Could not parse Google Maps URL")
            return
        set_destination(lat, lon, args.place_name)
    else:
        # It's a latitude
        if args.lon is None:
            print("❌ Longitude required")
            return
        set_destination(float(args.lat_or_url), args.lon, args.place_name)


if __name__ == "__main__":
    main()
