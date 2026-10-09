#!/usr/bin/env python3
"""
NavD Helpers — Valhalla/OSM edition

Provides:
- Coordinate class with haversine math
- Polyline6 decoder (Valhalla route shape format)
- Valhalla maneuver parser → openpilot navInstruction fields
- Distance calculations
"""

from __future__ import annotations

import json
import math
from typing import cast

from openpilot.common.numpy_fast import clip
from openpilot.common.params import Params

EARTH_MEAN_RADIUS = 6371007.2

# Valhalla maneuver type → (maneuverType, maneuverModifier)
# https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/#maneuver-types
_VALHALLA_TYPE_MAP: dict[int, tuple[str, str]] = {
    0:  ('none',        ''),
    1:  ('depart',      ''),
    2:  ('depart',      'right'),
    3:  ('depart',      'left'),
    4:  ('arrive',      ''),
    5:  ('arrive',      'right'),
    6:  ('arrive',      'left'),
    7:  ('continue',    ''),
    8:  ('continue',    'straight'),
    9:  ('turn',        'slight right'),
    10: ('turn',        'right'),
    11: ('turn',        'sharp right'),
    12: ('uturn',       'right'),
    13: ('uturn',       'left'),
    14: ('turn',        'sharp left'),
    15: ('turn',        'left'),
    16: ('turn',        'slight left'),
    17: ('on ramp',     'straight'),
    18: ('on ramp',     'right'),
    19: ('on ramp',     'left'),
    20: ('off ramp',    'right'),
    21: ('off ramp',    'left'),
    22: ('fork',        'straight'),
    23: ('fork',        'right'),
    24: ('fork',        'left'),
    25: ('merge',       ''),
    26: ('roundabout',  ''),
    27: ('roundabout exit', ''),
    28: ('ferry',       ''),
    29: ('ferry',       ''),
    37: ('none',        ''),
}


class Coordinate:
    """GPS coordinate with haversine distance math."""

    def __init__(self, latitude: float, longitude: float) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.annotations: dict[str, float] = {}

    @classmethod
    def from_valhalla_tuple(cls, lat: float, lon: float) -> Coordinate:
        return cls(lat, lon)

    def as_dict(self) -> dict[str, float]:
        return {'latitude': self.latitude, 'longitude': self.longitude}

    def __str__(self) -> str:
        return f'Coordinate({self.latitude:.6f}, {self.longitude:.6f})'

    def __repr__(self) -> str:
        return self.__str__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Coordinate):
            return False
        return self.latitude == other.latitude and self.longitude == other.longitude

    def __sub__(self, other: Coordinate) -> Coordinate:
        return Coordinate(self.latitude - other.latitude, self.longitude - other.longitude)

    def __add__(self, other: Coordinate) -> Coordinate:
        return Coordinate(self.latitude + other.latitude, self.longitude + other.longitude)

    def __mul__(self, c: float) -> Coordinate:
        return Coordinate(self.latitude * c, self.longitude * c)

    def dot(self, other: Coordinate) -> float:
        return self.latitude * other.latitude + self.longitude * other.longitude

    def distance_to(self, other: Coordinate) -> float:
        """Haversine distance in metres."""
        dlat = math.radians(other.latitude - self.latitude)
        dlon = math.radians(other.longitude - self.longitude)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(self.latitude))
             * math.cos(math.radians(other.latitude))
             * math.sin(dlon / 2) ** 2)
        return 2 * math.asin(math.sqrt(a)) * EARTH_MEAN_RADIUS


def minimum_distance(a: Coordinate, b: Coordinate, p: Coordinate) -> float:
    """Minimum distance from point p to line segment a–b."""
    if a.distance_to(b) < 0.01:
        return a.distance_to(p)
    ap = p - a
    ab = b - a
    t = clip(ap.dot(ab) / ab.dot(ab), 0.0, 1.0)
    return cast(float, (a + ab * t).distance_to(p))


def distance_along_geometry(geometry: list[Coordinate], pos: Coordinate) -> float:
    """Distance along geometry to the closest point to pos."""
    if len(geometry) <= 1:
        return 0.0
    if len(geometry) == 2:
        return geometry[0].distance_to(pos)

    total = 0.0
    closest_total = 0.0
    closest_dist = float('inf')

    for i in range(len(geometry) - 1):
        d = minimum_distance(geometry[i], geometry[i + 1], pos)
        if d < closest_dist:
            closest_dist = d
            closest_total = total + geometry[i].distance_to(pos)
        total += geometry[i].distance_to(geometry[i + 1])

    return closest_total


def coordinate_from_param(param: str, params: Params | None = None) -> Coordinate | None:
    if params is None:
        params = Params()
    json_str = params.get(param)
    if json_str is None:
        return None
    try:
        pos = json.loads(json_str)
        if 'latitude' not in pos or 'longitude' not in pos:
            return None
        return Coordinate(pos['latitude'], pos['longitude'])
    except json.JSONDecodeError:
        return None


# ------------------------------------------------------------------
# Valhalla-specific helpers
# ------------------------------------------------------------------

def decode_polyline6(encoded: str) -> list[Coordinate]:
    """
    Decode a Valhalla polyline6 encoded string into a list of Coordinates.

    Valhalla uses precision=6 (multiply by 1e6), unlike Google's precision=5.
    """
    coords: list[Coordinate] = []
    index = 0
    lat = 0
    lng = 0
    length = len(encoded)

    while index < length:
        # Decode latitude delta
        result = 0
        shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if result & 1 else result >> 1
        lat += dlat

        # Decode longitude delta
        result = 0
        shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if result & 1 else result >> 1
        lng += dlng

        coords.append(Coordinate(lat / 1e6, lng / 1e6))

    return coords


def parse_valhalla_maneuvers(
    maneuvers: list[dict],
    shape: list[Coordinate],
) -> list[dict]:
    """
    Convert Valhalla maneuver list into a simplified format consumed by navd.

    Each output dict has:
        instruction       str   — human text from Valhalla
        maneuver_type     str   — openpilot string (e.g. "turn")
        maneuver_modifier str   — openpilot modifier (e.g. "left")
        length_m          float — step length in metres
        time              float — step duration in seconds
        begin_shape_index int
        end_shape_index   int
        speed_limit_ms    float | None
    """
    parsed = []
    for m in maneuvers:
        vtype = m.get('type', 0)
        mtype, modifier = _VALHALLA_TYPE_MAP.get(vtype, ('none', ''))

        # Street name for the instruction suffix
        street = ''
        if m.get('street_names'):
            street = m['street_names'][0]

        instruction = m.get('instruction', '')
        if not instruction and mtype and street:
            instruction = f"{mtype.title()} onto {street}"

        length_km = m.get('length', 0.0)
        parsed.append({
            'instruction': instruction,
            'maneuver_type': mtype,
            'maneuver_modifier': modifier,
            'length_m': length_km * 1000.0,
            'time': m.get('time', 0.0),
            'begin_shape_index': m.get('begin_shape_index', 0),
            'end_shape_index': m.get('end_shape_index', max(0, len(shape) - 1) if shape else 0),
            'speed_limit_ms': None,  # Valhalla OSM doesn't annotate per-maneuver speed limits
        })

    return parsed


# ------------------------------------------------------------------
# Navigation TTS text generation
# ------------------------------------------------------------------

def format_distance_for_tts(distance_m: float) -> str:
    """Format distance in natural speech (meters or kilometers)."""
    if distance_m < 30:
        return ""  # Very close — omit distance
    elif distance_m < 100:
        # Round to nearest 10m
        d: float = round(distance_m / 10) * 10
        return f"In {int(d)} meters"
    elif distance_m < 1000:
        # Round to nearest 50m
        d = round(distance_m / 50) * 50
        return f"In {int(d)} meters"
    else:
        # Round to nearest 0.1km
        d = round(distance_m / 100) / 10
        if d == int(d):
            return f"In {int(d)} kilometer{'s' if int(d) > 1 else ''}"
        return f"In {d:.1f} kilometers"


def generate_tts_text(
    maneuver_type: str,
    maneuver_modifier: str,
    distance_m: float,
    street_name: str = "",
) -> str:
    """
    Generate natural-sounding TTS text for a navigation maneuver.

    Examples:
        - "In 500 meters, turn right onto Main Street"
        - "Turn left"
        - "In 2 kilometers, take the exit on the right"
        - "You have arrived at your destination"
        - "Continue straight for 1 kilometer"
    """
    # Special cases
    if maneuver_type == 'arrive':
        if street_name:
            return f"You have arrived at {street_name}"
        return "You have arrived at your destination"

    if maneuver_type == 'depart':
        if maneuver_modifier == 'right':
            return "Head right"
        elif maneuver_modifier == 'left':
            return "Head left"
        return "Start driving"

    # Build action phrase
    action = maneuver_type

    if maneuver_type == 'turn':
        if maneuver_modifier:
            action = f"turn {maneuver_modifier}"
    elif maneuver_type == 'continue':
        if maneuver_modifier == 'straight':
            action = "continue straight"
        else:
            action = "continue"
    elif maneuver_type == 'on ramp':
        action = f"take the on ramp on the {maneuver_modifier}" if maneuver_modifier else "take the on ramp"
    elif maneuver_type == 'off ramp':
        action = f"take the exit on the {maneuver_modifier}" if maneuver_modifier else "take the exit"
    elif maneuver_type == 'fork':
        action = f"keep {maneuver_modifier}" if maneuver_modifier else "keep straight"
    elif maneuver_type == 'merge':
        action = "merge"
    elif maneuver_type == 'roundabout':
        action = "enter the roundabout"
    elif maneuver_type == 'roundabout exit':
        action = "exit the roundabout"
    elif maneuver_type == 'uturn':
        action = f"make a U-turn to the {maneuver_modifier}" if maneuver_modifier else "make a U-turn"
    elif maneuver_type == 'ferry':
        action = "take the ferry"

    # Distance prefix
    distance_prefix = format_distance_for_tts(distance_m)

    # Combine
    if distance_prefix:
        if street_name:
            return f"{distance_prefix}, {action} onto {street_name}"
        return f"{distance_prefix}, {action}"
    else:
        # Very close — just the action
        if street_name:
            return f"{action} onto {street_name}"
        return action.title() if action else "Continue"
