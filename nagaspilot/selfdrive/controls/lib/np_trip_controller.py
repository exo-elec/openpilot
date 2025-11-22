#!/usr/bin/env python3
"""
NagasPilot Trip Controller - Simple Backend Service
Provides trip statistics for prime.cc UI using proven FrogPilot/sunnypilot patterns.

Design Principle: Backend only calculates, UI only displays.
"""

import time
import json
from datetime import datetime, timedelta
from cereal import messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper

# Update frequency - 1Hz for efficiency (FrogPilot pattern)
UPDATE_RATE_HZ = 1.0
DT = 1.0 / UPDATE_RATE_HZ

class NpTripController:
    # Flow overview for readability:
    # 1. __init__ loads persisted counters/trip baselines and sets up the
    #    message subscriptions/Params helper used everywhere else.
    # 2. update() is the 1Hz loop; it grabs new carState/controlsState,
    #    calls the helpers below to accumulate totals, trip A/B stats,
    #    and weekly history, then writes the JSON blob when safe.
    # 3. All helpers are intentionally pure data transforms so future
    #    tweaks (like adding new params) can hook in without touching
    #    the scheduler in update().
    """
    Simple trip controller that provides data for prime.cc UI.

    Following FrogPilot's proven architecture:
    - Simple state accumulation
    - Non-blocking parameter writes
    - Minimal processing overhead
    """

    def __init__(self):
        """Initialize trip controller with basic state tracking"""
        self.params = Params()

        # Message subscriber for sensor data (sunnypilot pattern)
        self.sm = messaging.SubMaster([
            'carState',      # Vehicle speed, standstill detection
            'controlsState'  # Engagement state
        ])

        # Last known sensor snapshot
        self.current_speed = 0.0
        self.current_engaged = False
        self.current_standstill = True

        # Simple totals (persistent across restarts)
        self.total_distance = self._load_float_param("np_total_distance", 0.0)
        self.total_time = self._load_float_param("np_total_uptime_onroad", 0.0)
        self.total_drives = self._load_int_param("np_total_drives", 0)
        self.engaged_time = self._load_float_param("np_total_engaged_time", 0.0)

        # Trip A/B baselines (persistent)
        self.trip_a_start_distance = self._load_float_param("np_trip_a_start_distance", self.total_distance)
        self.trip_a_start_time = self._load_float_param("np_trip_a_start_time", self.total_time)
        self.trip_b_start_distance = self._load_float_param("np_trip_b_start_distance", self.total_distance)
        self.trip_b_start_time = self._load_float_param("np_trip_b_start_time", self.total_time)
        self.current_trip_mode = self._load_int_param("np_trip_mode", 0)  # 0=A, 1=B

        # Session tracking for drive counting (FrogPilot pattern)
        self.drive_start_distance = self.total_distance
        self.drive_added = False
        self.was_engaged = False
        self.last_standstill = True

        # Weekly statistics tracking
        self.weekly_stats = self._load_weekly_stats()
        self.last_daily_update = datetime.now().strftime("%Y-%m-%d")

        # FrogPilot smart write timing pattern
        self.last_write_time = 0
        self.tracked_time = 0.0  # Time spent tracking (moving or engaged)
        self.was_engaged_this_session = False
        self.idle_time = 0.0

        # Load or migrate to FrogPilot JSON blob pattern
        self._load_or_migrate_stats()
        self.drive_start_distance = self.total_distance
        self._reset_daily_tracking_baselines()

        print(f"NpTripController initialized - Distance: {self.total_distance}m, Time: {self.total_time}s, Drives: {self.total_drives}")

    def _load_float_param(self, key, default):
        """Load float parameter with safe fallback (sunnypilot pattern)"""
        try:
            param_str = self.params.get(key)
            return float(param_str) if param_str else default
        except (ValueError, TypeError):
            return default

    def _load_int_param(self, key, default):
        """Load int parameter with safe fallback (sunnypilot pattern)"""
        try:
            param_str = self.params.get(key)
            return int(param_str) if param_str else default
        except (ValueError, TypeError):
            return default

    def _load_or_migrate_stats(self):
        """Load NagasPilotStats JSON blob or migrate from legacy parameters (FrogPilot pattern)"""
        try:
            stats_json = self.params.get("NagasPilotStats")
            if stats_json:
                # Load from JSON blob
                stats = json.loads(stats_json)
                self.total_distance = float(stats.get("TotalDistance", self.total_distance))
                self.total_time = float(stats.get("TotalTime", self.total_time))
                self.total_drives = int(stats.get("TotalDrives", self.total_drives))
                self.engaged_time = float(stats.get("EngagedTime", self.engaged_time))
                self.trip_a_start_distance = float(stats.get("TripAStartDistance", self.trip_a_start_distance))
                self.trip_a_start_time = float(stats.get("TripAStartTime", self.trip_a_start_time))
                self.trip_b_start_distance = float(stats.get("TripBStartDistance", self.trip_b_start_distance))
                self.trip_b_start_time = float(stats.get("TripBStartTime", self.trip_b_start_time))
                self.current_trip_mode = int(stats.get("TripMode", self.current_trip_mode))
                print("Loaded stats from NagasPilotStats JSON blob")
            else:
                # First run - save initial JSON blob
                print("Creating initial NagasPilotStats JSON blob")
                self._write_stats_json()
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            print(f"Failed to load NagasPilotStats, using defaults: {e}")
            # Initialize with defaults already set

    def _load_weekly_stats(self):
        """Load weekly statistics from parameters"""
        try:
            weekly_raw = self.params.get("np_trip_weekly_stats")
            weekly_data = json.loads(weekly_raw) if weekly_raw else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            weekly_data = {}

        weekly_stats = {}
        for i in range(7):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            daily_data = weekly_data.get(date, {})
            weekly_stats[date] = {
                "distance": float(daily_data.get("distance", 0.0)),
                "time": float(daily_data.get("time", 0.0)),
                "drives": int(daily_data.get("drives", 0)),
                "engaged_time": float(daily_data.get("engaged_time", 0.0)),
            }
        return weekly_stats

    def _reset_daily_tracking_baselines(self):
        """Reset baseline used to accumulate rolling daily stats"""
        self._daily_last_distance = self.total_distance
        self._daily_last_time = self.total_time
        self._daily_last_drives = self.total_drives
        self._daily_last_engaged = self.engaged_time

    def _update_daily_stats(self):
        """Update daily statistics for rolling weekly window"""
        current_date = datetime.now().strftime("%Y-%m-%d")

        if current_date not in self.weekly_stats:
            self.weekly_stats[current_date] = {"distance": 0.0, "time": 0.0, "drives": 0, "engaged_time": 0.0}

        # If the date rolled over, reset running baselines
        if current_date != self.last_daily_update:
            self._reset_daily_tracking_baselines()
            self.last_daily_update = current_date

        # Per-loop deltas since last accumulation
        session_distance = max(0.0, self.total_distance - self._daily_last_distance)
        session_time = max(0.0, self.total_time - self._daily_last_time)
        session_drives = max(0, self.total_drives - self._daily_last_drives)
        session_engaged = max(0.0, self.engaged_time - self._daily_last_engaged)

        if any(value > 0 for value in (session_distance, session_time, session_drives, session_engaged)):
            today_stats = self.weekly_stats[current_date]
            today_stats["distance"] += session_distance
            today_stats["time"] += session_time
            today_stats["drives"] += session_drives
            today_stats["engaged_time"] += session_engaged

        # Cleanup old stats (keep only 7 days)
        self._cleanup_old_daily_stats()
        self._persist_weekly_stats()

        # Update baseline for next loop
        self._daily_last_distance = self.total_distance
        self._daily_last_time = self.total_time
        self._daily_last_drives = self.total_drives
        self._daily_last_engaged = self.engaged_time

    def _cleanup_old_daily_stats(self):
        """Remove daily stats older than 7 days"""
        cutoff_date = datetime.now() - timedelta(days=7)
        for date in list(self.weekly_stats.keys()):
            try:
                if datetime.strptime(date, "%Y-%m-%d") < cutoff_date:
                    self.weekly_stats.pop(date, None)
            except ValueError:
                self.weekly_stats.pop(date, None)

    def _persist_weekly_stats(self):
        """Persist weekly stats as a single JSON blob"""
        try:
            self.params.put_nonblocking("np_trip_weekly_stats", json.dumps(self.weekly_stats))
        except Exception:
            pass

    def _calculate_weekly_totals(self):
        """Calculate true 7-day rolling window statistics"""
        week_distance = 0.0
        week_time = 0.0
        week_drives = 0
        week_engaged_time = 0.0

        # Sum last 7 days including today
        for i in range(7):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            if date in self.weekly_stats:
                stats = self.weekly_stats[date]
                week_distance += stats["distance"]
                week_time += stats["time"]
                week_drives += stats["drives"]
                week_engaged_time += stats["engaged_time"]

        return week_distance, week_time, week_drives, week_engaged_time

    def update(self):
        """Main update loop - simple data collection and calculation"""
        # Update message subscriber
        self.sm.update(0)

        # 1. COLLECT simple sensor data
        if not self._collect_basic_data():
            # Still handle UI requests even if sensors aren't ready yet
            self._handle_ui_requests()
            return

        # 2. UPDATE simple totals
        self._update_totals()

        # 3. CALCULATE trip deltas for UI
        self._calculate_trip_values()

        # 4. UPDATE weekly statistics
        self._update_daily_stats()

        # 5. HANDLE UI triggers
        self._handle_ui_requests()

        # 6. WRITE parameters using FrogPilot's smart timing
        if self._should_write_stats():
            self._write_stats_json()
            self.last_write_time = time.time()

    def _collect_basic_data(self):
        """Collect basic sensor data with validation"""
        if not self.sm.updated['carState']:
            return False

        # Speed with bounds checking (sunnypilot pattern)
        v_ego = max(self.sm['carState'].vEgo, 0.0)  # Ensure non-negative
        v_ego = min(v_ego, 100.0)  # Reasonable max speed (100 m/s = 360 km/h)

        # Engagement detection
        engaged = self.sm['controlsState'].enabled if self.sm.updated['controlsState'] else False

        # Standstill detection for drive counting
        standstill = self.sm['carState'].standstill if hasattr(self.sm['carState'], 'standstill') else (v_ego < 0.1)

        # Store for totals update
        self.current_speed = v_ego
        self.current_engaged = engaged
        self.current_standstill = standstill

        return True

    def _update_totals(self):
        """Update lifetime totals (FrogPilot accumulation pattern)"""
        # Accumulate distance continuously
        self.total_distance += self.current_speed * DT

        # Accumulate time when moving or engaged (like FrogPilot)
        if self.current_speed > 0.1 or self.current_engaged:
            self.total_time += DT
            self.tracked_time += DT  # FrogPilot tracked time pattern

        # Track engagement for session-based writing
        if self.current_engaged:
            self.engaged_time += DT
            self.was_engaged_this_session = True

        # Drive counting logic (FrogPilot pattern)
        self._update_drive_count()

        # Reset write gating once the car has been idle for a bit
        if self.current_standstill and not self.current_engaged:
            self.idle_time += DT
            if self.idle_time >= 5.0:
                self.tracked_time = 0.0
                self.was_engaged_this_session = False
        else:
            self.idle_time = 0.0

    def _update_drive_count(self):
        """Update drive count using FrogPilot's enhanced logic"""
        # Detect start of new drive (was standstill, now moving and engaged)
        if (self.last_standstill and
            not self.current_standstill and
            self.current_engaged and
            not self.drive_added):

            # FrogPilot enhanced thresholds: 100m distance + 60s tracked time
            distance_since_start = self.total_distance - self.drive_start_distance
            if distance_since_start > 100.0 and self.tracked_time > 60.0:
                self.total_drives += 1
                self.drive_added = True
                print(f"New drive detected - Total drives: {self.total_drives}")

        # Reset drive detection when parked for a while
        if self.current_standstill and not self.current_engaged:
            if not self.was_engaged:  # Was parked and still parked
                self.drive_added = False
                self.drive_start_distance = self.total_distance

        # Update state for next iteration
        self.last_standstill = self.current_standstill
        self.was_engaged = self.current_engaged

    def _calculate_trip_values(self):
        """Calculate trip deltas for UI (backend does the math, UI just displays)"""
        # Trip A calculations
        self.trip_a_distance = max(0.0, self.total_distance - self.trip_a_start_distance)
        self.trip_a_time = max(0.0, self.total_time - self.trip_a_start_time)

        # Trip B calculations
        self.trip_b_distance = max(0.0, self.total_distance - self.trip_b_start_distance)
        self.trip_b_time = max(0.0, self.total_time - self.trip_b_start_time)

        # Auto-reset logic (moved from UI to backend for clean separation)
        self._handle_auto_reset()

    def _handle_auto_reset(self):
        """Handle automatic trip reset at 1000km (moved from UI)"""
        # Auto-reset Trip A at 1000km
        if self.trip_a_distance >= 1000000.0:  # 1000km in meters
            self.trip_a_start_distance = self.total_distance
            self.trip_a_start_time = self.total_time
            print("Auto-reset Trip A at 1000km")

        # Auto-reset Trip B at 1000km
        if self.trip_b_distance >= 1000000.0:  # 1000km in meters
            self.trip_b_start_distance = self.total_distance
            self.trip_b_start_time = self.total_time
            print("Auto-reset Trip B at 1000km")

    def _handle_ui_requests(self):
        """Handle requests from UI (trip reset, mode change)"""
        # Handle trip reset requests from UI
        reset_request = self.params.get("np_trip_reset_request")
        if reset_request:
            try:
                trip_to_reset = int(reset_request)
                if trip_to_reset == 0:  # Reset Trip A
                    self.trip_a_start_distance = self.total_distance
                    self.trip_a_start_time = self.total_time
                    print("Manual reset Trip A")
                elif trip_to_reset == 1:  # Reset Trip B
                    self.trip_b_start_distance = self.total_distance
                    self.trip_b_start_time = self.total_time
                    print("Manual reset Trip B")

                # Clear the request and confirm completion
                self.params.delete("np_trip_reset_request")
                self.params.put_nonblocking("np_trip_reset_status", f"completed_{trip_to_reset}_{int(time.time())}")
            except (ValueError, TypeError):
                # Clear invalid request
                self.params.delete("np_trip_reset_request")

        # Update trip mode from UI
        try:
            mode_param = self.params.get("np_trip_mode")
            if mode_param:
                new_mode = int(mode_param)
                if new_mode in [0, 1] and new_mode != self.current_trip_mode:
                    self.current_trip_mode = new_mode
                    print(f"Trip mode changed to: {'A' if new_mode == 0 else 'B'}")
        except (ValueError, TypeError):
            pass  # Ignore invalid mode changes

    def _should_write_stats(self):
        """FrogPilot smart write timing: write at standstill after 60s tracking + engagement"""
        return (self.tracked_time > 60.0 and
                self.current_standstill and
                self.was_engaged_this_session)

    def _write_stats_json(self):
        """Write all statistics to single JSON blob (FrogPilot pattern)"""
        # Calculate engagement ratio
        engagement_ratio = (self.engaged_time / self.total_time * 100.0) if self.total_time > 0 else 0.0

        # Calculate trip deltas
        trip_a_distance = max(0.0, self.total_distance - self.trip_a_start_distance)
        trip_a_time = max(0.0, self.total_time - self.trip_a_start_time)
        trip_b_distance = max(0.0, self.total_distance - self.trip_b_start_distance)
        trip_b_time = max(0.0, self.total_time - self.trip_b_start_time)

        # Calculate weekly totals
        week_distance, week_time, week_drives, week_engaged_time = self._calculate_weekly_totals()
        week_engagement_ratio = (week_engaged_time / week_time * 100.0) if week_time > 0 else 0.0

        # Create FrogPilot-style JSON blob
        stats = {
            # Lifetime totals
            "TotalDistance": self.total_distance,
            "TotalTime": self.total_time,
            "TotalDrives": self.total_drives,
            "EngagedTime": self.engaged_time,
            "LifetimeEngagementRatio": engagement_ratio,

            # Trip A/B data
            "TripADistance": trip_a_distance,
            "TripATime": trip_a_time,
            "TripBDistance": trip_b_distance,
            "TripBTime": trip_b_time,
            "TripMode": self.current_trip_mode,

            # Trip A/B baselines (for backend persistence)
            "TripAStartDistance": self.trip_a_start_distance,
            "TripAStartTime": self.trip_a_start_time,
            "TripBStartDistance": self.trip_b_start_distance,
            "TripBStartTime": self.trip_b_start_time,

            # Weekly statistics
            "WeekDistance": week_distance,
            "WeekTime": week_time,
            "WeekDrives": week_drives,
            "WeekEngagementRatio": week_engagement_ratio,

            # System info
            "LastUpdate": int(time.time())
        }

        # Write JSON blob with DONT_LOG flag to prevent excessive logging
        stats_json = json.dumps(stats, separators=(',', ':'))  # Compact JSON
        self.params.put_nonblocking("NagasPilotStats", stats_json)
        # Also expose key values individually for UI widgets
        self.params.put_nonblocking("np_total_distance", f"{self.total_distance}")
        self.params.put_nonblocking("np_total_uptime_onroad", f"{self.total_time}")
        self.params.put_nonblocking("np_total_drives", str(self.total_drives))
        self.params.put_nonblocking("np_total_engaged_time", f"{self.engaged_time}")
        self.params.put_nonblocking("np_trip_a_start_distance", f"{self.trip_a_start_distance}")
        self.params.put_nonblocking("np_trip_a_start_time", f"{self.trip_a_start_time}")
        self.params.put_nonblocking("np_trip_b_start_distance", f"{self.trip_b_start_distance}")
        self.params.put_nonblocking("np_trip_b_start_time", f"{self.trip_b_start_time}")
        self.params.put_nonblocking("np_trip_mode", str(self.current_trip_mode))

        print(f"Stats written: {self.total_distance:.0f}m, {self.total_drives} drives, {engagement_ratio:.1f}% engaged")

        # Reset timers after successful write
        self.tracked_time = 0.0
        self.was_engaged_this_session = False
        self.idle_time = 0.0



def main():
    """Main entry point following FrogPilot service pattern"""
    print("Starting NagasPilot Trip Controller...")

    # Initialize controller
    controller = NpTripController()

    # Rate keeper for consistent 1Hz updates (FrogPilot pattern)
    ratekeeper = Ratekeeper(UPDATE_RATE_HZ)

    try:
        while True:
            # Update trip statistics
            controller.update()

            # Maintain consistent update rate
            ratekeeper.keep_time()

    except KeyboardInterrupt:
        print("NagasPilot Trip Controller stopped by user")
    except Exception as e:
        print(f"NagasPilot Trip Controller error: {e}")
        raise


if __name__ == "__main__":
    main()
