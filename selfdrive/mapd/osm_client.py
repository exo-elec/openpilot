"""
osm_client.py - OpenStreetMap Overpass API client.

Queries OSM for road geometry, speed limits, and other map data.
"""

import time
import json
import urllib.request
import urllib.error
from typing import Any
from openpilot.common.swaglog import cloudlog


# Overpass API endpoints (in order of preference)
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Request timeout
REQUEST_TIMEOUT = 30  # seconds

# Rate limiting
MIN_REQUEST_INTERVAL = 2.0  # seconds between requests
_last_request_time = 0.0


def rate_limited_request(url: str, data: bytes, timeout: int = REQUEST_TIMEOUT) -> bytes:
    """
    Make a rate-limited HTTP POST request.

    Args:
        url: Request URL
        data: POST data
        timeout: Request timeout in seconds

    Returns:
        Response bytes

    Raises:
        urllib.error.URLError: On request failure
    """
    global _last_request_time

    # Rate limiting
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)

    _last_request_time = time.monotonic()

    # Make request
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'EOP-MAPD/1.0'
        }
    )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def query_road_geometry(lat: float, lon: float, radius: int = 500,
                        overpass_url: str | None = None) -> dict[str, Any | None]:
    """
    Query OSM for road geometry around a location.

    Args:
        lat: Latitude
        lon: Longitude
        radius: Search radius in meters
        overpass_url: Specific Overpass URL (uses default if None)

    Returns:
        OSM JSON response or None on failure
    """
    urls = [overpass_url] if overpass_url else OVERPASS_URLS

    # Build Overpass QL query
    # Query for major road types with geometry
    query = f"""
    [out:json][timeout:30];
    way(around:{radius},{lat},{lon})
      [highway~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential)$"];
    out geom;
    """

    data = query.encode('utf-8')

    for url in urls:
        if not url:
            continue

        try:
            cloudlog.debug(f"Querying OSM at {lat:.6f}, {lon:.6f} (radius={radius}m)")
            response = rate_limited_request(url, data)
            return json.loads(response.decode('utf-8'))

        except urllib.error.HTTPError as e:
            cloudlog.warning(f"OSM HTTP error from {url}: {e.code}")
            if e.code == 429:  # Rate limited
                time.sleep(5)  # Wait longer before retry
            continue

        except urllib.error.URLError as e:
            cloudlog.warning(f"OSM URL error from {url}: {e.reason}")
            continue

        except json.JSONDecodeError as e:
            cloudlog.error(f"OSM JSON decode error: {e}")
            continue

        except Exception as e:
            cloudlog.error(f"OSM query error: {e}")
            continue

    cloudlog.error("All OSM endpoints failed")
    return None


def query_speed_limits(lat: float, lon: float, radius: int = 200,
                       overpass_url: str | None = None) -> dict[str, Any | None]:
    """
    Query OSM for speed limits around a location.

    Args:
        lat: Latitude
        lon: Longitude
        radius: Search radius in meters
        overpass_url: Specific Overpass URL

    Returns:
        OSM JSON response or None on failure
    """
    urls = [overpass_url] if overpass_url else OVERPASS_URLS

    # Query for roads with maxspeed tag
    query = f"""
    [out:json][timeout:30];
    way(around:{radius},{lat},{lon})
      [highway~"^(motorway|trunk|primary|secondary|tertiary)$"]
      [maxspeed];
    out tags;
    """

    data = query.encode('utf-8')

    for url in urls:
        if not url:
            continue

        try:
            response = rate_limited_request(url, data)
            return json.loads(response.decode('utf-8'))

        except Exception as e:
            cloudlog.warning(f"Speed limit query failed for {url}: {e}")
            continue

    return None


def parse_speed_limit(osm_data: dict[str, Any]) -> int | None:
    """
    Parse speed limit from OSM data.

    Args:
        osm_data: OSM JSON response

    Returns:
        Speed limit in km/h or None
    """
    if not osm_data or 'elements' not in osm_data:
        return None

    for element in osm_data['elements']:
        if element.get('type') != 'way':
            continue

        tags = element.get('tags', {})
        if 'maxspeed' in tags:
            maxspeed = tags['maxspeed']

            # Parse various formats: "50", "50 mph", "50 km/h"
            try:
                parts = maxspeed.split()
                value = int(parts[0])

                # Convert mph to km/h if needed
                if len(parts) > 1 and 'mph' in parts[1].lower():
                    value = int(value * 1.60934)

                return value
            except (ValueError, IndexError):
                continue

    return None


def extract_road_name(osm_data: dict[str, Any]) -> str:
    """
    Extract road name from OSM data.

    Args:
        osm_data: OSM JSON response

    Returns:
        Road name or empty string
    """
    if not osm_data or 'elements' not in osm_data:
        return ""

    for element in osm_data['elements']:
        if element.get('type') != 'way':
            continue

        tags = element.get('tags', {})

        # Prefer name, fallback to ref (e.g., "I-95")
        if 'name' in tags:
            return tags['name']
        elif 'ref' in tags:
            return tags['ref']

    return ""
