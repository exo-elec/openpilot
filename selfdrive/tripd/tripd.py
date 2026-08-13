#!/usr/bin/env python3
"""
tripd.py - Trip Statistics Daemon

Tracks comprehensive trip statistics for EOP:
- Lifetime distance, time, engagement
- Trip A/B (user resettable)
- Daily rolling statistics (7-day window)
- Drive detection and counting

Reference: TRIPD.md design document
"""

import time
from datetime import datetime, timedelta
from typing import Any
from dataclasses import dataclass, field
from enum import Enum

from cereal import log

import cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

_PERSONALITY_NAME_MAP = {v: k for k, v in log.LongitudinalPersonality.schema.enumerants.items()}


class DriveState(Enum):
    """Drive detection state machine states."""
    IDLE = "idle"
    STARTING = "starting"  # Checking if drive should start
    DRIVING = "driving"
    STOPPING = "stopping"  # Checking if drive should end


@dataclass
class TripStats:
    """Container for trip statistics."""
    distance: float = 0.0  # meters
    onroad_time: float = 0.0  # seconds
    engaged_time: float = 0.0  # seconds
    drives: int = 0
    # Per-drive detail metrics (EOP: merged from FrogPilot)
    personality_time: dict[str, float] = field(default_factory=dict)  # seconds per personality
    max_accel: float = 0.0  # m/s^2, peak positive acceleration
    longest_override_free_distance: float = 0.0  # meters, longest engaged segment without gas/brake

    @property
    def engagement_ratio(self) -> float:
        """Calculate engagement ratio as percentage."""
        if self.onroad_time <= 0:
            return 0.0
        return (self.engaged_time / self.onroad_time) * 100.0


@dataclass
class DailyStats:
    """Container for daily statistics."""
    date: str = ""  # YYYY-MM-DD
    distance: float = 0.0  # meters
    onroad_time: float = 0.0  # seconds
    engaged_time: float = 0.0  # seconds
    drives: int = 0


class DriveDetector:
    """
    State machine for drive start/end detection.

    Drive START: IsOnroad true + vEgo > 0.5 m/s for 5s
    Drive END: IsOnroad false OR vEgo < 0.3 m/s for 60s
    """

    # Thresholds
    START_SPEED_THRESHOLD = 0.5  # m/s
    START_TIME_THRESHOLD = 5.0  # seconds
    STOP_SPEED_THRESHOLD = 0.3  # m/s
    STOP_TIME_THRESHOLD = 60.0  # seconds

    def __init__(self):
        self.state = DriveState.IDLE
        self.state_entry_time = 0.0
        self.state_entry_speed = 0.0

    def update(self, is_onroad: bool, v_ego: float, current_time: float) -> tuple[bool, bool]:
        """
        Update drive detection state machine.

        Returns:
            (drive_started, drive_ended) tuple
        """
        drive_started = False
        drive_ended = False

        if self.state == DriveState.IDLE:
            if is_onroad and v_ego > self.START_SPEED_THRESHOLD:
                self._transition_to(DriveState.STARTING, current_time, v_ego)

        elif self.state == DriveState.STARTING:
            if not is_onroad:
                self._transition_to(DriveState.IDLE, current_time, v_ego)
            elif v_ego <= self.START_SPEED_THRESHOLD:
                # Speed dropped, reset timer
                self._transition_to(DriveState.IDLE, current_time, v_ego)
            elif current_time - self.state_entry_time >= self.START_TIME_THRESHOLD:
                # Drive confirmed started
                drive_started = True
                self._transition_to(DriveState.DRIVING, current_time, v_ego)

        elif self.state == DriveState.DRIVING:
            if not is_onroad:
                drive_ended = True
                self._transition_to(DriveState.IDLE, current_time, v_ego)
            elif v_ego < self.STOP_SPEED_THRESHOLD:
                self._transition_to(DriveState.STOPPING, current_time, v_ego)

        elif self.state == DriveState.STOPPING:
            if not is_onroad:
                drive_ended = True
                self._transition_to(DriveState.IDLE, current_time, v_ego)
            elif v_ego >= self.START_SPEED_THRESHOLD:
                # Moving again, back to driving
                self._transition_to(DriveState.DRIVING, current_time, v_ego)
            elif current_time - self.state_entry_time >= self.STOP_TIME_THRESHOLD:
                # Drive confirmed ended
                drive_ended = True
                self._transition_to(DriveState.IDLE, current_time, v_ego)

        return drive_started, drive_ended

    def _transition_to(self, new_state: DriveState, current_time: float, v_ego: float):
        """Transition to new state."""
        self.state = new_state
        self.state_entry_time = current_time
        self.state_entry_speed = v_ego


class TripD:
    """
    Trip Statistics Daemon

    Tracks:
    - Lifetime statistics (persistent)
    - Trip A/B (user resettable)
    - Daily rolling window (7 days)
    - Current drive session
    """

    # Update rate
    UPDATE_RATE = 1.0  # Hz

    # Persistence interval
    SAVE_INTERVAL = 5.0  # seconds

    def __init__(self):
        self.params = Params()

        # Messaging
        self.sm = messaging.SubMaster(['carState', 'controlsState', 'selfdriveState'])

        # Drive detection
        self.drive_detector = DriveDetector()
        self.is_driving = False

        # Statistics containers
        self.lifetime = TripStats()
        self.trip_a = TripStats()
        self.trip_b = TripStats()
        self.current_session = TripStats()
        self.daily_stats: dict[str, DailyStats] = {}

        # Per-drive tracking state
        self._current_personality: int | None = None
        self._personality_segment_start: float = 0.0
        self._override_free_distance: float = 0.0  # current engaged-no-override segment
        self._max_override_free_distance: float = 0.0

        # Timing
        self.last_update_time = 0.0
        self.last_save_time = 0.0
        self.session_start_time = 0.0

        # Load persisted data
        self._load_stats()

        cloudlog.info("TripD: Initialized")

    def _load_stats(self):
        """Load persisted statistics from params."""
        try:
            # Lifetime stats
            self.lifetime.distance = float(self.params.get("EOPTripTotalDistance") or 0.0)
            self.lifetime.onroad_time = float(self.params.get("EOPTripUptimeOnroad") or 0.0)
            self.lifetime.engaged_time = float(self.params.get("EOPTripUptimeEngaged") or 0.0)
            self.lifetime.drives = int(self.params.get("EOPTripTotalDrives") or 0)

            # Trip A baselines
            trip_a_start_distance = float(self.params.get("EOPTripAStartDistance") or 0.0)
            trip_a_start_time = float(self.params.get("EOPTripAStartTime") or 0.0)
            self.trip_a.distance = self.lifetime.distance - trip_a_start_distance
            self.trip_a.onroad_time = self.lifetime.onroad_time - trip_a_start_time

            # Trip B baselines
            trip_b_start_distance = float(self.params.get("EOPTripBStartDistance") or 0.0)
            trip_b_start_time = float(self.params.get("EOPTripBStartTime") or 0.0)
            self.trip_b.distance = self.lifetime.distance - trip_b_start_distance
            self.trip_b.onroad_time = self.lifetime.onroad_time - trip_b_start_time

            # Load daily stats for past 7 days
            self._load_daily_stats()

        except Exception as e:
            cloudlog.error(f"TripD: Failed to load stats: {e}")

    def _load_daily_stats(self):
        """Load daily statistics for rolling 7-day window."""
        today = datetime.now().date()
        for i in range(7):
            date = today - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")

            daily = DailyStats(date=date_str)
            daily.distance = float(self.params.get(f"Daily_{date_str}_Distance") or 0.0)
            daily.onroad_time = float(self.params.get(f"Daily_{date_str}_Time") or 0.0)
            daily.engaged_time = float(self.params.get(f"Daily_{date_str}_EngagedTime") or 0.0)
            daily.drives = int(self.params.get(f"Daily_{date_str}_Drives") or 0)

            self.daily_stats[date_str] = daily

    def _save_stats(self):
        """Persist statistics to params."""
        try:
            # Lifetime stats
            self.params.put("EOPTripTotalDistance", float(self.lifetime.distance))
            self.params.put("EOPTripUptimeOnroad", float(self.lifetime.onroad_time))
            self.params.put("EOPTripUptimeEngaged", float(self.lifetime.engaged_time))
            self.params.put("EOPTripTotalDrives", self.lifetime.drives)

            # Calculate and save engagement ratio
            engagement_ratio = self.lifetime.engagement_ratio
            self.params.put("EOPTripLifetimeEngagementRatio", float(engagement_ratio))

            # Save current daily stats
            self._save_daily_stats()

        except Exception as e:
            cloudlog.error(f"TripD: Failed to save stats: {e}")

    def _save_daily_stats(self):
        """Save daily statistics."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        if today_str in self.daily_stats:
            daily = self.daily_stats[today_str]
            self.params.put(f"Daily_{today_str}_Distance", float(daily.distance))
            self.params.put(f"Daily_{today_str}_Time", float(daily.onroad_time))
            self.params.put(f"Daily_{today_str}_EngagedTime", float(daily.engaged_time))
            self.params.put(f"Daily_{today_str}_Drives", daily.drives)

    def _get_or_create_daily_stats(self, date_str: str) -> DailyStats:
        """Get or create daily stats for date."""
        if date_str not in self.daily_stats:
            self.daily_stats[date_str] = DailyStats(date=date_str)
        return self.daily_stats[date_str]

    def _update_distance(self, v_ego: float, dt: float):
        """Update distance traveled."""
        # Simple integration: distance = speed * time
        distance_delta = v_ego * dt

        if distance_delta > 0:
            self.lifetime.distance += distance_delta
            self.current_session.distance += distance_delta

            # Update today's daily stats
            today_str = datetime.now().strftime("%Y-%m-%d")
            daily = self._get_or_create_daily_stats(today_str)
            daily.distance += distance_delta

    def _update_time(self, is_onroad: bool, is_engaged: bool, dt: float):
        """Update time statistics."""
        if is_onroad:
            self.lifetime.onroad_time += dt
            self.current_session.onroad_time += dt

            if is_engaged:
                self.lifetime.engaged_time += dt
                self.current_session.engaged_time += dt

            # Update today's daily stats
            today_str = datetime.now().strftime("%Y-%m-%d")
            daily = self._get_or_create_daily_stats(today_str)
            daily.onroad_time += dt
            if is_engaged:
                daily.engaged_time += dt

    def _update_personality_time(self, personality: int, current_time: float):
        """Track time spent in each longitudinal personality."""
        if self._current_personality != personality:
            # Finalize previous segment
            if self._current_personality is not None:
                elapsed = current_time - self._personality_segment_start
                prev_name = _PERSONALITY_NAME_MAP.get(self._current_personality, 'unknown')
                self.current_session.personality_time[prev_name] = \
                    self.current_session.personality_time.get(prev_name, 0.0) + elapsed
            self._current_personality = personality
            self._personality_segment_start = current_time

    def _update_max_accel(self, a_ego: float):
        """Track peak positive acceleration."""
        if a_ego > self.current_session.max_accel:
            self.current_session.max_accel = a_ego

    def _update_override_free_distance(self, v_ego: float, is_engaged: bool,
                                       gas_pressed: bool, brake_pressed: bool, dt: float):
        """Track longest engaged segment without driver override."""
        overridden = gas_pressed or brake_pressed
        if is_engaged and not overridden:
            self._override_free_distance += v_ego * dt
        else:
            # Segment ended — record max if applicable
            if self._override_free_distance > self._max_override_free_distance:
                self._max_override_free_distance = self._override_free_distance
            self._override_free_distance = 0.0

    def _handle_drive_start(self):
        """Handle drive start event."""
        self.is_driving = True
        self.session_start_time = time.monotonic()
        self.current_session = TripStats()  # Reset session stats

        # Reset per-drive tracking state
        self._current_personality = None
        self._personality_segment_start = time.monotonic()
        self._override_free_distance = 0.0
        self._max_override_free_distance = 0.0

        cloudlog.info("TripD: Drive started")

    def _handle_drive_end(self):
        """Handle drive end event."""
        if not self.is_driving:
            return

        self.is_driving = False
        self.lifetime.drives += 1

        # Update today's drive count
        today_str = datetime.now().strftime("%Y-%m-%d")
        daily = self._get_or_create_daily_stats(today_str)
        daily.drives += 1

        # Finalize active personality segment so last personality time isn't lost
        if self._current_personality is not None:
            elapsed = time.monotonic() - self._personality_segment_start
            prev_name = _PERSONALITY_NAME_MAP.get(self._current_personality, 'unknown')
            self.current_session.personality_time[prev_name] = \
                self.current_session.personality_time.get(prev_name, 0.0) + elapsed

        # Finalize per-drive metrics
        self.current_session.longest_override_free_distance = self._max_override_free_distance

        # Log session summary
        duration = time.monotonic() - self.session_start_time
        personality_summary = ", ".join(
            f"{k}={v:.0f}s" for k, v in sorted(self.current_session.personality_time.items())
        )
        cloudlog.info(
            f"TripD: Drive ended - Distance: {self.current_session.distance:.1f}m, "
            + f"Duration: {duration:.1f}s, "
            + f"Engaged: {self.current_session.engagement_ratio:.1f}%, "
            + f"MaxAccel: {self.current_session.max_accel:.2f}m/s², "
            + f"LongestOverrideFree: {self.current_session.longest_override_free_distance:.0f}m, "
            + f"Personalities: [{personality_summary}]"
        )

        # Export per-drive detail stats to params
        try:
            import json
            self.params.put("EOPTripLastPersonalityTime", json.dumps(self.current_session.personality_time))
            self.params.put("EOPTripLastMaxAccel", float(self.current_session.max_accel))
            self.params.put("EOPTripLastOverrideFreeDistance", float(self.current_session.longest_override_free_distance))
            self.params.put("EOPTripLastDistance", float(self.current_session.distance))
            self.params.put("EOPTripLastDuration", float(duration))
        except Exception as e:
            cloudlog.error(f"TripD: Failed to export per-drive stats: {e}")

        # Save immediately on drive end
        self._save_stats()

    def reset_trip_a(self):
        """Reset Trip A to current position."""
        self.params.put("EOPTripAStartDistance", float(self.lifetime.distance))
        self.params.put("EOPTripAStartTime", float(self.lifetime.onroad_time))
        self.trip_a = TripStats()
        cloudlog.info("TripD: Trip A reset")

    def reset_trip_b(self):
        """Reset Trip B to current position."""
        self.params.put("EOPTripBStartDistance", float(self.lifetime.distance))
        self.params.put("EOPTripBStartTime", float(self.lifetime.onroad_time))
        self.trip_b = TripStats()
        cloudlog.info("TripD: Trip B reset")

    def get_stats_dict(self) -> dict[str, Any]:
        """Get all statistics as dictionary."""
        # Update trip A/B from baselines
        trip_a_start_distance = float(self.params.get("EOPTripAStartDistance") or 0.0)
        trip_a_start_time = float(self.params.get("EOPTripAStartTime") or 0.0)
        self.trip_a.distance = self.lifetime.distance - trip_a_start_distance
        self.trip_a.onroad_time = self.lifetime.onroad_time - trip_a_start_time

        trip_b_start_distance = float(self.params.get("EOPTripBStartDistance") or 0.0)
        trip_b_start_time = float(self.params.get("EOPTripBStartTime") or 0.0)
        self.trip_b.distance = self.lifetime.distance - trip_b_start_distance
        self.trip_b.onroad_time = self.lifetime.onroad_time - trip_b_start_time

        return {
            'lifetime': {
                'distance': self.lifetime.distance,
                'onroad_time': self.lifetime.onroad_time,
                'engaged_time': self.lifetime.engaged_time,
                'engagement_ratio': self.lifetime.engagement_ratio,
                'drives': self.lifetime.drives,
            },
            'trip_a': {
                'distance': self.trip_a.distance,
                'onroad_time': self.trip_a.onroad_time,
            },
            'trip_b': {
                'distance': self.trip_b.distance,
                'onroad_time': self.trip_b.onroad_time,
            },
            'current_session': {
                'distance': self.current_session.distance,
                'onroad_time': self.current_session.onroad_time,
                'engaged_time': self.current_session.engaged_time,
                'is_driving': self.is_driving,
                'personality_time': self.current_session.personality_time,
                'max_accel': self.current_session.max_accel,
                'longest_override_free_distance': self.current_session.longest_override_free_distance,
            },
        }

    def update(self):
        """Single update iteration."""
        self.sm.update(0)

        current_time = time.monotonic()

        # Calculate delta time
        if self.last_update_time == 0:
            dt = 0.0
        else:
            dt = current_time - self.last_update_time
        self.last_update_time = current_time

        # Get inputs
        car_state = self.sm['carState']
        controls_state = self.sm['controlsState']
        selfdrive_state = self.sm['selfdriveState']

        v_ego = car_state.vEgo
        is_onroad = selfdrive_state.isOnroad
        is_engaged = controls_state.enabled

        # Update drive detection
        drive_started, drive_ended = self.drive_detector.update(
            is_onroad, v_ego, current_time
        )

        if drive_started:
            self._handle_drive_start()

        if drive_ended:
            self._handle_drive_end()

        # Update statistics (only if we're in a drive or onroad)
        if is_onroad or self.is_driving:
            self._update_distance(v_ego, dt)
            self._update_time(is_onroad, is_engaged, dt)
            self._update_max_accel(car_state.aEgo)
            self._update_override_free_distance(
                v_ego, is_engaged, car_state.gasPressed, car_state.brakePressed, dt
            )
            self._update_personality_time(selfdrive_state.personality, current_time)

        # Periodic save
        if current_time - self.last_save_time >= self.SAVE_INTERVAL:
            self._save_stats()
            self.last_save_time = current_time

    def run(self):
        """Main daemon loop."""
        cloudlog.info("TripD: Starting daemon")

        rk = Ratekeeper(self.UPDATE_RATE)

        while True:
            self.update()
            rk.keep_time()


def main():
    """Entry point."""
    tripd = TripD()
    tripd.run()


if __name__ == "__main__":
    main()
