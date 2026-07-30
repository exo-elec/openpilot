"""Storage policy for log management with rotation and cleanup.

Provides configurable storage limits, automatic rotation, and
cleanup policies for log segments.
"""
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from collections.abc import Callable
import threading

from openpilot.common.swaglog import cloudlog
from openpilot.system.loggerd.xattr_cache import getxattr


@dataclass
class StorageLimits:
    """Storage limit configuration.
    
    Args:
        max_total_size_gb: Maximum total storage for all segments
        max_single_segment_gb: Maximum size for a single segment
        max_segment_duration_min: Maximum duration for a single segment
        min_free_space_gb: Minimum free space to maintain
        max_age_days: Maximum age of segments (None = no limit)
    """
    max_total_size_gb: float = 100.0
    max_single_segment_gb: float = 4.0
    max_segment_duration_min: int = 10
    min_free_space_gb: float = 5.0
    max_age_days: int | None = 7


class StoragePolicy:
    """Manages log storage with rotation and cleanup policies.
    
    Enforces storage limits by rotating segments when they exceed
    size/duration limits and cleaning up old segments when total
    storage exceeds limits.
    
    Args:
        base_path: Root directory for log storage
        limits: Storage limit configuration
        on_cleanup: Optional callback when segments are deleted
    
    Example:
        >>> policy = StoragePolicy(
        ...     Path("/data/media/0/realdata"),
        ...     StorageLimits(max_total_size_gb=100, max_age_days=7)
        ... )
        >>> if policy.should_rotate(current_segment):
        ...     start_new_segment()
        >>> deleted = policy.enforce_limits()
    """
    
    PRESERVE_ATTR_NAME = 'user.preserve'
    PRESERVE_ATTR_VALUE = b'1'
    PRESERVE_COUNT = 5
    
    def __init__(self, base_path: Path, limits: StorageLimits,
                 on_cleanup: Callable[[list[Path]], None] | None = None):
        self._base_path = Path(base_path)
        self._limits = limits
        self._on_cleanup = on_cleanup
        self._lock = threading.Lock()
    
    def should_rotate(self, segment_path: Path) -> bool:
        """Check if segment should be rotated.
        
        Args:
            segment_path: Path to current segment directory
        
        Returns:
            True if segment exceeds size or duration limits
        """
        if not segment_path.exists():
            return False
        
        # Check size limit
        size_gb = self._get_dir_size(segment_path) / (1024**3)
        if size_gb >= self._limits.max_single_segment_gb:
            cloudlog.info(f"storage: Rotating segment {segment_path.name} "
                         f"(size {size_gb:.2f}GB >= {self._limits.max_single_segment_gb}GB)")
            return True
        
        # Check duration limit
        try:
            ctime = datetime.fromtimestamp(segment_path.stat().st_ctime)
            duration = datetime.now() - ctime
            if duration >= timedelta(minutes=self._limits.max_segment_duration_min):
                cloudlog.info(f"storage: Rotating segment {segment_path.name} "
                             f"(duration {duration} >= {self._limits.max_segment_duration_min}min)")
                return True
        except (OSError, ValueError):
            pass
        
        return False
    
    def check_free_space(self) -> bool:
        """Check if free space is above minimum.
        
        Returns:
            True if free space >= min_free_space_gb
        """
        try:
            free_gb = shutil.disk_usage(self._base_path).free / (1024**3)
            return free_gb >= self._limits.min_free_space_gb
        except Exception:
            # metric unavailable — let the legacy statvfs signal drive cleanup
            return True
    
    def get_free_space_gb(self) -> float:
        """Get available free space in GB."""
        try:
            return shutil.disk_usage(self._base_path).free / (1024**3)
        except Exception:
            return float('inf')  # metric unavailable — don't trigger on it
    
    def get_used_space_gb(self) -> float:
        """Get used space by log segments in GB."""
        try:
            segments = list(self._base_path.glob("*--*"))
            total_size = sum(self._get_dir_size(s) for s in segments)
            return total_size / (1024**3)
        except OSError:
            return 0.0
    
    def enforce_limits(self, force: bool = False) -> list[Path]:
        """Clean up old segments to enforce storage limits.

        Deletes oldest segments first (respecting preserve xattr)
        until total size is under limit and free space is adequate.

        Args:
            force: the caller has independently detected disk pressure
                (e.g. the legacy statvfs checks in deleter.py). Delete at
                least the oldest deletable segment even if this policy's
                own shutil.disk_usage metrics look fine — the two APIs can
                disagree, and ignoring the caller's signal would make the
                deleter spin forever without freeing anything.

        Returns:
            List of deleted segment paths
        """
        deleted = []

        with self._lock:
            segments = self._list_segments()

            # Calculate current usage
            total_size_gb = self.get_used_space_gb()
            free_gb = self.get_free_space_gb()

            cloudlog.debug(f"storage: {len(segments)} segments, "
                          f"{total_size_gb:.2f}GB used, {free_gb:.2f}GB free")

            # Delete oldest segments until under limits
            while segments and (force or
                               total_size_gb > self._limits.max_total_size_gb or
                               free_gb < self._limits.min_free_space_gb):
                force = False  # force guarantees at most one extra pass
                oldest = segments.pop(0)

                # Preserved segments sort to the end of the list, so they are
                # only reached (and deleted) once nothing else is left.
                # Skip if locked (still being written).
                if self._is_locked(oldest):
                    continue
                
                try:
                    size = self._get_dir_size(oldest)
                    shutil.rmtree(oldest)
                    total_size_gb -= size / (1024**3)
                    free_gb = self.get_free_space_gb()
                    deleted.append(oldest)
                    cloudlog.info(f"storage: Deleted {oldest.name}")
                except OSError as e:
                    cloudlog.warning(f"storage: Failed to delete {oldest}: {e}")
            
            # Delete by age
            if self._limits.max_age_days:
                cutoff = datetime.now() - timedelta(days=self._limits.max_age_days)
                for segment in segments:
                    if self._is_preserved(segment) or self._is_locked(segment):
                        continue
                    
                    try:
                        ctime = datetime.fromtimestamp(segment.stat().st_ctime)
                        if ctime < cutoff:
                            shutil.rmtree(segment)
                            deleted.append(segment)
                            cloudlog.info(f"storage: Deleted aged segment {segment.name}")
                    except OSError as e:
                        cloudlog.warning(f"storage: Failed to delete aged {segment}: {e}")
        
        if deleted and self._on_cleanup:
            self._on_cleanup(deleted)
        
        return deleted
    
    def get_stats(self) -> dict:
        """Get storage statistics.
        
        Returns:
            Dictionary with segment count, total size, free space
        """
        segments = list(self._base_path.glob("*--*"))
        total_size_gb = sum(self._get_dir_size(s) for s in segments) / (1024**3)
        free_gb = self.get_free_space_gb()
        
        return {
            "segment_count": len(segments),
            "total_size_gb": round(total_size_gb, 2),
            "free_space_gb": round(free_gb, 2),
            "max_size_gb": self._limits.max_total_size_gb,
            "max_age_days": self._limits.max_age_days,
        }
    
    DELETE_LAST = ('boot', 'crash')

    def _list_segments(self) -> list[Path]:
        """List segments in deletion order (upstream deleter semantics):
        regular segments by name (oldest route/segment first), then protected
        preserved segments, then boot/crash logs. Only the newest
        PRESERVE_COUNT preserved segments are protected (upstream
        get_preserved_segments); they stay deletable as a last resort instead
        of deadlocking with a full disk."""
        try:
            segments = [p for p in self._base_path.iterdir() if p.is_dir()]
            preserved = sorted((p for p in segments if self._is_preserved(p)), key=lambda p: p.name)
            protected = set(preserved[-self.PRESERVE_COUNT:])
            return sorted(segments, key=lambda p: (
                any(p.name.startswith(d) for d in self.DELETE_LAST),
                p in protected,
                p.name,
            ))
        except OSError:
            return []
    
    def _get_dir_size(self, path: Path) -> int:
        """Get total size of directory in bytes."""
        try:
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        except OSError:
            return 0
    
    def _is_preserved(self, path: Path) -> bool:
        """Check if segment is preserved via xattr."""
        try:
            return getxattr(path, self.PRESERVE_ATTR_NAME) == self.PRESERVE_ATTR_VALUE
        except Exception:
            return False
    
    def _is_locked(self, path: Path) -> bool:
        """Check if segment has active lock files."""
        try:
            return any(f.name.endswith(".lock") for f in path.iterdir())
        except OSError:
            return False
    
    def get_preserved_segments(self) -> list[Path]:
        """Get list of preserved segments."""
        return [s for s in self._list_segments() if self._is_preserved(s)]
