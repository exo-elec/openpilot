#!/usr/bin/env python3
"""Hardware Watchdog Daemon for Rockchip SoCs.

Monitors system health via Linux watchdog interface (/dev/watchdog).
Automatically resets the system if the daemon fails to pet the watchdog.

Publishes:
  - wdgState: Current watchdog status and statistics

Safety:
  - System reset if daemon crashes or hangs
  - Configurable timeout (default 5 seconds)
  - Graceful disable on clean shutdown
"""
import fcntl
import os
import struct
import threading
import time
from dataclasses import dataclass

from cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity


# Linux watchdog ioctls
WDIOC_SETTIMEOUT = 0x80045706
WDIOC_GETTIMEOUT = 0x80045707
WDIOC_GETTIMELEFT = 0x8004570A
WDIOC_KEEPALIVE = 0x80045705
WDIOC_GETSTATUS = 0x80045701

# Magic close character (disables watchdog)
MAGIC_CLOSE = b'V'


@dataclass
class WdgConfig:
    """Watchdog configuration."""
    timeout_sec: float = 5.0
    device: str = "/dev/watchdog"
    pet_interval_sec: float = 2.0  # Pet at 2.5x safety margin


class WdgDaemon:
    """Hardware watchdog daemon.

    Uses Linux /dev/watchdog interface to monitor system health.
    Must pet periodically to prevent automatic system reset.

    Args:
        config: Watchdog configuration

    Example:
        >>> daemon = WdgDaemon()
        >>> daemon.run()  # Starts petting watchdog
        >>> # If daemon crashes, system resets after timeout
    """

    MIN_TIMEOUT = 1.0
    MAX_TIMEOUT = 60.0

    def __init__(self, config: WdgConfig | None = None):
        set_daemon_affinity("wdgd")

        self.config = config or WdgConfig()
        self._validate_config()

        self.pm = messaging.PubMaster(['wdgState'])

        # Watchdog state
        self._fd: int | None = None
        self._running = False
        self._pet_count = 0
        self._last_pet_time = 0.0
        self._start_time = 0.0

        # Threading
        self._lock = threading.Lock()
        self._pet_thread: threading.Thread | None = None

        cloudlog.info(f"wdgd: Initialized (timeout={self.config.timeout_sec}s)")

    def _validate_config(self):
        """Validate watchdog configuration."""
        if not self.MIN_TIMEOUT <= self.config.timeout_sec <= self.MAX_TIMEOUT:
            raise ValueError(
                f"Timeout must be {self.MIN_TIMEOUT}-{self.MAX_TIMEOUT}s, "
                + f"got {self.config.timeout_sec}"
            )

        # Pet interval should be less than timeout
        max_pet_interval = self.config.timeout_sec * 0.4
        if self.config.pet_interval_sec > max_pet_interval:
            self.config.pet_interval_sec = max_pet_interval
            cloudlog.warning(
                f"wdgd: Pet interval adjusted to {max_pet_interval:.1f}s"
            )

    def open(self) -> bool:
        """Open and enable watchdog.

        Returns:
            True if watchdog enabled successfully
        """
        try:
            # Open watchdog device
            self._fd = os.open(
                self.config.device,
                os.O_RDWR | os.O_CLOEXEC
            )

            # Set timeout
            timeout_int = int(self.config.timeout_sec)
            fcntl.ioctl(self._fd, WDIOC_SETTIMEOUT, timeout_int)

            # Verify actual timeout
            actual_timeout = fcntl.ioctl(self._fd, WDIOC_GETTIMEOUT, 0)

            self._start_time = time.monotonic()
            self._last_pet_time = self._start_time

            cloudlog.info(
                f"wdgd: Opened (requested={timeout_int}s, actual={actual_timeout}s)"
            )
            return True

        except OSError as e:
            cloudlog.error(f"wdgd: Failed to open: {e}")
            self._fd = None
            return False

    def close(self, graceful: bool = True) -> bool:
        """Close and optionally disable watchdog.

        Args:
            graceful: If True, disable watchdog before closing

        Returns:
            True if closed successfully
        """
        self._running = False

        # Stop petting thread
        if self._pet_thread and self._pet_thread.is_alive():
            self._pet_thread.join(timeout=1.0)

        if self._fd is not None:
            try:
                if graceful:
                    # Write magic character to disable
                    os.write(self._fd, MAGIC_CLOSE)
                    cloudlog.info("wdgd: Disabled via magic close")

                os.close(self._fd)
                self._fd = None
                return True

            except OSError as e:
                cloudlog.error(f"wdgd: Close error: {e}")
                return False

        return True

    def pet(self) -> bool:
        """Pet the watchdog manually.

        Writes to watchdog device to prevent reset.
        Normally called automatically by background thread.

        Returns:
            True if petted successfully
        """
        if self._fd is None:
            return False

        try:
            # Write any byte to pet
            os.write(self._fd, b'\x00')

            with self._lock:
                self._pet_count += 1
                self._last_pet_time = time.monotonic()

            return True

        except OSError as e:
            cloudlog.error(f"wdgd: Pet failed: {e}")
            return False

    def get_timeleft(self) -> float:
        """Get seconds remaining before watchdog expires.

        Returns:
            Seconds remaining, or -1 if unknown
        """
        if self._fd is None:
            return -1

        try:
            buf = struct.pack('i', 0)
            result = fcntl.ioctl(self._fd, WDIOC_GETTIMELEFT, buf)
            return float(struct.unpack('i', result)[0])
        except (OSError, struct.error):
            return -1

    def get_status(self) -> dict:
        """Get watchdog status."""
        with self._lock:
            uptime = time.monotonic() - self._start_time if self._start_time > 0 else 0

            return {
                'enabled': self._fd is not None,
                'running': self._running,
                'pet_count': self._pet_count,
                'uptime_sec': uptime,
                'timeout_sec': self.config.timeout_sec,
                'timeleft_sec': self.get_timeleft(),
            }

    def _pet_loop(self):
        """Background thread that pets watchdog."""
        cloudlog.info("wdgd: Petting thread started")

        while self._running:
            if not self.pet():
                cloudlog.error("wdgd: Pet failed in loop!")

            # Sleep until next pet
            time.sleep(self.config.pet_interval_sec)

        cloudlog.info("wdgd: Petting thread stopped")

    def _publish_state(self):
        """Publish watchdog state."""
        msg = messaging.new_message('wdgState', valid=True)
        ws = msg.wdgState

        status = self.get_status()
        ws.enabled = status['enabled']
        ws.running = status['running']
        ws.petCount = status['pet_count']
        ws.timeout = status['timeout_sec']
        ws.timeLeft = status['timeleft_sec']

        self.pm.send('wdgState', msg)

    def run(self):
        """Main daemon loop."""
        # Open watchdog
        if not self.open():
            cloudlog.error("wdgd: Failed to open, exiting")
            return

        self._running = True

        # Start petting thread
        self._pet_thread = threading.Thread(target=self._pet_loop, daemon=True)
        self._pet_thread.start()

        # Main loop at 1Hz for status publishing
        rk = Ratekeeper(1)
        cloudlog.info("wdgd: Running")

        while self._running:
            self._publish_state()
            rk.keep_time()

    def stop(self):
        """Stop daemon gracefully."""
        cloudlog.info("wdgd: Stopping...")
        self._running = False
        self.close(graceful=True)


def main():
    daemon = WdgDaemon()
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == "__main__":
    main()
