#!/usr/bin/env python3
"""
NPMapdBinaryManager - Enhanced binary management for pfeiferj/mapd integration
Based on proven patterns from existing NagasPilot MapdRunner and download systems
"""
import json
import requests
import os
import stat
import hashlib
import platform
import time
from pathlib import Path
from typing import Optional, Dict, Any
import subprocess
import signal

from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert

# Configuration
MAPD_BINARY_DIR = Path.home() / "nagaspilot" / "mapd" / "bin"
MAPD_VERSION_FILE = MAPD_BINARY_DIR / "version.json"
MAPD_BINARY_NAME = "mapd"

# GitHub release configuration for pfeiferj/mapd
GITHUB_API_BASE = "https://api.github.com/repos/pfeiferj/mapd"
GITHUB_RELEASES_URL = f"{GITHUB_API_BASE}/releases/latest"

# Platform mapping for binary downloads
PLATFORM_MAP = {
    "Linux": {
        "x86_64": "linux-amd64",
        "aarch64": "linux-arm64",
        "armv7l": "linux-armv7"
    },
    "Darwin": {
        "x86_64": "darwin-amd64",
        "arm64": "darwin-arm64"
    }
}


class NPMapdManager:
    """
    Enhanced binary manager for pfeiferj/mapd with auto-download and update capabilities.
    Extends proven MapdRunner patterns with GitHub integration and validation.
    """
    
    def __init__(self, params: Params):
        self.params = params
        self.proc: Optional[subprocess.Popen] = None
        self.last_path: Optional[str] = None
        self.restart_attempts = 0  # Track restart attempts for backoff strategy
        self.last_mapd_query = 0.0  # Track last successful query
        self._ensure_directories()
    
    def _ensure_directories(self) -> None:
        """Create necessary directories for binary storage."""
        try:
            MAPD_BINARY_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    
    def _get_platform_info(self) -> tuple[str, str]:
        """Get current platform and architecture for binary selection."""
        system = platform.system()
        machine = platform.machine()
        return system, machine
    
    def _get_binary_path(self) -> Path:
        """Get expected path for the mapd binary."""
        return MAPD_BINARY_DIR / MAPD_BINARY_NAME
    
    def _is_executable(self, path: Path) -> bool:
        """Check if file is executable using proven MapdRunner pattern."""
        try:
            st = path.stat()
            return stat.S_ISREG(st.st_mode) and os.access(path, os.X_OK)
        except Exception:
            return False
    
    def _calculate_file_hash(self, filepath: Path) -> str:
        """Calculate SHA256 hash of file for validation."""
        try:
            with open(filepath, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""
    
    def _get_current_version_info(self) -> Dict[str, Any]:
        """Load current version info from local storage."""
        try:
            if MAPD_VERSION_FILE.exists():
                return json.loads(MAPD_VERSION_FILE.read_text())
        except Exception:
            pass
        return {}
    
    def _save_version_info(self, version_info: Dict[str, Any]) -> None:
        """Save version info to local storage."""
        try:
            MAPD_VERSION_FILE.write_text(json.dumps(version_info, indent=2))
        except Exception:
            pass
    
    def _handle_mapd_failure(self, error_type: str, error_details: str = "") -> None:
        """Handle MAPD system failures with specific recovery strategies"""
        cloudlog.error(f"MAPD failure: {error_type} - {error_details}")
        
        if error_type == "binary_missing":
            # Auto-download binary
            cloudlog.info("MAPD: Attempting to auto-download missing binary")
            if self.download_binary():
                cloudlog.info("MAPD: Successfully downloaded binary")
            else:
                set_offroad_alert("Offroad_MapdBinaryMissing", True, 
                                 "MAPD binary is missing. Auto-download failed. Please check internet connection.")
        
        elif error_type == "binary_corrupt":
            # Re-download binary
            cloudlog.info("MAPD: Binary appears corrupt, re-downloading")
            binary_path = self._get_binary_path()
            if binary_path.exists():
                binary_path.unlink()  # Remove corrupt binary
            if self.download_binary():
                cloudlog.info("MAPD: Successfully re-downloaded binary")
            else:
                set_offroad_alert("Offroad_MapdBinaryCorrupt", True,
                                 "MAPD binary is corrupted. Re-download failed.")
        
        elif error_type == "data_corrupt":
            # Clear cache and retry
            cloudlog.info("MAPD: Clearing cache due to corrupt data")
            self._clear_cache()
            set_offroad_alert("Offroad_MapdDataCorrupt", True,
                             "MAPD data appears corrupted. Cache cleared. System will rebuild data.")
        
        elif error_type == "network_error":
            # Use cached data with reduced confidence
            cloudlog.info("MAPD: Network error, using cached data with reduced confidence")
            set_offroad_alert("Offroad_MapdNetworkError", True,
                             "MAPD network error. Using cached data with reduced accuracy.")
        
        elif error_type == "process_crashed":
            # Restart process with backoff
            cloudlog.info("MAPD: Process crashed, attempting restart")
            self._restart_with_backoff()
        
        else:
            # Generic error handling
            cloudlog.error(f"MAPD: Unknown error type: {error_type}")
            set_offroad_alert("Offroad_MapdUnknownError", True,
                             f"MAPD encountered an error: {error_details}")
    
    def _clear_cache(self) -> None:
        """Clear MAPD cache and reset state"""
        try:
            self.cached_segments.clear()
            self.last_position = None
            cloudlog.info("MAPD: Cache cleared successfully")
        except Exception as e:
            cloudlog.error(f"MAPD: Failed to clear cache: {e}")
    
    def _restart_with_backoff(self) -> None:
        """Restart MAPD process with exponential backoff"""
        import random
        
        # Simple backoff strategy (could be enhanced with more sophisticated retry logic)
        backoff_time = min(60, 2 ** self.restart_attempts) + random.uniform(0, 5)
        cloudlog.info(f"MAPD: Restarting with backoff: {backoff_time:.1f} seconds")
        
        time.sleep(backoff_time)
        self.restart_attempts += 1
        
        # Attempt restart
        try:
            self.stop()
            time.sleep(2)  # Brief pause before restart
            self.start()
            self.restart_attempts = 0  # Reset on successful restart
            cloudlog.info("MAPD: Successfully restarted with backoff")
        except Exception as e:
            cloudlog.error(f"MAPD: Restart failed: {e}")
    
    def _check_mapd_health(self) -> Dict[str, Any]:
        """Check MAPD system health and return status"""
        health_status = {
            "binary_exists": False,
            "binary_executable": False,
            "version_current": False,
            "data_freshness": "unknown",
            "cache_size": 0,
            "last_update": 0,
            "overall_health": "unknown"
        }
        
        try:
            # Check binary existence and executability
            binary_path = self._get_binary_path()
            health_status["binary_exists"] = binary_path.exists()
            health_status["binary_executable"] = self._is_executable(binary_path)
            
            # Check version currency
            current_version = self._get_current_version_info().get("tag_name", "")
            latest_version = self._get_latest_release_info().get("tag_name", "")
            health_status["version_current"] = current_version == latest_version and current_version != ""
            
            # Check data freshness
            last_update = self.last_mapd_query if hasattr(self, 'last_mapd_query') else 0
            time_since_update = time.time() - last_update
            if time_since_update < 60:  # Less than 1 minute
                health_status["data_freshness"] = "fresh"
            elif time_since_update < 300:  # Less than 5 minutes
                health_status["data_freshness"] = "stale"
            else:
                health_status["data_freshness"] = "expired"
            
            # Check cache size
            health_status["cache_size"] = len(getattr(self, 'cached_segments', {}))
            health_status["last_update"] = last_update
            
            # Overall health assessment
            if (health_status["binary_exists"] and 
                health_status["binary_executable"] and 
                health_status["data_freshness"] == "fresh"):
                health_status["overall_health"] = "healthy"
            elif (health_status["binary_exists"] and 
                  health_status["binary_executable"]):
                health_status["overall_health"] = "degraded"
            else:
                health_status["overall_health"] = "unhealthy"
                
        except Exception as e:
            cloudlog.error(f"MAPD: Health check failed: {e}")
            health_status["overall_health"] = "error"
        
        return health_status
    
    def _get_latest_release_info(self) -> Optional[Dict[str, Any]]:
        """Fetch latest release info from GitHub API."""
        try:
            response = requests.get(GITHUB_RELEASES_URL, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None
    
    def _find_asset_for_platform(self, assets: list) -> Optional[Dict[str, Any]]:
        """Find appropriate binary asset for current platform."""
        system, machine = self._get_platform_info()
        
        if system not in PLATFORM_MAP or machine not in PLATFORM_MAP[system]:
            return None
        
        platform_tag = PLATFORM_MAP[system][machine]
        
        for asset in assets:
            if platform_tag in asset.get("name", "").lower():
                return asset
        
        return None
    
    def _stream_download_binary(self, url: str, target_path: Path, expected_size: int = 0) -> bool:
        """
        Download binary with progress tracking using proven stream_download pattern.
        Adapted from existing np_mapd_manager.py.
        """
        try:
            mp = self._get_mem_params()
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                total = int(r.headers.get('Content-Length', expected_size))
                downloaded = 0
                
                # Initialize progress tracking
                mp.put("MapdBinaryDownloadProgress", json.dumps({
                    "total_files": 100, 
                    "downloaded_files": 0,
                    "status": "downloading"
                }))
                
                with open(target_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=512 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if total > 0:
                            pct = max(0, min(100, int(downloaded * 100 / total)))
                            mp.put("MapdBinaryDownloadProgress", json.dumps({
                                "total_files": 100, 
                                "downloaded_files": pct,
                                "status": "downloading"
                            }))
                
                # Set executable permissions
                target_path.chmod(0o755)
                
                # Complete progress tracking
                mp.put("MapdBinaryDownloadProgress", json.dumps({
                    "total_files": 100, 
                    "downloaded_files": 100,
                    "status": "completed"
                }))
                
                return True
                
        except Exception as e:
            try:
                mp = self._get_mem_params()
                mp.put("MapdBinaryDownloadProgress", json.dumps({
                    "total_files": 100, 
                    "downloaded_files": 0,
                    "status": f"error: {str(e)}"
                }))
            except Exception:
                pass
            return False
    
    def _get_mem_params(self) -> Params:
        """Get memory params using proven pattern from np_mapd_manager.py."""
        return Params("/dev/shm/params") if platform.system() != "Darwin" else Params()
    
    def check_for_updates(self) -> bool:
        """Check if binary update is available."""
        current_version = self._get_current_version_info()
        latest_release = self._get_latest_release_info()
        
        if not latest_release:
            return False
        
        current_tag = current_version.get("tag_name", "")
        latest_tag = latest_release.get("tag_name", "")
        
        return latest_tag != current_tag and latest_tag != ""
    
    def download_binary(self) -> bool:
        """Download and install the latest mapd binary."""
        release_info = self._get_latest_release_info()
        if not release_info:
            set_offroad_alert("Offroad_MapdBinaryUpdateFailed", True, 
                             "Failed to fetch latest mapd release info")
            return False
        
        asset = self._find_asset_for_platform(release_info.get("assets", []))
        if not asset:
            set_offroad_alert("Offroad_MapdBinaryUnsupported", True,
                             "No mapd binary available for your platform")
            return False
        
        download_url = asset.get("browser_download_url")
        if not download_url:
            return False
        
        # Download to temporary file first
        binary_path = self._get_binary_path()
        temp_path = binary_path.with_suffix(".tmp")
        
        try:
            if not self._stream_download_binary(download_url, temp_path, asset.get("size", 0)):
                return False
            
            # Validate downloaded binary
            if not self._is_executable(temp_path):
                temp_path.unlink(missing_ok=True)
                return False
            
            # Move to final location
            if binary_path.exists():
                binary_path.unlink()
            temp_path.rename(binary_path)
            
            # Save version info
            version_info = {
                "tag_name": release_info.get("tag_name", ""),
                "published_at": release_info.get("published_at", ""),
                "download_url": download_url,
                "file_hash": self._calculate_file_hash(binary_path),
                "installed_at": time.time()
            }
            self._save_version_info(version_info)
            
            # Update params to point to new binary
            self.params.put("NpMapdBinary", str(binary_path))
            
            # Clear any error alerts
            set_offroad_alert("Offroad_MapdBinaryUpdateFailed", False)
            set_offroad_alert("Offroad_MapdBinaryUnsupported", False)
            
            return True
            
        except Exception as e:
            # Cleanup on failure
            temp_path.unlink(missing_ok=True)
            set_offroad_alert("Offroad_MapdBinaryUpdateFailed", True,
                             f"Binary download failed: {str(e)}")
            return False
    
    def ensure_binary_available(self) -> bool:
        """Ensure mapd binary is available and up-to-date."""
        binary_path = self._get_binary_path()
        
        # Check if binary exists and is executable
        if not binary_path.exists() or not self._is_executable(binary_path):
            return self.download_binary()
        
        # Check if update is available (but don't force update)
        if self.check_for_updates():
            # Set parameter to indicate update available
            self.params.put_bool("MapdBinaryUpdateAvailable", True)
        else:
            self.params.put_bool("MapdBinaryUpdateAvailable", False)
        
        # Ensure params points to current binary
        current_path = self.params.get("NpMapdBinary", encoding='utf-8')
        if current_path != str(binary_path):
            self.params.put("NpMapdBinary", str(binary_path))
        
        return True
    
    def get_binary_info(self) -> Dict[str, Any]:
        """Get information about currently installed binary."""
        binary_path = self._get_binary_path()
        version_info = self._get_current_version_info()
        
        return {
            "path": str(binary_path),
            "exists": binary_path.exists(),
            "executable": self._is_executable(binary_path),
            "version_info": version_info,
            "update_available": self.check_for_updates()
        }
    
    def tick(self):
        """
        Main tick function for binary management with enhanced error handling.
        Extends MapdRunner.tick() with auto-download capability and health monitoring.
        """
        try:
            # Check system health before proceeding
            health_status = self._check_mapd_health()
            if health_status["overall_health"] == "unhealthy":
                cloudlog.warning(f"MAPD: System unhealthy, attempting recovery: {health_status}")
                self._handle_mapd_failure("system_unhealthy", str(health_status))
                return
            
            # Ensure binary is available
            if not self.ensure_binary_available():
                self._handle_mapd_failure("binary_missing", "Binary not available")
                return
            
            # Use existing MapdRunner logic for process management with enhanced error handling
            want = self.params.get("NpMapdBinary", encoding='utf-8')
            
            # Restart if path changed or process crashed
            if want != self.last_path or (self.proc and self.proc.poll() is not None):
                if self.proc and self.proc.poll() is not None:
                    self._handle_mapd_failure("process_crashed", "Process terminated unexpectedly")
                self.stop()
                self.last_path = want
        
        if want and Path(want).exists() and self._is_executable(Path(want)):
            if not self.proc or self.proc.poll() is not None:
                try:
                    # Run mapd binary with basic configuration
                    self.proc = subprocess.Popen(
                        [want], 
                        stdout=subprocess.DEVNULL, 
                        stderr=subprocess.DEVNULL
                    )
                except Exception:
                    self.proc = None
        else:
            self.stop()
    
    def stop(self):
        """Stop running mapd process using proven pattern."""
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGTERM)
                # Give it a moment to terminate gracefully
                time.sleep(0.1)
                if self.proc.poll() is None:
                    self.proc.kill()
            except Exception:
                pass
        self.proc = None


def main():
    """Main function for standalone testing."""
    params = Params()
    manager = NPMapdManager(params)
    
    print("NPMapdManager - Testing binary management")
    print(f"Binary info: {manager.get_binary_info()}")
    
    if manager.ensure_binary_available():
        print("✅ Binary is available and ready")
        manager.tick()  # Start the process
        time.sleep(2)   # Let it run briefly
        manager.stop()  # Clean shutdown
    else:
        print("❌ Failed to ensure binary availability")


if __name__ == "__main__":
    main()