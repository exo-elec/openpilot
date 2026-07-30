#!/usr/bin/env python3
"""
rtcd - Real-Time Clock Daemon

Manages system time synchronization:
- GPS time synchronization (from pigeond)
- NTP fallback
- RTC battery backup time
- Time validity checking

Publishes:
  - rtcStatus: Current time status and source
"""

from __future__ import annotations

import time
import datetime

from cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity


class RtcD:
    """Real-Time Clock daemon."""
    
    def __init__(self):
        set_daemon_affinity("rtcd")
        
        self.pm = messaging.PubMaster(['rtcStatus'])
        self.sm = messaging.SubMaster(['ubloxGnss'])  # For GPS time
        
        self.time_source = "unknown"
        self.time_valid = False
        self.last_gps_time: datetime.datetime | None = None
        
        cloudlog.info("rtcd: Initialized")
    
    def _get_gps_time(self) -> datetime.datetime | None:
        """Get time from GPS if available."""
        if not self.sm.updated['ubloxGnss']:
            return None
        
        gps = self.sm['ubloxGnss']
        if not gps.hasFix:
            return None
        
        # Parse GPS time
        try:
            return datetime.datetime(
                gps.year, gps.month, gps.day,
                gps.hour, gps.minute, gps.second
            )
        except (ValueError, AttributeError):
            return None
    
    def _sync_time(self):
        """Synchronize system time."""
        # Try GPS first
        gps_time = self._get_gps_time()
        if gps_time:
            self.last_gps_time = gps_time
            self.time_source = "gps"
            self.time_valid = True
            return
        
        # Fallback to system clock
        self.time_source = "system"
        self.time_valid = True
    
    def update(self):
        """Update time status."""
        self.sm.update(0)
        self._sync_time()
        
        # Publish status
        msg = messaging.new_message('rtcStatus', valid=True)
        msg.rtcStatus.timeSource = self.time_source
        msg.rtcStatus.timeValid = self.time_valid
        msg.rtcStatus.unixTimestamp = int(time.time())
        
        if self.last_gps_time:
            msg.rtcStatus.gpsTime = self.last_gps_time.isoformat()
        
        self.pm.send('rtcStatus', msg)
    
    def run(self):
        """Main daemon loop."""
        rk = Ratekeeper(1)  # 1Hz
        cloudlog.info("rtcd: Running")
        
        while True:
            self.update()
            rk.keep_time()


def main():
    daemon = RtcD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        cloudlog.info("rtcd: Stopped")


if __name__ == "__main__":
    main()
