#!/usr/bin/env python3
"""
NPMapAutoUpdater - GPS-based automatic map download system for M-TSC
Based on proven patterns from existing NagasPilot OSM download system
"""
import json
import time
import math
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass
from enum import Enum

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from nagaspilot.selfdrive.mapd.np_mapd_manager import stream_download, mem_params, NP_OSM_OFFLINE


class DownloadTrigger(Enum):
    GPS_REGION_CHANGE = "gps_region_change"
    USER_REQUEST = "user_request" 
    SCHEDULED_UPDATE = "scheduled_update"
    MISSING_DATA = "missing_data"


@dataclass
class RegionInfo:
    """Information about a geographical region for map downloads."""
    country_code: str
    country_name: str
    geofabrik_path: str
    bbox: Tuple[float, float, float, float]  # min_lat, min_lon, max_lat, max_lon
    priority: int = 1  # Higher = more important


class NPMapdUpdater:
    """
    GPS-based automatic map download system with intelligent scheduling.
    Extends proven OSM download patterns with smart region detection.
    """
    
    # Region definitions based on existing country mapping
    REGIONS = {
        # North America
        "US": RegionInfo("US", "United States", "north-america/us", 
                        (24.396308, -125.0, 49.384358, -66.93457), 1),
        "CA": RegionInfo("CA", "Canada", "north-america/canada",
                        (41.676555, -141.002701, 83.110619, -52.636291), 1),
        "MX": RegionInfo("MX", "Mexico", "north-america/mexico",
                        (14.532, -118.453, 32.718, -86.710), 1),
        
        # Europe
        "GB": RegionInfo("GB", "Great Britain", "europe/great-britain",
                        (49.959999, -7.572168, 58.6350001, 1.681531), 1),
        "DE": RegionInfo("DE", "Germany", "europe/germany", 
                        (47.270111, 5.866944, 55.0581, 15.04193), 1),
        "FR": RegionInfo("FR", "France", "europe/france",
                        (41.333, -5.140, 51.089, 9.560), 1),
        "ES": RegionInfo("ES", "Spain", "europe/spain", 
                        (35.173, -9.301, 43.792, 3.317), 1),
        "IT": RegionInfo("IT", "Italy", "europe/italy",
                        (36.619, 6.627, 47.095, 18.521), 1),
        "NL": RegionInfo("NL", "Netherlands", "europe/netherlands",
                        (50.750, 3.362, 53.555, 7.227), 1),
        "PL": RegionInfo("PL", "Poland", "europe/poland",
                        (49.006, 14.123, 54.836, 24.146), 1),
        
        # Asia
        "JP": RegionInfo("JP", "Japan", "asia/japan",
                        (24.045, 122.934, 45.557, 148.0), 1),
        "KR": RegionInfo("KR", "South Korea", "asia/korea-south", 
                        (33.113, 125.887, 38.612, 129.585), 1),
        "TH": RegionInfo("TH", "Thailand", "asia/thailand",
                        (5.613, 97.345, 20.464, 105.639), 1),
        "SG": RegionInfo("SG", "Singapore", "asia/singapore",
                        (1.158, 103.536, 1.471, 104.088), 2),  # Higher priority - small area
        "MY": RegionInfo("MY", "Malaysia", "asia/malaysia",
                        (0.855, 99.644, 7.364, 119.267), 1),
        "ID": RegionInfo("ID", "Indonesia", "asia/indonesia",
                        (-10.929, 95.009, 6.077, 141.021), 1),
        "PH": RegionInfo("PH", "Philippines", "asia/philippines",
                        (4.587, 116.931, 21.121, 126.537), 1),
        "IN": RegionInfo("IN", "India", "asia/india",
                        (6.747, 68.033, 35.674, 97.395), 1),
        "AE": RegionInfo("AE", "United Arab Emirates", "asia/united-arab-emirates",
                        (22.633, 51.498, 26.084, 56.381), 2),
        "SA": RegionInfo("SA", "Saudi Arabia", "asia/saudi-arabia",
                        (15.618, 34.495, 32.158, 55.667), 1),
        
        # Oceania
        "AU": RegionInfo("AU", "Australia", "australia-oceania/australia",
                        (-43.634, 113.338, -10.668, 153.569), 1),
        "NZ": RegionInfo("NZ", "New Zealand", "australia-oceania/new-zealand",
                        (-46.641, 166.509, -34.45, 178.517), 1),
        
        # South America
        "BR": RegionInfo("BR", "Brazil", "south-america/brazil",
                        (-33.751, -73.986, 5.272, -28.847), 1),
        "AR": RegionInfo("AR", "Argentina", "south-america/argentina",
                        (-55.061, -73.560, -21.781, -53.591), 1),
        "CL": RegionInfo("CL", "Chile", "south-america/chile",
                        (-55.926, -75.644, -17.507, -66.417), 1),
    }
    
    def __init__(self, params: Params):
        self.params = params
        self.mp = mem_params()
        self.sm = messaging.SubMaster(["gpsLocation", "deviceState"])
        
        # State tracking
        self.last_region_check = 0.0
        self.current_region: Optional[str] = None
        self.download_in_progress = False
        self.last_download_attempt = 0.0
        self.failed_regions: Set[str] = set()
        
        # Configuration
        self.region_check_interval = 30.0  # seconds
        self.download_retry_delay = 300.0  # 5 minutes
        self.region_change_threshold = 0.05  # degrees (~5km)
        
    def _point_in_bbox(self, lat: float, lon: float, bbox: Tuple[float, float, float, float]) -> bool:
        """Check if GPS coordinates fall within a bounding box."""
        min_lat, min_lon, max_lat, max_lon = bbox
        return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
    
    def _detect_region_from_gps(self, lat: float, lon: float) -> Optional[str]:
        """Detect region based on GPS coordinates using bounding boxes."""
        # Sort regions by priority (higher first) for overlapping areas
        sorted_regions = sorted(
            self.REGIONS.items(), 
            key=lambda x: x[1].priority, 
            reverse=True
        )
        
        for region_code, region_info in sorted_regions:
            if self._point_in_bbox(lat, lon, region_info.bbox):
                return region_code
        
        return None
    
    def _calculate_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two GPS points in degrees (rough)."""
        return math.sqrt((lat2 - lat1)**2 + (lon2 - lon1)**2)
    
    def _get_last_known_position(self) -> Optional[Tuple[float, float]]:
        """Get last known GPS position from params."""
        try:
            pos_data = self.params.get("LastGPSPosition", encoding='utf-8')
            if pos_data:
                data = json.loads(pos_data)
                return data.get("latitude"), data.get("longitude")
        except Exception:
            pass
        return None
    
    def _should_download_for_region(self, region_code: str) -> bool:
        """Check if we should download maps for a given region."""
        # Don't retry failed regions too soon
        if region_code in self.failed_regions:
            return False
        
        # Check if already downloading
        if self.download_in_progress:
            return False
        
        # Rate limit download attempts
        if time.time() - self.last_download_attempt < self.download_retry_delay:
            return False
        
        # Check if we already have recent maps for this region
        current_region = self.params.get("OsmLocationName", encoding='utf-8') or ""
        if current_region == region_code:
            # Check last download date
            try:
                last_download = float(self.params.get("OsmDownloadedDate", encoding='utf-8') or "0")
                # Don't re-download if less than 7 days old
                if time.time() - last_download < 7 * 24 * 3600:
                    return False
            except Exception:
                pass
        
        # Check device state for download eligibility
        device_state = self.sm.get("deviceState")
        if device_state and device_state.deviceState:
            # Prefer WiFi for large downloads
            is_metered = device_state.deviceState.networkMetered
            wifi_only = self.params.get_bool("OsmWifiOnly")
            
            if wifi_only and is_metered:
                # Schedule for later when on WiFi
                self._schedule_wifi_download(region_code)
                return False
        
        return True
    
    def _schedule_wifi_download(self, region_code: str) -> None:
        """Schedule download when WiFi becomes available."""
        try:
            scheduled = json.loads(self.params.get("ScheduledMapDownloads", encoding='utf-8') or "[]")
            if region_code not in scheduled:
                scheduled.append(region_code)
                self.params.put("ScheduledMapDownloads", json.dumps(scheduled))
        except Exception:
            pass
    
    def _trigger_download_for_region(self, region_code: str, trigger: DownloadTrigger) -> bool:
        """Trigger map download for specified region."""
        if region_code not in self.REGIONS:
            return False
        
        region_info = self.REGIONS[region_code]
        
        try:
            # Set download parameters using existing system
            self.params.put("OsmLocationName", region_code)
            self.params.put_bool("OsmDbUpdatesCheck", True)
            
            # Update state
            self.download_in_progress = True
            self.last_download_attempt = time.time()
            
            # Set user-friendly alert
            set_offroad_alert("Offroad_MapAutoDownload", True,
                             f"Downloading {region_info.country_name} maps for better M-TSC accuracy")
            
            # Track trigger reason
            self.mp.put("MapDownloadTrigger", trigger.value)
            
            return True
            
        except Exception as e:
            set_offroad_alert("Offroad_MapAutoDownloadFailed", True,
                             f"Failed to start map download: {str(e)}")
            return False
    
    def _check_download_completion(self) -> None:
        """Check if ongoing download has completed."""
        if not self.download_in_progress:
            return
        
        # Check if download flag has been cleared (indicates completion)
        if not self.params.get_bool("OsmDbUpdatesCheck"):
            self.download_in_progress = False
            
            # Clear download alert
            set_offroad_alert("Offroad_MapAutoDownload", False)
            
            # Check if download was successful
            current_region = self.params.get("OsmLocationName", encoding='utf-8') or ""
            if current_region:
                # Check for downloaded files
                has_maps = any(NP_OSM_OFFLINE.rglob('*.osm.pbf'))
                if has_maps:
                    # Success
                    self.failed_regions.discard(current_region)
                    set_offroad_alert("Offroad_MapAutoDownloadSuccess", True,
                                     f"Maps downloaded successfully for {current_region}")
                    # Clear success alert after delay
                    self.mp.put("ClearMapDownloadSuccess", str(time.time() + 10))
                else:
                    # Failed - add to failed regions
                    self.failed_regions.add(current_region)
                    set_offroad_alert("Offroad_MapAutoDownloadFailed", True,
                                     f"Map download failed for {current_region}")
    
    def _process_scheduled_downloads(self) -> None:
        """Process any scheduled downloads when conditions are met."""
        try:
            scheduled = json.loads(self.params.get("ScheduledMapDownloads", encoding='utf-8') or "[]")
            if not scheduled:
                return
            
            # Check if we're on WiFi now
            device_state = self.sm.get("deviceState")
            if device_state and device_state.deviceState:
                is_metered = device_state.deviceState.networkMetered
                
                if not is_metered:  # On WiFi
                    # Try to download the first scheduled region
                    region_code = scheduled[0]
                    if self._should_download_for_region(region_code):
                        if self._trigger_download_for_region(region_code, DownloadTrigger.SCHEDULED_UPDATE):
                            # Remove from scheduled list
                            scheduled.remove(region_code)
                            self.params.put("ScheduledMapDownloads", json.dumps(scheduled))
                            
        except Exception:
            pass
    
    def _clear_expired_alerts(self) -> None:
        """Clear time-based alerts that have expired."""
        try:
            clear_time_str = self.mp.get("ClearMapDownloadSuccess", encoding='utf-8')
            if clear_time_str:
                clear_time = float(clear_time_str)
                if time.time() >= clear_time:
                    set_offroad_alert("Offroad_MapAutoDownloadSuccess", False)
                    self.mp.remove("ClearMapDownloadSuccess")
        except Exception:
            pass
    
    def tick(self) -> None:
        """Main update loop for auto map download system."""
        current_time = time.time()
        
        # Update sensor data
        self.sm.update(1000)
        
        # Check download completion
        self._check_download_completion()
        
        # Process scheduled downloads
        self._process_scheduled_downloads()
        
        # Clear expired alerts
        self._clear_expired_alerts()
        
        # GPS-based region detection (rate limited)
        if current_time - self.last_region_check >= self.region_check_interval:
            self.last_region_check = current_time
            
            # Get current GPS position
            if self.sm.updated["gpsLocation"]:
                gps = self.sm["gpsLocation"].gpsLocation
                if gps.latitude != 0 and gps.longitude != 0:
                    detected_region = self._detect_region_from_gps(gps.latitude, gps.longitude)
                    
                    # Check if region has changed significantly
                    if detected_region and detected_region != self.current_region:
                        # Verify change with distance threshold to avoid edge cases
                        last_pos = self._get_last_known_position()
                        if last_pos:
                            lat_diff = abs(gps.latitude - last_pos[0])
                            lon_diff = abs(gps.longitude - last_pos[1])
                            
                            if (lat_diff > self.region_change_threshold or 
                                lon_diff > self.region_change_threshold):
                                
                                # Significant region change detected
                                self.current_region = detected_region
                                
                                # Check if we should download maps for new region
                                if self._should_download_for_region(detected_region):
                                    self._trigger_download_for_region(
                                        detected_region, 
                                        DownloadTrigger.GPS_REGION_CHANGE
                                    )
                        else:
                            # No previous position - first detection
                            self.current_region = detected_region
                            if self._should_download_for_region(detected_region):
                                self._trigger_download_for_region(
                                    detected_region,
                                    DownloadTrigger.GPS_REGION_CHANGE
                                )
    
    def request_manual_download(self, region_code: str) -> bool:
        """Manually request download for specific region."""
        if region_code not in self.REGIONS:
            return False
        
        # Clear from failed regions to allow retry
        self.failed_regions.discard(region_code)
        
        return self._trigger_download_for_region(region_code, DownloadTrigger.USER_REQUEST)
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status of auto updater."""
        return {
            "current_region": self.current_region,
            "download_in_progress": self.download_in_progress,
            "failed_regions": list(self.failed_regions),
            "scheduled_downloads": json.loads(self.params.get("ScheduledMapDownloads", encoding='utf-8') or "[]"),
            "last_download_attempt": self.last_download_attempt,
            "available_regions": list(self.REGIONS.keys())
        }


def main():
    """Main function for standalone testing."""
    params = Params()
    updater = NPMapdUpdater(params)
    
    print("NPMapdUpdater - Testing auto download system")
    print(f"Status: {updater.get_status()}")
    
    # Run for a few iterations
    for i in range(5):
        updater.tick()
        time.sleep(1)
        
    print(f"Final status: {updater.get_status()}")


if __name__ == "__main__":
    main()