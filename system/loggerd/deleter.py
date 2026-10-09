#!/usr/bin/env python3
"""Log deletion daemon with storage policy management.

Manages log storage by enforcing size limits, free space requirements,
and age-based cleanup policies.
"""
import threading
from pathlib import Path

from openpilot.system.hardware.hw import Paths
from openpilot.common.swaglog import cloudlog
from openpilot.system.loggerd.config import get_available_bytes, get_available_percent
from openpilot.system.loggerd.storage_policy import StoragePolicy, StorageLimits

# Upstream API surface — the preserve-xattr constants live on StoragePolicy now
PRESERVE_ATTR_NAME = StoragePolicy.PRESERVE_ATTR_NAME
PRESERVE_ATTR_VALUE = StoragePolicy.PRESERVE_ATTR_VALUE
PRESERVE_COUNT = StoragePolicy.PRESERVE_COUNT

# Legacy limits (kept for compatibility)
MIN_BYTES = 5 * 1024 * 1024 * 1024
MIN_PERCENT = 10

# Storage policy configuration
STORAGE_CONFIG = StorageLimits(
    max_total_size_gb=100.0,
    max_single_segment_gb=4.0,
    max_segment_duration_min=10,
    min_free_space_gb=5.0,
    max_age_days=7,
)


def deleter_thread(exit_event: threading.Event):
    """Main deletion thread using storage policy.

    Uses StoragePolicy for intelligent cleanup while maintaining
    backward compatibility with existing limit checks.
    """
    # Initialize storage policy
    policy = StoragePolicy(
        base_path=Path(Paths.log_root()),
        limits=STORAGE_CONFIG,
        on_cleanup=lambda deleted: cloudlog.info(f"deleter: Cleaned up {len(deleted)} segments")
    )

    cloudlog.info("deleter: Started with storage policy")

    # Backoff state: when cleanup cannot make progress (e.g. every segment is
    # locked or preserved), increase sleep time to avoid a tight spin loop.
    no_progress_backoff = 0.1
    last_progress = True

    while not exit_event.is_set():
      try:
        # Check legacy limits for compatibility
        out_of_bytes = get_available_bytes(default=MIN_BYTES + 1) < MIN_BYTES
        out_of_percent = get_available_percent(default=MIN_PERCENT + 1) < MIN_PERCENT

        # Also check storage policy limits
        low_free_space = not policy.check_free_space()

        if out_of_percent or out_of_bytes or low_free_space:
            # Use storage policy for cleanup; force guarantees progress even
            # when the legacy statvfs signal and the policy's shutil metrics
            # disagree (otherwise this loop would spin without freeing space)
            deleted = policy.enforce_limits(force=out_of_percent or out_of_bytes)

            if deleted:
                cloudlog.info(f"deleter: Deleted {len(deleted)} segments to free space")
                no_progress_backoff = 0.1
                last_progress = True
            else:
                last_progress = False

            # Log current stats
            stats = policy.get_stats()
            cloudlog.debug(f"deleter: {stats['segment_count']} segments, " +
                          f"{stats['total_size_gb']}GB used, " +
                          f"{stats['free_space_gb']}GB free")

            # If no progress was made, back off to avoid hammering a full disk
            # where every remaining segment is locked/preserved.
            if not last_progress:
                no_progress_backoff = min(no_progress_backoff * 2, 30.0)
                cloudlog.warning(f"deleter: cleanup made no progress, backing off {no_progress_backoff:.1f}s")
                exit_event.wait(no_progress_backoff)
            else:
                exit_event.wait(0.1)
        else:
            no_progress_backoff = 0.1
            # Periodic stats logging (every 30 cycles ~ 15 minutes)
            if not hasattr(deleter_thread, '_stats_counter'):
                deleter_thread._stats_counter = 0
            deleter_thread._stats_counter += 1

            if deleter_thread._stats_counter >= 30:
                stats = policy.get_stats()
                cloudlog.info(f"deleter: Stats - {stats}")
                deleter_thread._stats_counter = 0

            exit_event.wait(30)
      except Exception:
        # an always-run daemon must survive transient errors (bad mounts,
        # racing segment removal, metric failures) — log and keep going
        cloudlog.exception("deleter: error in cleanup loop")
        exit_event.wait(5)


def main():
    deleter_thread(threading.Event())


if __name__ == "__main__":
    main()
