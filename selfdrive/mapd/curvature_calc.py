"""
curvature_calc.py - Calculate road curvature from OSM node data.
"""

import math
from typing import Any


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate great circle distance between two points.

    Returns:
        Distance in meters
    """
    R = 6371000  # Earth radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def calculate_curvature_from_nodes(nodes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """
    Calculate curvature from a sequence of OSM nodes.

    Uses three consecutive points to calculate local curvature.

    Args:
        nodes: list of (lat, lon) tuples

    Returns:
        list of (distance_from_start, curvature) tuples
        curvature is in 1/m (0 for straight)
    """
    if len(nodes) < 3:
        return []

    curvatures = []
    cumulative_dist = 0.0

    # Calculate curvature at each interior point
    for i in range(1, len(nodes) - 1):
        p1 = nodes[i - 1]
        p2 = nodes[i]  # Current point
        p3 = nodes[i + 1]

        # Calculate side lengths of triangle
        a = haversine_distance(p2[0], p2[1], p3[0], p3[1])  # p2 to p3
        b = haversine_distance(p1[0], p1[1], p3[0], p3[1])  # p1 to p3
        c = haversine_distance(p1[0], p1[1], p2[0], p2[1])  # p1 to p2

        # Update cumulative distance
        cumulative_dist += c

        # Calculate curvature using circumradius formula
        # For three points, radius of circumscribed circle = (abc) / (4 * area)
        # Curvature = 1 / radius

        # Triangle area using Heron's formula
        s = (a + b + c) / 2  # Semi-perimeter
        area_sq = s * (s - a) * (s - b) * (s - c)

        if area_sq > 1e-10:  # Avoid division by zero
            area = math.sqrt(area_sq)
            circumradius = (a * b * c) / (4 * area)
            curvature = 1.0 / circumradius

            # Limit curvature to reasonable values (max ~0.1 = 10m radius)
            curvature = min(curvature, 0.1)
        else:
            curvature = 0.0

        curvatures.append((cumulative_dist, curvature))

    return curvatures


def parse_osm_geometry(osm_data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Parse OSM Overpass API response to extract road geometry.

    Args:
        osm_data: JSON response from Overpass API

    Returns:
        list of road segments with geometry
    """
    roads = []

    if not osm_data or 'elements' not in osm_data:
        return roads

    for element in osm_data['elements']:
        if element.get('type') != 'way':
            continue

        tags = element.get('tags', {})
        geometry = element.get('geometry', [])

        if not geometry:
            continue

        # Extract node coordinates
        nodes = [(node['lat'], node['lon']) for node in geometry]

        # Calculate curvature
        curvature = calculate_curvature_from_nodes(nodes)

        # Extract speed limit if available
        speed_limit = None
        if 'maxspeed' in tags:
            try:
                # Parse speed limit (handles "50", "50 mph", etc.)
                speed_str = tags['maxspeed'].split()[0]
                speed_limit = int(speed_str)
            except (ValueError, IndexError):
                pass

        road = {
            'id': element.get('id'),
            'name': tags.get('name', ''),
            'highway': tags.get('highway', ''),
            'nodes': nodes,
            'curvature': curvature,
            'speed_limit': speed_limit,
            'oneway': tags.get('oneway', 'no') == 'yes'
        }

        roads.append(road)

    return roads


def find_nearest_road(roads: list[dict], lat: float, lon: float,
                       bearing: float | None = None) -> dict | None:
    """
    Find the nearest road to a GPS position.

    Args:
        roads: list of road segments
        lat: Current latitude
        lon: Current longitude
        bearing: Current bearing in degrees (optional, for matching direction)

    Returns:
        Nearest road segment or None
    """
    if not roads:
        return None

    nearest = None
    min_distance = float('inf')

    for road in roads:
        # Find closest point on road
        for node in road['nodes']:
            dist = haversine_distance(lat, lon, node[0], node[1])
            if dist < min_distance:
                min_distance = dist
                nearest = road

    return nearest


def get_upcoming_curves(road: dict, lat: float, lon: float,
                        lookahead: int = 500) -> list[tuple[float, float]]:
    """
    Get upcoming curves on a road segment.

    Args:
        road: Road segment dictionary
        lat: Current position latitude
        lon: Current position longitude
        lookahead: Distance to look ahead in meters

    Returns:
        list of (distance, curvature) tuples for curves ahead
    """
    if not road or 'curvature' not in road:
        return []

    # Find current position on road
    min_dist = float('inf')
    current_idx = 0

    for i, node in enumerate(road['nodes']):
        dist = haversine_distance(lat, lon, node[0], node[1])
        if dist < min_dist:
            min_dist = dist
            current_idx = i

    # Calculate distance from current position to each curvature point
    upcoming = []

    for i in range(current_idx, len(road['nodes']) - 1):
        if i > 0 and i - 1 < len(road['curvature']):
            dist_along, curvature = road['curvature'][i - 1]

            # Calculate actual distance from current position
            actual_dist = dist_along
            if current_idx > 0 and current_idx - 1 < len(road['curvature']):
                current_dist_along = road['curvature'][current_idx - 1][0]
                actual_dist = dist_along - current_dist_along

            if actual_dist <= lookahead and curvature > 0.001:  # Filter small curvatures
                upcoming.append((actual_dist, curvature))

    return upcoming


