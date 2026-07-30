#!/usr/bin/env python3
"""
NavD - Navigation Daemon for EOP (OpenPilot)

Valhalla/OSM-based turn-by-turn navigation (OFFLINE).
NavPilot app sets the destination; navd calls LOCAL Valhalla to compute the route.
No internet required - runs entirely on-device.

Requirements:
    - Valhalla service running locally on port 8002
    - Offline tiles at /data/valhalla/tiles/
    
Destination is written to the NavDestination param by bluetoothd/spp.py
(NCP 0x0302) or by set_destination.py.

Usage:
    # Set destination (from NavPilot app via BLE, or CLI)
    python3 selfdrive/navd/set_destination.py 32.7116 -117.1256 "Taco Bell"
"""

import json
import os
import time

import requests

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.navd.helpers import (
    Coordinate, distance_along_geometry,
    parse_valhalla_maneuvers, decode_polyline6,
    generate_tts_text,
)

# OpenPilot: OFFLINE ONLY - No internet required
# For online/cloud routing, use VisionPilot (successor)
# Valhalla service runs locally on port 8002
# Tiles stored at: /data/media/0/valhalla/
LOCAL_VALHALLA_URL = "http://127.0.0.1:8002/route"
LOCAL_VALHALLA_TILES = "/data/media/0/valhalla/valhalla_tiles.tar"
LOCAL_VALHALLA_CONFIG = "/data/media/0/valhalla/valhalla.json"

REROUTE_DISTANCE = 25       # metres — reroute threshold
MIN_QUERY_INTERVAL = 5.0    # seconds between API calls
MAX_RECOMPUTE_BACKOFF = 6   # max exponent for retry backoff


class NavD:
    """Navigation daemon — Valhalla/OSM routing, destination from NavPilot."""

    def __init__(self):
        set_daemon_affinity("navd")
        self.pm = messaging.PubMaster(['navInstruction', 'navRoute', 'ttsRequest'])
        self.sm = messaging.SubMaster(['gpsLocationExternal', 'fusedPosition', 'carState'])

        self.params = Params()
        self.valhalla_url = self._get_valhalla_url()

        # Navigation state
        self.last_position: Coordinate | None = None
        self.last_bearing: float | None = None
        self.nav_destination: Coordinate | None = None
        self.nav_destination_name: str = ""

        # Route state — list of parsed maneuver dicts + flat coordinate list
        self.maneuvers: list | None = None
        self.shape: list[Coordinate] | None = None  # full decoded polyline
        self.step_idx: int | None = None

        # Rate limiting
        self.last_query_time = 0.0
        self.recompute_backoff = 0
        self.recompute_countdown = 0
        self._last_route_publish_t = 0.0

        # TTS announcement tracking — prevent duplicate announcements
        # Stores (step_idx, threshold_key) tuples that have been announced
        self.announced: set[tuple[int, str]] = set()
        self.nav_voice_enabled = self.params.get_bool("EOPNavVoiceEnabled")

        # Log initialization mode - OpenPilot is OFFLINE ONLY
        tile_status = self._check_tile_status()
        if self._is_local_valhalla_available():
            cloudlog.info(f"NavD initialized (OFFLINE): tiles={tile_status['tiles_size_mb']} MB")
        elif tile_status['tiles_exist']:
            cloudlog.warning(f"NavD: Tiles installed ({tile_status['tiles_size_mb']} MB) but service not running")
        else:
            cloudlog.error("NavD: No offline tiles installed. Run: tile_manager.py ensure <region>")

    def _get_valhalla_url(self) -> str:
        """Get Valhalla URL - OpenPilot is OFFLINE ONLY."""
        # Check for explicit override (development only)
        url = self.params.get("EOPValhallaUrl")
        if url:
            return url.strip()
        
        # OpenPilot requires local Valhalla - no online fallback
        if not self._is_local_valhalla_available():
            cloudlog.error(
                "NavD: Local Valhalla not available. "
                "Install tiles: python selfdrive/navd/tile_manager.py ensure <region>\n"
                "For online/cloud routing, use VisionPilot (successor)"
            )
        
        return LOCAL_VALHALLA_URL
    
    def _is_local_valhalla_available(self) -> bool:
        """Check if local Valhalla service is running and tiles are available."""
        import socket
        
        # Check if tiles exist
        if not os.path.exists(LOCAL_VALHALLA_TILES):
            return False
        
        # Check if service is listening on port 8002
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 8002))
            sock.close()
            return result == 0
        except Exception:
            return False
    
    def _check_tile_status(self) -> dict:
        """Check status of local Valhalla tiles."""
        import os
        tiles_exist = os.path.exists(LOCAL_VALHALLA_TILES)
        config_exist = os.path.exists(LOCAL_VALHALLA_CONFIG)
        
        tiles_size_mb = 0
        if tiles_exist:
            tiles_size_mb = os.path.getsize(LOCAL_VALHALLA_TILES) / (1024 * 1024)
        
        return {
            'tiles_exist': tiles_exist,
            'tiles_size_mb': round(tiles_size_mb, 1),
            'config_exist': config_exist,
            'tiles_path': LOCAL_VALHALLA_TILES,
        }

    # ------------------------------------------------------------------
    # Location
    # ------------------------------------------------------------------

    def update_location(self) -> bool:
        # Prefer coordinationd fusedPosition (road-constrained, best accuracy)
        if self.sm.updated['fusedPosition']:
            fused = self.sm['fusedPosition']
            if fused.confidence > 0.3:
                self.last_position = Coordinate(
                    fused.positionGeodetic.latitude,
                    fused.positionGeodetic.longitude,
                )
                # Bearing preserved from last GPS if not in fused msg
                return True

        # Fallback: raw GPS (cold-start or coordinationd not running)
        if self.sm.updated['gpsLocationExternal']:
            gps = self.sm['gpsLocationExternal']
            self.last_position = Coordinate(gps.latitude, gps.longitude)
            if gps.bearingDeg > 0:
                self.last_bearing = gps.bearingDeg
            return True

        return False

    # ------------------------------------------------------------------
    # Destination
    # ------------------------------------------------------------------

    def update_destination(self) -> bool:
        dest_json = self.params.get("NavDestination")

        if not dest_json:
            if self.nav_destination is not None:
                cloudlog.info("NavD: Destination cleared")
                self.nav_destination = None
                self._clear_route()
                self._reset_backoff()
            return False

        try:
            dest_data = json.loads(dest_json)
            new_dest = Coordinate(dest_data['latitude'], dest_data['longitude'])

            if (self.nav_destination is None or
                    self.nav_destination.distance_to(new_dest) > 10):
                cloudlog.info(f"NavD: New destination: {dest_data.get('place_name', 'Unknown')}")
                self.nav_destination = new_dest
                self.nav_destination_name = dest_data.get('place_name', '')
                self._clear_route()
                self._reset_backoff()
                return True

        except (json.JSONDecodeError, KeyError) as e:
            cloudlog.error(f"NavD: Invalid destination format: {e}")

        return False

    # ------------------------------------------------------------------
    # Route calculation — Valhalla
    # ------------------------------------------------------------------

    def should_recompute(self) -> bool:
        if self.maneuvers is None:
            return True

        if self.last_position and self.step_idx is not None and self.maneuvers:
            step = self.maneuvers[self.step_idx]
            step_coords = self._step_shape(step)
            if step_coords:
                along = distance_along_geometry(step_coords, self.last_position)
                remaining = step['length_m'] - along
                if remaining < -REROUTE_DISTANCE:
                    cloudlog.info("NavD: Off route, recomputing")
                    return True

        return False

    def _reset_backoff(self):
        self.recompute_backoff = 0
        self.recompute_countdown = 0

    def _clear_route(self):
        self.maneuvers = None
        self.shape = None
        self.step_idx = None
        self._clear_announcements()

    def _publish_tts(self, text: str, priority: int = 1):
        """Publish a TTS request message."""
        if not self.nav_voice_enabled:
            return
        msg = messaging.new_message('ttsRequest')
        msg.ttsRequest.text = text
        msg.ttsRequest.priority = priority
        msg.ttsRequest.interrupt = True
        self.pm.send('ttsRequest', msg)
        cloudlog.info(f"NavD: TTS: {text}")

    def calculate_route(self):
        if not self.last_position or not self.nav_destination:
            return

        # Rate limiting with exponential backoff
        elapsed = time.monotonic() - self.last_query_time
        min_interval = MIN_QUERY_INTERVAL * (2 ** self.recompute_backoff)
        if elapsed < min_interval:
            return

        # Track if this is a reroute (we had a previous route)
        had_previous_route = self.maneuvers is not None

        cloudlog.info(f"NavD: Calculating route to {self.nav_destination_name} via Valhalla")

        body = {
            "locations": [
                {"lat": self.last_position.latitude, "lon": self.last_position.longitude,
                 "type": "break"},
                {"lat": self.nav_destination.latitude, "lon": self.nav_destination.longitude,
                 "type": "break"},
            ],
            "costing": "auto",
            "directions_options": {"units": "kilometers", "language": "en-US"},
        }

        if self.last_bearing is not None:
            body["locations"][0]["heading"] = int(self.last_bearing)
            body["locations"][0]["heading_tolerance"] = 45

        try:
            resp = requests.post(self.valhalla_url, json=body, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            trip = data.get('trip') or data  # valhalla wraps in 'trip'
            legs = trip.get('legs', [])
            if not legs:
                cloudlog.warning("NavD: No route legs returned")
                return

            leg = legs[0]
            self.shape = decode_polyline6(leg['shape'])
            self.maneuvers = parse_valhalla_maneuvers(leg['maneuvers'], self.shape)
            self.step_idx = 0
            self.last_query_time = time.monotonic()
            self.recompute_backoff = 0

            total_km = sum(m['length'] for m in leg['maneuvers'])
            cloudlog.info(
                f"NavD: Route calculated: {len(self.maneuvers)} maneuvers, {total_km:.1f} km"
            )
            self._publish_route()

            # Announce route recalculation if we had a previous route
            if had_previous_route:
                self._publish_tts("Recalculating route.", priority=1)

        except requests.exceptions.ConnectionError as e:
            # Local Valhalla not running - offline only, no fallback
            tile_status = self._check_tile_status()
            if not tile_status['tiles_exist']:
                cloudlog.error(
                    f"NavD: Offline tiles not found. "
                    f"Install: python selfdrive/navd/tile_manager.py ensure <region>"
                )
            else:
                cloudlog.error(
                    f"NavD: Local Valhalla service not running. "
                    f"Tiles: {tile_status['tiles_size_mb']} MB installed. "
                    f"Ensure valhalla_service process is running."
                )
            self.recompute_backoff = min(MAX_RECOMPUTE_BACKOFF, self.recompute_backoff + 1)
        except requests.exceptions.RequestException as e:
            cloudlog.error(f"NavD: Valhalla request failed: {e}")
            self.recompute_backoff = min(MAX_RECOMPUTE_BACKOFF, self.recompute_backoff + 1)
        except Exception as e:
            cloudlog.exception(f"NavD: Unexpected error: {e}")

    # ------------------------------------------------------------------
    # Route progress
    # ------------------------------------------------------------------

    def _step_shape(self, step: dict) -> list[Coordinate]:
        """Return the coordinate slice for a maneuver step."""
        if self.shape is None:
            return []
        begin = step.get('begin_shape_index', 0)
        end = step.get('end_shape_index', len(self.shape) - 1)
        return self.shape[begin:end + 1]

    def update_route_progress(self):
        if not self.maneuvers or not self.last_position or self.step_idx is None:
            return

        while self.step_idx < len(self.maneuvers) - 1:
            step = self.maneuvers[self.step_idx]
            step_coords = self._step_shape(step)
            if not step_coords:
                break

            along = distance_along_geometry(step_coords, self.last_position)
            remaining = step['length_m'] - along
            if remaining < 10:
                self.step_idx += 1
                cloudlog.debug(f"NavD: Advanced to step {self.step_idx}")
            else:
                break

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def send_instruction(self):
        msg = messaging.new_message('navInstruction')

        if not self.maneuvers or self.step_idx is None or self.step_idx >= len(self.maneuvers):
            msg.valid = False
            self.pm.send('navInstruction', msg)
            return

        step = self.maneuvers[self.step_idx]
        step_coords = self._step_shape(step)

        along = distance_along_geometry(step_coords, self.last_position) if step_coords else 0.0
        distance_to_maneuver = max(0.0, step['length_m'] - along)

        msg.navInstruction.maneuverPrimaryText = step.get('instruction', '')
        msg.navInstruction.maneuverType = step.get('maneuver_type', '')
        msg.navInstruction.maneuverModifier = step.get('maneuver_modifier', '')
        msg.navInstruction.maneuverDistance = distance_to_maneuver
        msg.navInstruction.showFull = distance_to_maneuver < 200

        # Totals
        remaining_distance = distance_to_maneuver
        remaining_time = step['time'] * (distance_to_maneuver / max(step['length_m'], 1))
        for i in range(self.step_idx + 1, len(self.maneuvers)):
            remaining_distance += self.maneuvers[i]['length_m']
            remaining_time += self.maneuvers[i]['time']

        msg.navInstruction.distanceRemaining = remaining_distance
        msg.navInstruction.timeRemaining = remaining_time
        msg.navInstruction.timeRemainingTypical = remaining_time

        # Speed limit from step annotation if present
        speed_limit = step.get('speed_limit_ms')
        if speed_limit:
            msg.navInstruction.speedLimit = speed_limit

        self.pm.send('navInstruction', msg)

    def _check_tts_announcement(self):
        """Check if a TTS voice announcement should be triggered."""
        if not self.nav_voice_enabled:
            return
        if not self.maneuvers or self.step_idx is None or self.step_idx >= len(self.maneuvers):
            return

        step = self.maneuvers[self.step_idx]
        step_coords = self._step_shape(step)
        along = distance_along_geometry(step_coords, self.last_position) if step_coords else 0.0
        distance_to_maneuver = max(0.0, step['length_m'] - along)

        # Special case: arrival announcement when very close
        mtype = step.get('maneuver_type', '')
        if mtype == 'arrive' and distance_to_maneuver < 50:
            arrival_id = (self.step_idx, "arrival")
            if arrival_id not in self.announced:
                self.announced.add(arrival_id)
                street = ""
                if step.get('instruction'):
                    instr = step['instruction']
                    if ' onto ' in instr:
                        street = instr.split(' onto ')[-1].rstrip('.')
                    elif ' toward ' in instr:
                        street = instr.split(' toward ')[-1].rstrip('.')
                tts_text = generate_tts_text('arrive', '', 0, street)
                if tts_text:
                    self._publish_tts(tts_text, priority=1)
            return

        # Determine which threshold applies
        threshold_key = None
        if distance_to_maneuver > 500:
            # Far away — no announcement yet
            return
        elif distance_to_maneuver > 200:
            threshold_key = "approach"
        elif distance_to_maneuver > 80:
            threshold_key = "imminent"
        elif distance_to_maneuver > 20:
            threshold_key = "execute"
        else:
            # Very close — already announced or no need
            return

        # Check if already announced for this step + threshold
        announcement_id = (self.step_idx, threshold_key)
        if announcement_id in self.announced:
            return

        # Mark as announced
        self.announced.add(announcement_id)

        # Generate TTS text
        street = ""
        if step.get('instruction'):
            # Extract street name from instruction if possible
            # Valhalla format: "Turn right onto Main Street."
            instr = step['instruction']
            if ' onto ' in instr:
                street = instr.split(' onto ')[-1].rstrip('.')
            elif ' toward ' in instr:
                street = instr.split(' toward ')[-1].rstrip('.')

        tts_text = generate_tts_text(
            maneuver_type=step.get('maneuver_type', ''),
            maneuver_modifier=step.get('maneuver_modifier', ''),
            distance_m=distance_to_maneuver,
            street_name=street,
        )

        if not tts_text:
            return

        cloudlog.info(f"NavD: TTS [{threshold_key}]: {tts_text}")

        # Publish ttsRequest
        msg = messaging.new_message('ttsRequest')
        msg.ttsRequest.text = tts_text
        msg.ttsRequest.priority = 1  # high priority (navigation)
        msg.ttsRequest.interrupt = True  # allow newer nav instruction to interrupt
        self.pm.send('ttsRequest', msg)

    def _clear_announcements(self):
        """Clear announcement tracking when route changes."""
        self.announced.clear()

    def _publish_route(self):
        """Publish navRoute with up to 500m of geometry."""
        if not self.shape:
            return

        msg = messaging.new_message('navRoute')
        total_distance = 0.0
        coord_count = 0

        for i, coord in enumerate(self.shape):
            if total_distance > 500:
                break
            coord_count += 1
            if i > 0:
                total_distance += self.shape[i - 1].distance_to(coord)

        coords = msg.navRoute.init('coordinates', coord_count)
        for i, coord in enumerate(self.shape[:coord_count]):
            coords[i].latitude = coord.latitude
            coords[i].longitude = coord.longitude

        if self.nav_destination:
            msg.navRoute.routeId = (
                hash((self.nav_destination.latitude, self.nav_destination.longitude)) & 0xFFFFFFFF
            )

        self.pm.send('navRoute', msg)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def update(self):
        self.sm.update(0)
        self.update_location()
        dest_changed = self.update_destination()

        if dest_changed or self.should_recompute():
            self.calculate_route()

        if self.maneuvers:
            self.update_route_progress()
            self.send_instruction()
            self._check_tts_announcement()
            now = time.monotonic()
            if now - self._last_route_publish_t >= 5.0:
                self._last_route_publish_t = now
                self._publish_route()

    def run(self):
        cloudlog.info("NavD: Starting (Valhalla/OSM)")
        rk = Ratekeeper(1.0)
        while True:
            try:
                self.update()
            except Exception:
                cloudlog.exception("NavD: Update failed")
            rk.keep_time()


def main():
    navd = NavD()
    navd.run()


if __name__ == "__main__":
    main()
