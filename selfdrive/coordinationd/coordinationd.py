#!/usr/bin/env python3
"""
Coordination Daemon (coordinationd) - Localization with OSM + SGM fusion.

Consolidates OSM localization, SGM localization, and position fusion
into a single 5Hz daemon. Coordinates odometry + GPS + map constraints.
Replaces: osm_localizer, sgm_localizer.

Architecture:
  mapd (1Hz) ──► mapData ──► MTSC/MSLC controllers
                    │
  ┌─────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   COORDINATIOND (5Hz)                                   │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ INPUTS                                                          │    │
│  │ ├── gpsLocationExternal ──► Raw GNSS                           │    │
│  │ ├── livePose ─────────────► Velocity/orientation               │    │
│  │ ├── mapData ──────────────► Road geometry (optional)           │    │
│  │ └── pointcloudProcessed ──► 3D points (SGM matching)           │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              ↓                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ MODULES                                                         │    │
│  │                                                                 │    │
│  │ OSMLocalizerModule                                              │    │
│  │ ├── Uses mapd OSM cache                                        │    │
│  │ ├── Map-match to road network                                  │    │
│  │ └── Output: RoadConstraint                                     │    │
│  │                                                                 │    │
│  │ SGMLocalizerModule                                              │    │
│  │ ├── Load SGM map tiles                                         │    │
│  │ ├── ICP match pointcloud                                       │    │
│  │ └── Output: RoadConstraint                                     │    │
│  │                                                                 │    │
│  │ FusionEngine                                                    │    │
│  │ ├── Dead reckoning                                             │    │
│  │ ├── GNSS fusion                                                │    │
│  │ ├── OSM road constraint                                        │    │
│  │ └── SGM geometry confirmation                                  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              ↓                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ OUTPUTS                                                         │    │
│  │ ├── fusedPosition ───────► ECEF position (pathd/navd)          │    │
│  │ ├── osmCorrectedPose ────► Debug: OSM match                    │    │
│  │ └── sgmCorrectedPose ────► Debug: SGM match                    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘

Key Design:
- Single daemon at 5Hz (was 2 separate daemons: osm_localizer + sgm_localizer)
- ECEF internally for accurate fusion
- Uses mapd's OSM cache for road geometry

Platform Support:
- RK3588: OSM + SGM (80mm baseline)
"""

from __future__ import annotations

import math
import time

import numpy as np

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.transformations.coordinates import geodetic2ecef

# Import localization modules
from openpilot.selfdrive.coordinationd.osm_localizer import (
    OSMLocalizerModule, MapMatchResult
)
from openpilot.selfdrive.coordinationd.sgm_localizer import SGMLocalizerModule
from openpilot.selfdrive.coordinationd.fusion import FusionEngine, PositionEstimate, GNSSMeasurement, RoadConstraint


class GlobalD:
    """
    Global Position Daemon with OSM localization, SGM localization, and fusion.
    Subscribes to mapData from mapd for road geometry (optional).
    """

    UPDATE_RATE = 5.0  # Hz

    def __init__(self):
        set_daemon_affinity("coordinationd")

        self.params = Params()
        self.enabled = self.params.get_bool("EOPGlobaldEnabled")
        self.use_osm = self.params.get_bool("EOPOsmLocalizerEnabled")
        self.use_sgm = self.params.get_bool("EOPSGMLocalizerEnabled")

        # Messaging
        self.pm = messaging.PubMaster([
            'fusedPosition',
            'osmCorrectedPose',
            'sgmCorrectedPose',
        ])
        self.sm = messaging.SubMaster([
            'livePose',
            'gpsLocationExternal',
            'pointcloudProcessed',
            'stereoGround',
        ])

        # Modules
        self.osm_localizer = OSMLocalizerModule()
        self.sgm_localizer = SGMLocalizerModule(self.params)
        self.fusion_engine = FusionEngine()

        # State
        self.current_position: PositionEstimate | None = None
        self.last_gnss: GNSSMeasurement | None = None
        self.last_osm: RoadConstraint | None = None
        self.last_sgm: RoadConstraint | None = None

        # Stats
        self.frame_count = 0
        self.gnss_count = 0
        self.osm_count = 0
        self.sgm_count = 0

        cloudlog.info(
            f"GlobalD: initialized (enabled={self.enabled}, " +
            f"use_osm={self.use_osm}, use_sgm={self.use_sgm})"
        )

    def _update_from_gnss(self) -> None:
        """Update from GPS."""
        if not self.sm.updated['gpsLocationExternal']:
            return

        gps = self.sm['gpsLocationExternal']
        if gps.accuracy > 50.0:
            return

        lat, lon, alt = gps.latitude, gps.longitude, gps.altitude
        ecef = geodetic2ecef([lat, lon, alt])
        is_rtk = gps.accuracy < 0.1

        self.last_gnss = GNSSMeasurement(
            lat=lat, lon=lon, alt=alt, timestamp=time.monotonic(),
            accuracy=gps.accuracy, speed=gps.speed,
            speed_accuracy=gps.speedAccuracy, bearing=gps.bearingDeg,
            ecef=ecef, is_rtk=is_rtk
        )
        self.gnss_count += 1

    def _update_osm_localizer(self) -> None:
        """Run OSM map matching."""
        if not self.use_osm:
            return
        if not self.sm.updated['livePose']:
            return

        pose = self.sm['livePose']

        heading = math.degrees(pose.orientationNED.z) if hasattr(pose, 'orientationNED') else 0.0
        vx, vy = pose.velocityDevice.x, pose.velocityDevice.y
        speed = math.sqrt(vx * vx + vy * vy)

        if self.current_position is None and self.last_gnss is None:
            return

        base_lat = self.current_position.lat if self.current_position else self.last_gnss.lat
        base_lon = self.current_position.lon if self.current_position else self.last_gnss.lon

        match = self.osm_localizer.match(base_lat, base_lon, heading, speed)

        if match and match.confidence >= 0.5:
            self.last_osm = RoadConstraint(
                lat=match.matched_lat, lon=match.matched_lon,
                road_width=0.0, confidence=match.confidence, source="osm"
            )
            self.osm_count += 1
            self._publish_osm_pose(match, base_lat, base_lon)

    def _update_sgm_localizer(self) -> None:
        """Run SGM pointcloud matching."""
        if not self.use_sgm or not self.sgm_localizer.enabled:
            return

        pc_msg = self.sm['pointcloudProcessed'] if self.sm.updated['pointcloudProcessed'] else None
        sg_msg = self.sm['stereoGround'] if self.sm.updated['stereoGround'] else None

        if not pc_msg and not sg_msg:
            return

        live_pc = self.sgm_localizer.extract_pointcloud(pc_msg, sg_msg)
        if live_pc is None:
            return

        if pc_msg and pc_msg.hasGeoPosition:
            lat, lon, alt = pc_msg.geoLat, pc_msg.geoLon, pc_msg.geoAlt
        elif self.current_position:
            lat, lon, alt = self.current_position.lat, self.current_position.lon, self.current_position.alt
        elif self.last_gnss:
            lat, lon, alt = self.last_gnss.lat, self.last_gnss.lon, self.last_gnss.alt
        else:
            return

        tile_id = self.sgm_localizer._get_tile_id(lat, lon)
        if tile_id != self.sgm_localizer.current_tile_id:
            self.sgm_localizer.current_tile_id = tile_id
            self.sgm_localizer._load_map_tile(tile_id)

        map_tile = self.sgm_localizer.map_tiles.get(tile_id)
        if map_tile is None:
            return

        match = self.sgm_localizer.match_pointcloud(live_pc, map_tile)

        if match:
            from openpilot.common.transformations.coordinates import ecef2geodetic
            base_ecef = geodetic2ecef([lat, lon, alt])
            corrected_ecef = base_ecef + np.array([match.dx, match.dy, match.dz])
            corrected_geo = ecef2geodetic(corrected_ecef)

            self.last_sgm = RoadConstraint(
                lat=corrected_geo[0], lon=corrected_geo[1],
                road_width=12.0, confidence=match.confidence, source="sgm"
            )
            self.sgm_count += 1
            self._publish_sgm_pose(base_ecef, match)

    def _publish_osm_pose(self, match: MapMatchResult, orig_lat: float, orig_lon: float) -> None:
        """Publish OSM-corrected pose for debugging."""
        msg = messaging.new_message('osmCorrectedPose')
        pose = msg.osmCorrectedPose

        pose.timestamp = int(time.monotonic() * 1e9)
        pose.originalLat = orig_lat
        pose.originalLon = orig_lon
        pose.correctedLat = match.matched_lat
        pose.correctedLon = match.matched_lon
        pose.correctedHeading = match.matched_heading
        pose.wayId = match.way_id
        pose.roadName = match.road_name
        pose.roadType = match.road_type
        pose.laneIndex = match.lane_index
        pose.isOneway = match.is_oneway
        pose.confidence = match.confidence
        pose.distanceToRoad = match.distance_to_road

        self.pm.send('osmCorrectedPose', msg)

    def _publish_sgm_pose(self, base_ecef: np.ndarray, match) -> None:
        """Publish SGM-corrected pose for debugging."""
        msg = messaging.new_message('sgmCorrectedPose')

        msg.sgmCorrectedPose.position.x = float(base_ecef[0]) + match.dx
        msg.sgmCorrectedPose.position.y = float(base_ecef[1]) + match.dy
        msg.sgmCorrectedPose.position.z = float(base_ecef[2]) + match.dz
        msg.sgmCorrectedPose.orientation.x = 0.0
        msg.sgmCorrectedPose.orientation.y = 0.0
        msg.sgmCorrectedPose.orientation.z = 0.0
        msg.sgmCorrectedPose.orientation.w = 1.0
        msg.sgmCorrectedPose.confidence = match.confidence
        msg.sgmCorrectedPose.inliers = match.inliers
        msg.sgmCorrectedPose.mapSource = 'SGM'

        self.pm.send('sgmCorrectedPose', msg)

    def _fuse_position(self) -> PositionEstimate | None:
        """Fuse all position sources using FusionEngine."""
        fused = self.fusion_engine.fuse(
            current_position=self.current_position,
            gnss=self.last_gnss,
            osm_constraint=self.last_osm,
            sgm_constraint=self.last_sgm,
            use_osm=self.use_osm,
            use_sgm=self.use_sgm
        )

        if fused:
            self.current_position = fused

        return fused

    def _publish_position(self, position: PositionEstimate) -> None:
        """Publish fused position."""
        msg = messaging.new_message('fusedPosition')

        msg.fusedPosition.positionECEF.x = position.ecef[0]
        msg.fusedPosition.positionECEF.y = position.ecef[1]
        msg.fusedPosition.positionECEF.z = position.ecef[2]
        msg.fusedPosition.positionECEF.xStd = math.sqrt(position.covariance[0, 0])
        msg.fusedPosition.positionECEF.yStd = math.sqrt(position.covariance[1, 1])
        msg.fusedPosition.positionECEF.zStd = math.sqrt(position.covariance[2, 2])

        msg.fusedPosition.positionGeodetic.latitude = position.lat
        msg.fusedPosition.positionGeodetic.longitude = position.lon
        msg.fusedPosition.positionGeodetic.altitude = position.alt

        msg.fusedPosition.source = position.source
        msg.fusedPosition.confidence = position.confidence
        msg.fusedPosition.onRoad = position.on_road
        msg.fusedPosition.roadWidth = position.road_width

        self.pm.send('fusedPosition', msg)

    def update(self) -> None:
        """Main update loop."""
        self.sm.update(0)

        if not self.enabled:
            return

        # Update inputs
        self._update_from_gnss()
        self._update_osm_localizer()
        self._update_sgm_localizer()

        # Fuse and publish
        fused = self._fuse_position()

        if fused:
            self._publish_position(fused)

            if self.frame_count % 50 == 0:
                cloudlog.info(
                    f"GlobalD: lat={fused.lat:.6f}, lon={fused.lon:.6f}, " +
                    f"conf={fused.confidence:.2f}, source={fused.source}"
                )

        self.frame_count += 1

    def run(self) -> None:
        """Main loop."""
        cloudlog.info("GlobalD: starting localization daemon at 5Hz")

        rk = Ratekeeper(self.UPDATE_RATE, None)

        try:
            while True:
                self.update()
                rk.keep_time()
        except KeyboardInterrupt:
            cloudlog.info("GlobalD: shutting down")
        except Exception:
            raise


def main() -> int:
    """Entry point."""
    try:
        GlobalD().run()
        return 0
    except Exception as e:
        cloudlog.exception(f"CoordinationD fatal error: {e}")
        raise


if __name__ == "__main__":
    exit(main())
