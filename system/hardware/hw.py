import os
from pathlib import Path

from openpilot.system.hardware import PC

DEFAULT_DOWNLOAD_CACHE_ROOT = "/tmp/comma_download_cache"


def get_storage_root():
  """Get storage root directory (SD card or internal eMMC).

  For EOP platforms, checks common SD card mount points.
  """
  # Check common SD card mount points
  for sd_path in ["/media/sd", "/media/comma/sd", "/mnt/sd"]:
    if os.path.ismount(sd_path):
      return Path(sd_path)
  return None


class Paths:
  @staticmethod
  def comma_home() -> str:
    return os.path.join(str(Path.home()), ".comma" + os.environ.get("OPENPILOT_PREFIX", ""))

  @staticmethod
  def log_root() -> str:
    """Get log root directory (SD card or fallback to internal storage).

    Priority:
    1. LOG_ROOT environment variable
    2. SD card mount point (via storage manager)
    3. Internal storage fallback (/data/media/0/realdata/)
    """
    if 'LOG_ROOT' in os.environ:
      return os.environ['LOG_ROOT']
    if PC:
      return str(Path(Paths.comma_home()) / "media" / "0" / "realdata")

    # Use storage manager if available (SD card with fallback)
    storage_root = get_storage_root()
    if storage_root is not None:
      try:
        log_dir = storage_root / "realdata"
        log_dir.mkdir(parents=True, exist_ok=True)
        return str(log_dir)
      except Exception:
        pass  # Fall through to default

    return '/data/media/0/realdata/'

  @staticmethod
  def swaglog_root() -> str:
    """Get system log directory (always on internal storage for safety)."""
    if PC:
      return os.path.join(Paths.comma_home(), "log")
    return "/data/log/"

  @staticmethod
  def swaglog_ipc() -> str:
    return "ipc:///tmp/logmessage" + os.environ.get("OPENPILOT_PREFIX", "")

  @staticmethod
  def download_cache_root() -> str:
    if 'COMMA_CACHE' in os.environ:
      return os.environ['COMMA_CACHE'] + "/"
    return DEFAULT_DOWNLOAD_CACHE_ROOT + os.environ.get("OPENPILOT_PREFIX", "") + "/"

  @staticmethod
  def persist_root() -> str:
    if PC:
      return os.path.join(Paths.comma_home(), "persist")
    return "/persist/"

  @staticmethod
  def stats_root() -> str:
    """Get stats directory (SD card or fallback to internal storage)."""
    if PC:
      return str(Path(Paths.comma_home()) / "stats")
    storage_root = get_storage_root()
    if storage_root is not None:
      try:
        stats_dir = storage_root / "stats"
        stats_dir.mkdir(parents=True, exist_ok=True)
        return str(stats_dir)
      except Exception:
        pass
    return "/data/stats/"

  @staticmethod
  def config_root() -> str:
    return "/tmp/.comma"

  @staticmethod
  def eop_data_root() -> str:
    """Base for EOP persistent data (/data on boards; ~/.comma/data on PCs).

    Daemons must compose persistent paths from this instead of hardcoding
    /data so they run on dev PCs (calibration, OSM caches, dashcam, ...).
    """
    if PC:
      return os.path.join(Paths.comma_home(), "data")
    return "/data"

  @staticmethod
  def shm_path() -> str:
    return "/dev/shm"

  @staticmethod
  def external_record_root() -> str:
    """
    Preferred base directory for persistent recorder outputs (recordd).

    Priority:
    1. RECORD_ROOT or EOP_RECORD_ROOT environment variables
    2. SD card mount point (via storage manager) + "crashes"
    3. /media/<user>/eop_recorder fallback

    With storage manager, automatically uses SD card if available.
    """
    for env_var in ("RECORD_ROOT", "EOP_RECORD_ROOT"):
      if env_var in os.environ:
        return os.environ[env_var]

    # Use storage manager for SD card support
    storage_root = get_storage_root()
    if storage_root is not None:
      try:
        crash_dir = storage_root / "crashes"
        crash_dir.mkdir(parents=True, exist_ok=True)
        return str(crash_dir)
      except Exception:
        pass

    # Fallback to traditional path
    username = Path.home().name or "comma"
    return str(Path("/media") / username / "eop_recorder")
