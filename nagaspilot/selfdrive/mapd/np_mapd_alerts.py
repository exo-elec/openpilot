#!/usr/bin/env python3
"""
NPMapAlertManager - Home screen alert system for MAPD status and user actions
Based on proven patterns from existing NagasPilot alert system
"""
import json
import time
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass

from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from nagaspilot.selfdrive.mapd.np_mapd_updater import NPMapdUpdater, DownloadTrigger


class AlertType(Enum):
    """Types of MAPD-related alerts."""
    MAP_MISSING = "map_missing"
    MAP_OUTDATED = "map_outdated" 
    DOWNLOAD_AVAILABLE = "download_available"
    DOWNLOAD_PROGRESS = "download_progress"
    DOWNLOAD_SUCCESS = "download_success"
    DOWNLOAD_FAILED = "download_failed"
    BINARY_UPDATE = "binary_update"
    WIFI_RECOMMENDED = "wifi_recommended"


@dataclass
class AlertConfig:
    """Configuration for a specific alert type."""
    title: str
    priority: int  # 1=highest, 5=lowest
    timeout: Optional[float] = None  # Auto-dismiss after seconds
    actions: List[str] = None  # Available user actions
    
    def __post_init__(self):
        if self.actions is None:
            self.actions = []


class NPMapdAlerts:
    """
    Home screen alert management for MAPD system.
    Provides contextual alerts with action buttons for user interaction.
    """
    
    # Alert configurations
    ALERT_CONFIGS = {
        AlertType.MAP_MISSING: AlertConfig(
            "Maps Missing for M-TSC",
            priority=1,
            actions=["download_now", "schedule_wifi", "dismiss"]
        ),
        AlertType.MAP_OUTDATED: AlertConfig(
            "Map Update Available", 
            priority=2,
            actions=["update_now", "schedule_wifi", "dismiss"]
        ),
        AlertType.DOWNLOAD_AVAILABLE: AlertConfig(
            "New Region Detected",
            priority=2,
            actions=["download_now", "schedule_wifi", "dismiss"]
        ),
        AlertType.DOWNLOAD_PROGRESS: AlertConfig(
            "Downloading Maps",
            priority=3,
            actions=["cancel_download"]
        ),
        AlertType.DOWNLOAD_SUCCESS: AlertConfig(
            "Maps Updated Successfully",
            priority=4,
            timeout=10.0,
            actions=["dismiss"]
        ),
        AlertType.DOWNLOAD_FAILED: AlertConfig(
            "Map Download Failed", 
            priority=1,
            actions=["retry_download", "schedule_wifi", "dismiss"]
        ),
        AlertType.BINARY_UPDATE: AlertConfig(
            "MAPD Binary Update Available",
            priority=3,
            actions=["update_binary", "schedule_wifi", "dismiss"]
        ),
        AlertType.WIFI_RECOMMENDED: AlertConfig(
            "Large Download - WiFi Recommended",
            priority=2,
            actions=["download_anyway", "wait_for_wifi", "dismiss"]
        )
    }
    
    def __init__(self, params: Params, auto_updater: NPMapdUpdater):
        self.params = params
        self.mp = self._get_mem_params()
        self.auto_updater = auto_updater
        
        # Alert state tracking
        self.active_alerts: Dict[AlertType, Dict[str, Any]] = {}
        self.last_user_interaction = 0.0
        self.dismissed_alerts: Dict[str, float] = {}  # alert_id -> dismiss_time
        
        # Configuration
        self.dismiss_duration = 3600.0  # 1 hour before showing dismissed alert again
        self.progress_update_interval = 1.0  # seconds
    
    def _get_mem_params(self) -> Params:
        """Get memory params for real-time communication."""
        import platform
        return Params("/dev/shm/params") if platform.system() != "Darwin" else Params()
    
    
    def _generate_alert_id(self, alert_type: AlertType, context: str = "") -> str:
        """Generate unique alert ID for tracking dismissals."""
        return f"mapd_{alert_type.value}_{context}".lower()
    
    def _is_alert_dismissed(self, alert_id: str) -> bool:
        """Check if alert was recently dismissed by user."""
        if alert_id in self.dismissed_alerts:
            dismissed_time = self.dismissed_alerts[alert_id]
            return time.time() - dismissed_time < self.dismiss_duration
        return False
    
    def _set_alert_active(self, alert_type: AlertType, message: str, 
                         context: Dict[str, Any] = None) -> None:
        """Set an alert as active with context."""
        config = self.ALERT_CONFIGS[alert_type]
        alert_id = self._generate_alert_id(alert_type, context.get("region", "") if context else "")
        
        # Don't show recently dismissed alerts
        if self._is_alert_dismissed(alert_id):
            return
        
        # Store alert state
        alert_data = {
            "alert_id": alert_id,
            "title": config.title,
            "message": message,
            "priority": config.priority,
            "actions": config.actions,
            "context": context or {},
            "created_at": time.time(),
            "timeout": config.timeout
        }
        
        self.active_alerts[alert_type] = alert_data
        
        # Set system alert
        set_offroad_alert(f"Offroad_MAPD_{alert_type.value.title()}", True, message)
        
        # Store alert data for UI access
        self.mp.put(f"MapdAlert_{alert_type.value}", json.dumps(alert_data))
    
    def _clear_alert(self, alert_type: AlertType) -> None:
        """Clear an active alert."""
        if alert_type in self.active_alerts:
            del self.active_alerts[alert_type]
        
        # Clear system alert
        set_offroad_alert(f"Offroad_MAPD_{alert_type.value.title()}", False)
        
        # Clear alert data
        self.mp.remove(f"MapdAlert_{alert_type.value}")
    
    def _get_download_progress(self) -> Optional[Dict[str, Any]]:
        """Get current download progress information."""
        try:
            # Check binary download progress
            binary_progress = self.mp.get("MapdBinaryDownloadProgress", encoding='utf-8')
            if binary_progress:
                return json.loads(binary_progress)
            
            # Check OSM download progress  
            osm_progress = self.mp.get("OSMDownloadProgress", encoding='utf-8')
            if osm_progress:
                return json.loads(osm_progress)
                
        except Exception:
            pass
        return None
    
    def _handle_user_action(self, action: str, alert_type: AlertType, context: Dict[str, Any]) -> bool:
        """Handle user action on an alert."""
        self.last_user_interaction = time.time()
        
        try:
            if action == "download_now":
                region = context.get("region")
                if region:
                    success = self.auto_updater.request_manual_download(region)
                    if success:
                        self._clear_alert(alert_type)
                        return True
            
            elif action == "schedule_wifi":
                region = context.get("region") 
                if region:
                    # Add to scheduled downloads
                    scheduled = json.loads(self.params.get("ScheduledMapDownloads", encoding='utf-8') or "[]")
                    if region not in scheduled:
                        scheduled.append(region)
                        self.params.put("ScheduledMapDownloads", json.dumps(scheduled))
                    
                    self._clear_alert(alert_type)
                    self._set_alert_active(AlertType.WIFI_RECOMMENDED, 
                                         f"Download scheduled for {region} when on WiFi",
                                         {"region": region})
                    return True
            
            elif action == "dismiss":
                alert_id = self._generate_alert_id(alert_type, context.get("region", ""))
                self.dismissed_alerts[alert_id] = time.time()
                self._clear_alert(alert_type)
                return True
            
            elif action == "retry_download":
                region = context.get("region")
                if region:
                    # Clear from failed regions and retry
                    self.auto_updater.failed_regions.discard(region)
                    success = self.auto_updater.request_manual_download(region)
                    if success:
                        self._clear_alert(alert_type)
                        return True
            
            elif action == "cancel_download":
                # Set cancellation flag
                self.params.put_bool("OsmDbUpdatesCheck", False)
                self._clear_alert(AlertType.DOWNLOAD_PROGRESS)
                return True
            
            elif action == "update_binary":
                # Trigger binary update
                self.mp.put_bool("MapdBinaryUpdateRequested", True)
                self._clear_alert(AlertType.BINARY_UPDATE)
                return True
            
            elif action == "download_anyway":
                # Proceed with download despite metered connection
                region = context.get("region")
                if region:
                    # Temporarily disable WiFi-only setting
                    wifi_only = self.params.get_bool("OsmWifiOnly")
                    self.params.put_bool("OsmWifiOnly", False)
                    
                    success = self.auto_updater.request_manual_download(region)
                    
                    # Restore WiFi-only setting
                    self.params.put_bool("OsmWifiOnly", wifi_only)
                    
                    if success:
                        self._clear_alert(alert_type)
                        return True
            
            elif action == "wait_for_wifi":
                # Same as schedule_wifi
                return self._handle_user_action("schedule_wifi", alert_type, context)
            
                
        except Exception:
            pass
            
        return False
    
    def _check_pending_user_actions(self) -> None:
        """Check for and process pending user actions."""
        try:
            # Check for user actions in params
            action_data = self.mp.get("MapdUserAction", encoding='utf-8')
            if action_data:
                self.mp.remove("MapdUserAction")
                
                data = json.loads(action_data)
                action = data.get("action")
                alert_type_str = data.get("alert_type")
                context = data.get("context", {})
                
                if action and alert_type_str:
                    alert_type = AlertType(alert_type_str)
                    self._handle_user_action(action, alert_type, context)
                    
        except Exception:
            pass
    
    def _check_timeout_alerts(self) -> None:
        """Check and clear alerts that have timed out."""
        current_time = time.time()
        
        for alert_type, alert_data in list(self.active_alerts.items()):
            timeout = alert_data.get("timeout")
            if timeout:
                created_at = alert_data.get("created_at", 0)
                if current_time - created_at >= timeout:
                    self._clear_alert(alert_type)
    
    def _check_map_status(self) -> None:
        """Check current map status and generate appropriate alerts."""
        try:
            # Get current status
            status = self.auto_updater.get_status()
            current_region = status.get("current_region")
            download_in_progress = status.get("download_in_progress", False)
            
            # Check for missing maps
            if current_region and not download_in_progress:
                osm_local = self.params.get_bool("OsmLocal")
                if osm_local:
                    # Check if we have maps for current region
                    from nagaspilot.selfdrive.mapd.np_mapd_manager import NP_OSM_OFFLINE
                    has_maps = any(NP_OSM_OFFLINE.rglob('*.osm.pbf'))
                    
                    if not has_maps:
                        self._set_alert_active(
                            AlertType.MAP_MISSING,
                            f"M-TSC accuracy reduced without {current_region} maps",
                            {"region": current_region}
                        )
                    else:
                        # Clear missing map alert if resolved
                        self._clear_alert(AlertType.MAP_MISSING)
            
            # Check for download progress
            if download_in_progress:
                progress = self._get_download_progress()
                if progress:
                    status_msg = progress.get("status", "downloading")
                    if status_msg == "downloading":
                        downloaded = progress.get("downloaded_files", 0)
                        total = progress.get("total_files", 100)
                        
                        self._set_alert_active(
                            AlertType.DOWNLOAD_PROGRESS,
                            f"Downloading maps: {downloaded}/{total} ({downloaded}%)",
                            {"progress": downloaded, "total": total}
                        )
                    elif "error" in status_msg:
                        self._clear_alert(AlertType.DOWNLOAD_PROGRESS)
                        self._set_alert_active(
                            AlertType.DOWNLOAD_FAILED,
                            f"Download failed: {status_msg}",
                            {"region": current_region}
                        )
            else:
                # Clear progress alert if no longer downloading
                self._clear_alert(AlertType.DOWNLOAD_PROGRESS)
            
            # Check for binary updates
            if self.params.get_bool("MapdBinaryUpdateAvailable"):
                if AlertType.BINARY_UPDATE not in self.active_alerts:
                    self._set_alert_active(
                        AlertType.BINARY_UPDATE,
                        "New MAPD binary available for improved M-TSC performance",
                        {}
                    )
            else:
                self._clear_alert(AlertType.BINARY_UPDATE)
            
                
        except Exception:
            pass
    
    def tick(self) -> None:
        """Main update loop for alert management."""
        # Handle pending user actions
        self._check_pending_user_actions()
        
        # Check for timed out alerts
        self._check_timeout_alerts()
        
        # Check map status and generate alerts
        self._check_map_status()
        
        # Clean up old dismissed alerts (older than 24 hours)
        current_time = time.time()
        expired_dismissals = [
            alert_id for alert_id, dismiss_time in self.dismissed_alerts.items()
            if current_time - dismiss_time > 24 * 3600
        ]
        for alert_id in expired_dismissals:
            del self.dismissed_alerts[alert_id]
    
    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """Get list of currently active alerts sorted by priority."""
        alerts = list(self.active_alerts.values())
        return sorted(alerts, key=lambda x: x.get("priority", 5))
    
    def request_user_action(self, action: str, alert_type_str: str, context: Dict[str, Any] = None) -> bool:
        """Request user action to be processed (for external callers)."""
        try:
            action_data = {
                "action": action,
                "alert_type": alert_type_str,
                "context": context or {},
                "timestamp": time.time()
            }
            self.mp.put("MapdUserAction", json.dumps(action_data))
            return True
        except Exception:
            return False
    
    def get_alert_actions(self, alert_type: AlertType) -> List[str]:
        """Get available actions for a specific alert type."""
        config = self.ALERT_CONFIGS.get(alert_type)
        return config.actions if config else []
    
    def get_status(self) -> Dict[str, Any]:
        """Get current alert manager status."""
        return {
            "active_alerts": len(self.active_alerts),
            "alert_types": [at.value for at in self.active_alerts.keys()],
            "dismissed_count": len(self.dismissed_alerts),
            "last_user_interaction": self.last_user_interaction
        }


def main():
    """Main function for standalone testing."""
    from nagaspilot.selfdrive.mapd.np_mapd_updater import NPMapdUpdater
    
    params = Params()
    auto_updater = NPMapdUpdater(params)
    alert_manager = NPMapdAlerts(params, auto_updater)
    
    print("NPMapdAlerts - Testing alert system")
    print(f"Status: {alert_manager.get_status()}")
    
    # Simulate some alerts for testing
    alert_manager._set_alert_active(
        AlertType.MAP_MISSING,
        "Test missing map alert",
        {"region": "US"}
    )
    
    print(f"Active alerts: {alert_manager.get_active_alerts()}")
    
    # Run for a few iterations
    for i in range(3):
        alert_manager.tick()
        time.sleep(1)
    
    print(f"Final status: {alert_manager.get_status()}")


if __name__ == "__main__":
    main()