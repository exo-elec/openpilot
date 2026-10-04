#!/usr/bin/env python3
"""
tripd.py - Trip Statistics Daemon

Tracks comprehensive trip statistics (moved from EOP10's selfdrive/tripd; shared by every branch):
- Lifetime distance, time, engagement
- Trip A/B (user resettable)
- Daily rolling statistics (7-day window)
- Drive detection and counting

Reference: TRIPD.md design document
"""

import json
import re
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


def _ngp_key(suffix: str) -> str:
    """TotalDistance -> ngp_trip_total_distance."""
    return "ngp_trip_" + re.sub(r"(?<!^)(?=[A-Z])", "_", suffix).lower()


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

    def __init__(self, keys=None):
        """`keys(suffix) -> param name` maps a stat to its param: `ngp_trip_total_distance` by default, EOP10's shim passes
        `EOPTrip<Suffix>` so its UI keeps reading the same keys."""
        self._key_fn = keys or _ngp_key
        self.params = Params()

        # Messaging
        self.sm = messaging.SubMaster(['carState', 'controlsState', 'selfdriveState', 'deviceState'])

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

    def _key(self, suffix: str) -> str:
        return self._key_fn(suffix)

    def _load_stats(self):
        """Load persisted statistics from params."""
        try:
            # Lifetime stats
            self.lifetime.distance = float(self.params.get(self._key("TotalDistance")) or 0.0)
            self.lifetime.onroad_time = float(self.params.get(self._key("UptimeOnroad")) or 0.0)
            self.lifetime.engaged_time = float(self.params.get(self._key("UptimeEngaged")) or 0.0)
            self.lifetime.drives = int(self.params.get(self._key("TotalDrives")) or 0)

            # Trip A baselines
            trip_a_start_distance = float(self.params.get(self._key("AStartDistance")) or 0.0)
            trip_a_start_time = float(self.params.get(self._key("AStartTime")) or 0.0)
            self.trip_a.distance = self.lifetime.distance - trip_a_start_distance
            self.trip_a.onroad_time = self.lifetime.onroad_time - trip_a_start_time

            # Trip B baselines
            trip_b_start_distance = float(self.params.get(self._key("BStartDistance")) or 0.0)
            trip_b_start_time = float(self.params.get(self._key("BStartTime")) or 0.0)
            self.trip_b.distance = self.lifetime.distance - trip_b_start_distance
            self.trip_b.onroad_time = self.lifetime.onroad_time - trip_b_start_time

            # Load daily stats for past 7 days
            self._load_daily_stats()

        except Exception as e:
            cloudlog.error(f"TripD: Failed to load stats: {e}")

    def _read_daily_blob(self) -> dict:
        """Daily stats live in one JSON param (Params keys are fixed, so per-date keys cannot be registered)."""
        try:
            raw = self.params.get(self._key("DailyStats"))
            blob = json.loads(raw) if raw else {}
            return blob if isinstance(blob, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _load_daily_stats(self):
        """Load daily statistics for rolling 7-day window."""
        today = datetime.now().date()
        blob = self._read_daily_blob()
        for i in range(7):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            daily = DailyStats(date=date_str)
            try:
                dist, onroad, engaged, drives = blob.get(date_str, [0.0, 0.0, 0.0, 0])
                daily.distance, daily.onroad_time, daily.engaged_time, daily.drives = float(dist), float(onroad), float(engaged), int(drives)
            except (ValueError, TypeError):
                pass
            self.daily_stats[date_str] = daily

    def _save_stats(self):
        """Persist statistics to params."""
        try:
            # Lifetime stats
            self.params.put(self._key("TotalDistance"), float(self.lifetime.distance))
            self.params.put(self._key("UptimeOnroad"), float(self.lifetime.onroad_time))
            self.params.put(self._key("UptimeEngaged"), float(self.lifetime.engaged_time))
            self.params.put(self._key("TotalDrives"), self.lifetime.drives)

            # Calculate and save engagement ratio
            engagement_ratio = self.lifetime.engagement_ratio
            self.params.put(self._key("LifetimeEngagementRatio"), float(engagement_ratio))

            # Save current daily stats
            self._save_daily_stats()

        except Exception as e:
            cloudlog.error(f"TripD: Failed to save stats: {e}")

    def _save_daily_stats(self):
        """Save the last 7 days of daily statistics as one JSON param."""
        today = datetime.now().date()
        keep = {(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)}
        blob = {d: [float(s.distance), float(s.onroad_time), float(s.engaged_time), int(s.drives)]
                for d, s in self.daily_stats.items() if d in keep}
        self.params.put(self._key("DailyStats"), json.dumps(blob))

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
            self.params.put(self._key("LastPersonalityTime"), json.dumps(self.current_session.personality_time))
            self.params.put(self._key("LastMaxAccel"), float(self.current_session.max_accel))
            self.params.put(self._key("LastOverrideFreeDistance"), float(self.current_session.longest_override_free_distance))
            self.params.put(self._key("LastDistance"), float(self.current_session.distance))
            self.params.put(self._key("LastDuration"), float(duration))
        except Exception as e:
            cloudlog.error(f"TripD: Failed to export per-drive stats: {e}")

        # Save immediately on drive end
        self._save_stats()

    def reset_trip_a(self):
        """Reset Trip A to current position."""
        self.params.put(self._key("AStartDistance"), float(self.lifetime.distance))
        self.params.put(self._key("AStartTime"), float(self.lifetime.onroad_time))
        self.trip_a = TripStats()
        cloudlog.info("TripD: Trip A reset")

    def reset_trip_b(self):
        """Reset Trip B to current position."""
        self.params.put(self._key("BStartDistance"), float(self.lifetime.distance))
        self.params.put(self._key("BStartTime"), float(self.lifetime.onroad_time))
        self.trip_b = TripStats()
        cloudlog.info("TripD: Trip B reset")

    def get_stats_dict(self) -> dict[str, Any]:
        """Get all statistics as dictionary."""
        # Update trip A/B from baselines
        trip_a_start_distance = float(self.params.get(self._key("AStartDistance")) or 0.0)
        trip_a_start_time = float(self.params.get(self._key("AStartTime")) or 0.0)
        self.trip_a.distance = self.lifetime.distance - trip_a_start_distance
        self.trip_a.onroad_time = self.lifetime.onroad_time - trip_a_start_time

        trip_b_start_distance = float(self.params.get(self._key("BStartDistance")) or 0.0)
        trip_b_start_time = float(self.params.get(self._key("BStartTime")) or 0.0)
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
        is_onroad = bool(self.sm['deviceState'].started)  # SelfdriveState has no isOnroad
        is_engaged = selfdrive_state.enabled  # ControlsState has no `enabled`; it is on SelfdriveState

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
