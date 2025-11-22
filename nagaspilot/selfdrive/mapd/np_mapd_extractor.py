#!/usr/bin/env python3
"""
NPMTSCDataExtractor - Extract curve and road data from MAPD binary for M-TSC system
Based on proven patterns and designed to replace simulation with real OSM data
"""
import json
import time
import math
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

import cereal.messaging as messaging
from openpilot.common.params import Params


class RoadType(Enum):
    """OSM highway types mapped to M-TSC categories."""
    MOTORWAY = "motorway"
    TRUNK = "trunk" 
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    RESIDENTIAL = "residential"
    SERVICE = "service"
    UNKNOWN = "unknown"


@dataclass
class CurveData:
    """Curve information extracted from OSM data."""
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    radius: float  # meters
    curvature: float  # 1/radius
    length: float  # meters
    direction: str  # "left" or "right"
    road_type: RoadType
    speed_limit: Optional[float] = None
    confidence: float = 1.0


@dataclass
class RoadSegment:
    """Road segment with associated curve data."""
    segment_id: str
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    curves: List[CurveData]
    road_type: RoadType
    speed_limit: Optional[float] = None


class NPMapdExtractor:
    """
    Extract curve and road data from MAPD binary output for M-TSC integration.
    Replaces hash-based simulation with geometry-based curve detection.
    """
    
    # OSM highway tag mapping to road types
    HIGHWAY_TYPE_MAP = {
        "motorway": RoadType.MOTORWAY,
        "motorway_link": RoadType.MOTORWAY,
        "trunk": RoadType.TRUNK,
        "trunk_link": RoadType.TRUNK,
        "primary": RoadType.PRIMARY,
        "primary_link": RoadType.PRIMARY,
        "secondary": RoadType.SECONDARY,
        "secondary_link": RoadType.SECONDARY,
        "tertiary": RoadType.TERTIARY,
        "tertiary_link": RoadType.TERTIARY,
        "residential": RoadType.RESIDENTIAL,
        "living_street": RoadType.RESIDENTIAL,
        "service": RoadType.SERVICE,
        "unclassified": RoadType.TERTIARY,
    }
    
    # Speed limits by road type (km/h) - fallback when OSM data missing
    DEFAULT_SPEED_LIMITS = {
        RoadType.MOTORWAY: 120,
        RoadType.TRUNK: 100,
        RoadType.PRIMARY: 80,
        RoadType.SECONDARY: 60,
        RoadType.TERTIARY: 50,
        RoadType.RESIDENTIAL: 30,
        RoadType.SERVICE: 20,
        RoadType.UNKNOWN: 50
    }
    
    def __init__(self, params: Params):
        self.params = params
        self.mp = self._get_mem_params()
        self.sm = messaging.SubMaster(["gpsLocation"])
        
        # Cache and state (optimized for 500m M-TSC focus)
        self.last_position: Optional[Tuple[float, float]] = None
        self.cached_segments: Dict[str, RoadSegment] = {}
        self.cache_radius = 1000.0  # 1km cache - sufficient for 500m lookahead with buffer
        self.last_mapd_query = 0.0
        self.query_interval = 0.5  # 0.5s for responsive 500m updates
        self.max_cached_segments = 200  # Reduced for 500m focus - better memory efficiency
        
        # Curve detection parameters
        self.min_curve_radius = 50.0  # meters - minimum radius to consider a curve
        self.max_curve_radius = 2000.0  # meters - maximum radius for M-TSC relevance
        self.curve_length_threshold = 30.0  # meters - minimum curve length
        self.angle_threshold = 5.0  # degrees - minimum angle change for curve detection
        
        # FOCUSED 500m lookahead for M-TSC precision
        self.mtsc_lookahead_distance = 500.0  # 500m - optimal for M-TSC braking calculations
        self.lookahead_buffer = 100.0  # 100m buffer for data fetching (600m total fetch)
        self.max_fetch_distance = 600.0  # Fetch 600m to ensure 500m coverage
        
        # High-resolution waypoint tracking optimized for 500m
        self.target_waypoint_spacing = 10.0  # 10m spacing for high accuracy
        self.waypoints_in_500m = 50  # 50 waypoints in 500m at 10m spacing
        self.curve_analysis_window = 5  # 5-point window (50m analysis span)
    
    def _get_mem_params(self) -> Params:
        """Get memory params for real-time communication."""
        import platform
        return Params("/dev/shm/params") if platform.system() != "Darwin" else Params()
    
    def _haversine_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two GPS points in meters."""
        R = 6371000  # Earth radius in meters
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2) 
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        
        a = (math.sin(delta_lat / 2) ** 2 +
             math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return R * c
    
    def _bearing_between_points(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate bearing between two GPS points in degrees."""
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lon = math.radians(lon2 - lon1)
        
        x = math.sin(delta_lon) * math.cos(lat2_rad)
        y = (math.cos(lat1_rad) * math.sin(lat2_rad) - 
             math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon))
        
        bearing_rad = math.atan2(x, y)
        bearing_deg = math.degrees(bearing_rad)
        
        return (bearing_deg + 360) % 360
    
    def _interpolate_waypoints(self, coordinates: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """Interpolate waypoints to achieve target spacing for accurate curve calculation."""
        if len(coordinates) < 2:
            return coordinates
        
        interpolated = []
        
        for i in range(len(coordinates) - 1):
            lat1, lon1 = coordinates[i]
            lat2, lon2 = coordinates[i + 1]
            
            # Calculate distance between consecutive points
            segment_distance = self._haversine_distance(lat1, lon1, lat2, lon2)
            
            # Add start point
            interpolated.append((lat1, lon1))
            
            # If segment is longer than target spacing, interpolate
            if segment_distance > self.target_waypoint_spacing:
                num_interpolations = int(segment_distance / self.target_waypoint_spacing)
                
                for j in range(1, num_interpolations):
                    # Linear interpolation ratio
                    ratio = j / num_interpolations
                    
                    # Interpolate latitude and longitude
                    interp_lat = lat1 + (lat2 - lat1) * ratio
                    interp_lon = lon1 + (lon2 - lon1) * ratio
                    
                    interpolated.append((interp_lat, interp_lon))
        
        # Add final point
        if coordinates:
            interpolated.append(coordinates[-1])
        
        return interpolated
    
    def _calculate_high_resolution_curvature(self, waypoints: List[Tuple[float, float]], 
                                           position: int, window_size: int = 5) -> Optional[float]:
        """Calculate curvature using high-resolution waypoint data with improved accuracy."""
        if position < window_size // 2 or position >= len(waypoints) - window_size // 2:
            return None
        
        # Use symmetric window around position
        start_idx = position - window_size // 2
        end_idx = position + window_size // 2 + 1
        
        if end_idx > len(waypoints):
            return None
        
        window_points = waypoints[start_idx:end_idx]
        
        if len(window_points) < 3:
            return None
        
        # Use first, middle, and last points for circle fitting
        p1 = window_points[0]
        p2 = window_points[len(window_points) // 2]
        p3 = window_points[-1]
        
        radius = self._calculate_curve_radius([p1, p2, p3])
        
        if radius and radius > 0:
            return 1.0 / radius  # Return curvature (1/radius)
        
        return None
    
    def _calculate_curve_radius(self, points: List[Tuple[float, float]]) -> Optional[float]:
        """
        Calculate curve radius from a sequence of GPS points using geometry.
        Uses three-point circle fitting method.
        """
        if len(points) < 3:
            return None
        
        try:
            # Use evenly spaced points for better accuracy
            if len(points) > 3:
                step = len(points) // 3
                p1 = points[0]
                p2 = points[step]
                p3 = points[2 * step]
            else:
                p1, p2, p3 = points[0], points[1], points[2]
            
            # Convert to local coordinates (meters from first point)
            lat1, lon1 = p1
            
            def to_local(lat: float, lon: float) -> Tuple[float, float]:
                # Approximate conversion to meters
                lat_m = (lat - lat1) * 111320  # meters per degree latitude
                lon_m = (lon - lon1) * 111320 * math.cos(math.radians(lat1))
                return lat_m, lon_m
            
            x1, y1 = 0.0, 0.0  # First point is origin
            x2, y2 = to_local(*p2)
            x3, y3 = to_local(*p3)
            
            # Circle fitting using determinant method
            d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
            
            if abs(d) < 1e-10:  # Points are collinear
                return None
            
            # Center of circle
            cx = ((x1*x1 + y1*y1) * (y2 - y3) + 
                  (x2*x2 + y2*y2) * (y3 - y1) + 
                  (x3*x3 + y3*y3) * (y1 - y2)) / d
            
            cy = ((x1*x1 + y1*y1) * (x3 - x2) + 
                  (x2*x2 + y2*y2) * (x1 - x3) + 
                  (x3*x3 + y3*y3) * (x2 - x1)) / d
            
            # Radius
            radius = math.sqrt(cx*cx + cy*cy)
            
            # Validate radius is reasonable
            if self.min_curve_radius <= radius <= self.max_curve_radius:
                return radius
            
        except Exception:
            pass
        
        return None
    
    def _determine_curve_direction(self, points: List[Tuple[float, float]]) -> str:
        """Determine if curve turns left or right."""
        if len(points) < 3:
            return "unknown"
        
        try:
            # Calculate bearing change
            start_lat, start_lon = points[0]
            mid_lat, mid_lon = points[len(points) // 2]
            end_lat, end_lon = points[-1]
            
            bearing1 = self._bearing_between_points(start_lat, start_lon, mid_lat, mid_lon)
            bearing2 = self._bearing_between_points(mid_lat, mid_lon, end_lat, end_lon)
            
            # Calculate bearing change
            bearing_diff = bearing2 - bearing1
            
            # Normalize to -180 to 180
            if bearing_diff > 180:
                bearing_diff -= 360
            elif bearing_diff < -180:
                bearing_diff += 360
            
            return "right" if bearing_diff > 0 else "left"
            
        except Exception:
            return "unknown"
    
    def _query_mapd_binary(self, lat: float, lon: float, radius: float = 1000.0) -> Optional[Dict[str, Any]]:
        """
        Query MAPD binary for OSM data around given coordinates.
        Returns structured data about roads and geometry.
        """
        binary_path = self.params.get("NpMapdBinary", encoding='utf-8')
        if not binary_path or not Path(binary_path).exists():
            return None
        
        try:
            # Create query for MAPD binary
            query = {
                "lat": lat,
                "lon": lon,
                "radius": radius,
                "include": ["highways", "geometry", "tags"]
            }
            
            # Execute MAPD binary with query
            cmd = [
                binary_path,
                "--format", "json",
                "--query", json.dumps(query)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0 and result.stdout:
                return json.loads(result.stdout)
                
        except Exception as e:
            # Log error for debugging but don't crash
            self.mp.put("MapdQueryError", str(e))
        
        return None
    
    def _parse_osm_ways(self, osm_data: Dict[str, Any]) -> List[RoadSegment]:
        """Parse OSM ways data into structured road segments with curves."""
        segments = []
        
        try:
            ways = osm_data.get("ways", [])
            nodes = {node["id"]: node for node in osm_data.get("nodes", [])}
            
            for way in ways:
                # Extract highway type
                highway_tag = way.get("tags", {}).get("highway", "unknown")
                road_type = self.HIGHWAY_TYPE_MAP.get(highway_tag, RoadType.UNKNOWN)
                
                # Skip non-highway ways
                if road_type == RoadType.UNKNOWN and highway_tag not in self.HIGHWAY_TYPE_MAP:
                    continue
                
                # Extract speed limit if available
                speed_limit = None
                maxspeed = way.get("tags", {}).get("maxspeed")
                if maxspeed:
                    try:
                        # Handle various maxspeed formats
                        if maxspeed.endswith(" mph"):
                            speed_limit = float(maxspeed.replace(" mph", "")) * 1.609344  # Convert to km/h
                        elif maxspeed.isdigit():
                            speed_limit = float(maxspeed)
                    except ValueError:
                        pass
                
                # Get node coordinates for geometry
                node_refs = way.get("nd", [])
                if len(node_refs) < 2:
                    continue
                
                coordinates = []
                for node_ref in node_refs:
                    if node_ref in nodes:
                        node = nodes[node_ref]
                        coordinates.append((node["lat"], node["lon"]))
                
                if len(coordinates) < 2:
                    continue
                
                # Detect curves in this way
                curves = self._detect_curves_in_way(coordinates, road_type, speed_limit)
                
                # Create road segment
                segment = RoadSegment(
                    segment_id=f"way_{way.get('id', 'unknown')}",
                    start_lat=coordinates[0][0],
                    start_lon=coordinates[0][1], 
                    end_lat=coordinates[-1][0],
                    end_lon=coordinates[-1][1],
                    curves=curves,
                    road_type=road_type,
                    speed_limit=speed_limit or self.DEFAULT_SPEED_LIMITS[road_type]
                )
                
                segments.append(segment)
                
        except Exception:
            pass
        
        return segments
    
    def _detect_curves_in_way(self, coordinates: List[Tuple[float, float]], 
                             road_type: RoadType, speed_limit: Optional[float]) -> List[CurveData]:
        """Detect curves within a single OSM way using high-resolution waypoint analysis."""
        curves = []
        
        if len(coordinates) < 3:
            return curves
        
        # Step 1: Interpolate waypoints for high-resolution analysis
        high_res_waypoints = self._interpolate_waypoints(coordinates)
        
        if len(high_res_waypoints) < 10:  # Need sufficient points for analysis
            return curves
        
        # Step 2: Calculate curvature at each waypoint
        curvatures = []
        for i in range(len(high_res_waypoints)):
            curvature = self._calculate_high_resolution_curvature(
                high_res_waypoints, i, window_size=7  # 7-point window for smooth calculation
            )
            curvatures.append(curvature)
        
        # Step 3: Identify curve segments where curvature exceeds threshold
        curve_segments = []
        in_curve = False
        curve_start_idx = 0
        min_curvature_threshold = 1.0 / self.max_curve_radius  # Minimum curvature to consider
        
        for i, curvature in enumerate(curvatures):
            if curvature and abs(curvature) > min_curvature_threshold:
                if not in_curve:
                    # Starting a new curve
                    in_curve = True
                    curve_start_idx = i
            else:
                if in_curve:
                    # Ending current curve
                    in_curve = False
                    curve_end_idx = i - 1
                    
                    # Only keep curves with sufficient length
                    if curve_end_idx - curve_start_idx >= 3:  # At least 3 high-res points
                        curve_segments.append((curve_start_idx, curve_end_idx))
        
        # Handle curve that extends to end of waypoints
        if in_curve and len(curvatures) - curve_start_idx >= 3:
            curve_segments.append((curve_start_idx, len(curvatures) - 1))
        
        # Step 4: Create CurveData objects for each segment
        for start_idx, end_idx in curve_segments:
            # Calculate average properties for this curve segment
            segment_curvatures = [c for c in curvatures[start_idx:end_idx+1] if c is not None]
            
            if not segment_curvatures:
                continue
            
            avg_curvature = sum(abs(c) for c in segment_curvatures) / len(segment_curvatures)
            radius = 1.0 / avg_curvature if avg_curvature > 0 else float('inf')
            
            # Skip if radius is outside our range
            if not (self.min_curve_radius <= radius <= self.max_curve_radius):
                continue
            
            # Get curve boundaries
            start_lat, start_lon = high_res_waypoints[start_idx]
            end_lat, end_lon = high_res_waypoints[end_idx]
            
            # Calculate actual curve length
            curve_length = 0.0
            for i in range(start_idx, end_idx):
                lat1, lon1 = high_res_waypoints[i]
                lat2, lon2 = high_res_waypoints[i + 1]
                curve_length += self._haversine_distance(lat1, lon1, lat2, lon2)
            
            # Only keep curves above minimum length threshold
            if curve_length < self.curve_length_threshold:
                continue
            
            # Determine curve direction from curvature sign
            positive_curvatures = sum(1 for c in segment_curvatures if c > 0)
            negative_curvatures = sum(1 for c in segment_curvatures if c < 0)
            
            if positive_curvatures > negative_curvatures:
                direction = "left"
            elif negative_curvatures > positive_curvatures:
                direction = "right"
            else:
                direction = "unknown"
            
            # Calculate confidence based on curve consistency and length
            curvature_consistency = max(positive_curvatures, negative_curvatures) / len(segment_curvatures)
            length_factor = min(1.0, curve_length / 100.0)
            confidence = curvature_consistency * length_factor
            
            curve = CurveData(
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                radius=radius,
                curvature=avg_curvature,
                length=curve_length,
                direction=direction,
                road_type=road_type,
                speed_limit=speed_limit,
                confidence=confidence
            )
            
            curves.append(curve)
        
        return curves
    
    def _find_relevant_curves(self, lat: float, lon: float, heading: float, 
                             look_ahead: float = None) -> List[CurveData]:
        """Find curves ahead of vehicle position within focused 500m M-TSC range."""
        if look_ahead is None:
            look_ahead = self.mtsc_lookahead_distance  # Use focused 500m lookahead
            
        relevant_curves = []
        
        for segment in self.cached_segments.values():
            for curve in segment.curves:
                # Calculate distance to curve start
                distance = self._haversine_distance(lat, lon, curve.start_lat, curve.start_lon)
                
                if distance <= look_ahead:
                    # Check if curve is roughly in driving direction
                    bearing_to_curve = self._bearing_between_points(lat, lon, curve.start_lat, curve.start_lon)
                    heading_diff = abs(bearing_to_curve - heading)
                    if heading_diff > 180:
                        heading_diff = 360 - heading_diff
                    
                    # Only include curves within 90 degrees of heading
                    if heading_diff <= 90:
                        # Add distance to curve data for easier processing
                        curve_with_distance = curve
                        curve_with_distance.distance_from_ego = distance
                        relevant_curves.append(curve_with_distance)
        
        # Sort by distance
        relevant_curves.sort(key=lambda c: c.distance_from_ego)
        
        return relevant_curves
    
    def _analyze_curve_sequence(self, curves: List[CurveData]) -> Dict[str, Any]:
        """Analyze curve sequence for better speed planning."""
        if len(curves) < 2:
            return {"sequence_type": "single", "complexity": "simple"}
        
        # Analyze curve sequence patterns
        sequence_info = {
            "total_curves": len(curves),
            "sequence_type": "single",
            "complexity": "simple",
            "tightest_radius": min(c.radius for c in curves),
            "sequence_length": curves[-1].distance_from_ego - curves[0].distance_from_ego if hasattr(curves[0], 'distance_from_ego') else 0
        }
        
        if len(curves) >= 2:
            # Check for S-curves (alternating directions)
            directions = [c.direction for c in curves[:4]]  # Look at first 4 curves
            if len(set(directions)) > 1:  # Multiple directions
                sequence_info["sequence_type"] = "s_curves"
                sequence_info["complexity"] = "complex"
            
            # Check for hairpin sequences (multiple tight curves)
            tight_curves = [c for c in curves if c.radius < 150]
            if len(tight_curves) >= 2:
                sequence_info["sequence_type"] = "hairpin_sequence"  
                sequence_info["complexity"] = "very_complex"
            
            # Check for highway ramp pattern (gradually tightening)
            radii = [c.radius for c in curves[:3]]
            if len(radii) >= 3 and all(radii[i] > radii[i+1] for i in range(len(radii)-1)):
                sequence_info["sequence_type"] = "tightening_ramp"
                sequence_info["complexity"] = "moderate"
        
        return sequence_info
    
    def _format_mtsc_curve_data(self, curves: List[CurveData]) -> Dict[str, Any]:
        """Format curve data for M-TSC controller consumption with multi-curve planning."""
        if not curves:
            return {}
        
        # Take the most relevant curve (closest)
        primary_curve = curves[0]
        
        # Analyze curve sequence for better planning
        sequence_info = self._analyze_curve_sequence(curves[:5])  # Analyze first 5 curves
        
        return {
            "has_curve": True,
            "curve_radius": primary_curve.radius,
            "curve_curvature": primary_curve.curvature,
            "curve_direction": primary_curve.direction,
            "curve_distance": getattr(primary_curve, 'distance_from_ego', 0),
            "curve_length": primary_curve.length,
            "road_type": primary_curve.road_type.value,
            "speed_limit": primary_curve.speed_limit,
            "confidence": primary_curve.confidence,
            "data_source": "osm_real_offline",
            "timestamp": time.time(),
            
            # Multi-curve sequence information (500m focused)
            "sequence_info": sequence_info,
            "lookahead_distance": self.mtsc_lookahead_distance,
            "mtsc_focused": True,  # Flag indicating 500m M-TSC optimization
            
            "all_curves": [
                {
                    "radius": c.radius,
                    "curvature": c.curvature, 
                    "direction": c.direction,
                    "distance": getattr(c, 'distance_from_ego', 0),
                    "confidence": c.confidence,
                    "length": c.length
                }
                for c in curves if getattr(c, 'distance_from_ego', 0) <= self.mtsc_lookahead_distance  # Only curves within 500m
            ]
        }
    
    def update_position(self, lat: float, lon: float, heading: float = 0.0) -> None:
        """Update current position and refresh curve data if needed."""
        current_time = time.time()
        
        # Rate limit MAPD queries
        if current_time - self.last_mapd_query < self.query_interval:
            return
        
        # Check if position has changed significantly
        if self.last_position:
            distance = self._haversine_distance(lat, lon, *self.last_position)
            if distance < 100:  # Less than 100m movement
                return
        
        self.last_position = (lat, lon)
        self.last_mapd_query = current_time
        
        # Query MAPD for local road data
        osm_data = self._query_mapd_binary(lat, lon, self.cache_radius)
        
        if osm_data:
            # Parse and cache road segments
            segments = self._parse_osm_ways(osm_data)
            for segment in segments:
                self.cached_segments[segment.segment_id] = segment
            
            # Clean up distant cached segments
            self._cleanup_distant_cache(lat, lon)
        
        # Find relevant curves and publish to M-TSC
        curves = self._find_relevant_curves(lat, lon, heading)
        mtsc_data = self._format_mtsc_curve_data(curves)
        
        # Publish to memory params for M-TSC controller
        self.mp.put("MTSCCurveData", json.dumps(mtsc_data))
    
    def _cleanup_distant_cache(self, lat: float, lon: float) -> None:
        """Remove cached segments that are far from current position with improved offline logic."""
        max_cache_distance = self.cache_radius * 2  # Keep larger cache for efficiency
        
        # If we have too many segments, be more aggressive about cleanup
        if len(self.cached_segments) > self.max_cached_segments:
            # Create list of (distance, segment_id) pairs for sorting
            segment_distances = []
            for segment_id, segment in self.cached_segments.items():
                distance = self._haversine_distance(lat, lon, segment.start_lat, segment.start_lon)
                segment_distances.append((distance, segment_id))
            
            # Sort by distance and keep only the closest segments
            segment_distances.sort()
            segments_to_keep = self.max_cached_segments // 2  # Keep closest 50%
            
            segments_to_remove = [
                segment_id for _, segment_id in segment_distances[segments_to_keep:]
            ]
        else:
            # Normal cleanup - just remove distant segments
            segments_to_remove = []
            for segment_id, segment in self.cached_segments.items():
                distance = self._haversine_distance(lat, lon, segment.start_lat, segment.start_lon)
                if distance > max_cache_distance:
                    segments_to_remove.append(segment_id)
        
        # Remove segments
        for segment_id in segments_to_remove:
            if segment_id in self.cached_segments:
                del self.cached_segments[segment_id]
    
    def tick(self) -> None:
        """Main update loop - called regularly by M-TSC system."""
        self.sm.update(1000)
        
        if self.sm.updated["gpsLocation"]:
            gps = self.sm["gpsLocation"].gpsLocation
            if gps.latitude != 0 and gps.longitude != 0:
                # Get heading from GPS if available (or could come from other sources)
                heading = 0.0  # TODO: Get actual vehicle heading
                
                self.update_position(gps.latitude, gps.longitude, heading)
    
    def get_status(self) -> Dict[str, Any]:
        """Get current extractor status for debugging."""
        return {
            "cached_segments": len(self.cached_segments),
            "last_position": self.last_position,
            "last_query": self.last_mapd_query,
            "has_recent_data": time.time() - self.last_mapd_query < 60.0
        }


def main():
    """Main function for standalone testing."""
    params = Params()
    extractor = NPMapdExtractor(params)
    
    print("NPMapdExtractor - Testing curve extraction")
    print(f"Status: {extractor.get_status()}")
    
    # Run for a few iterations
    for i in range(5):
        extractor.tick()
        time.sleep(1)
    
    print(f"Final status: {extractor.get_status()}")


if __name__ == "__main__":
    main()