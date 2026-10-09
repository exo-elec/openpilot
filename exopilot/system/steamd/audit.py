#!/usr/bin/env python3
"""SteamD command audit log.

Persists every control command, engagement change, override, and link-loss
event to SQLite for post-incident forensics. Logged even if the command is
later clamped or overridden — the raw intent is what matters for analysis.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from openpilot.common.swaglog import cloudlog


class AuditLog:
  """Thread-safe SQLite audit log for teleoperation events."""

  DB_PATH = Path("/data/shared/exopilot/teleop_audit.db")
  _lock = threading.Lock()

  def __init__(self):
    self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    self._init_db()

  def _init_db(self):
    with sqlite3.connect(str(self.DB_PATH), check_same_thread=False) as conn:
      conn.execute(
        """
        CREATE TABLE IF NOT EXISTS teleop_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp REAL NOT NULL,
          event_type TEXT NOT NULL,
          source TEXT,
          client_id TEXT,
          steer REAL,
          accel REAL,
          gas REAL,
          brake REAL,
          engaged INTEGER,
          override_reason TEXT,
          v_ego REAL,
          details TEXT
        )
        """
      )
      conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teleop_time ON teleop_events(timestamp)"
      )
      conn.commit()

  def log_command(
    self,
    source: str,
    steer: float | None = None,
    accel: float | None = None,
    gas: float | None = None,
    brake: float | None = None,
    engaged: bool | None = None,
    client_id: str | None = None,
    v_ego: float | None = None,
  ):
    """Log a control command."""
    self._insert(
      "command",
      source=source,
      steer=steer,
      accel=accel,
      gas=gas,
      brake=brake,
      engaged=1 if engaged else 0 if engaged is not None else None,
      client_id=client_id,
      v_ego=v_ego,
    )

  def log_override(self, reason: str, v_ego: float | None = None):
    """Log a local override event."""
    self._insert("override", override_reason=reason, v_ego=v_ego)
    cloudlog.warning(f"SteamD audit: override ({reason})")

  def log_link_loss(self, elapsed_ms: float, v_ego: float | None = None):
    """Log a link-loss event."""
    self._insert("link_loss", details=f"elapsed_ms={elapsed_ms:.0f}", v_ego=v_ego)

  def log_session(self, event: str, client_id: str | None = None, details: str | None = None):
    """Log session lifecycle events (connect, disconnect, engage, disengage)."""
    self._insert("session", client_id=client_id, details=details)

  def _insert(self, event_type: str, **kwargs):
    with self._lock:
      try:
        with sqlite3.connect(str(self.DB_PATH), check_same_thread=False) as conn:
          conn.execute(
            """
            INSERT INTO teleop_events
            (timestamp, event_type, source, client_id, steer, accel, gas, brake, engaged, override_reason, v_ego, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
              time.monotonic(),
              event_type,
              kwargs.get("source"),
              kwargs.get("client_id"),
              kwargs.get("steer"),
              kwargs.get("accel"),
              kwargs.get("gas"),
              kwargs.get("brake"),
              kwargs.get("engaged"),
              kwargs.get("override_reason"),
              kwargs.get("v_ego"),
              kwargs.get("details"),
            ),
          )
          conn.commit()
      except Exception as e:
        cloudlog.error(f"SteamD audit log failed: {e}")
