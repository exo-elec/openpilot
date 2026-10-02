#!/usr/bin/env python3
"""
networkd - Network HAL for WiFi and Cellular

Manages network connectivity through standardized HAL interface:
- WiFi: Via NetworkManager D-Bus (supports RTL8822CE, AP6275P)
- Cellular: Quectel EC25 USB modem via ModemManager D-Bus

Publishes:
  - networkState: Connection status, IP, WiFi/cellular strength

Uses:
  - NetworkManager for WiFi
  - ModemManager for EC25 cellular (USB ECM/RNDIS/QMI)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from enum import IntEnum

try:
    from dbus_next.aio import MessageBus  # noqa: F401
    from dbus_next import BusType, Variant  # noqa: F401
    from dbus_next.errors import DBusError  # noqa: F401
    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False

from cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.params import Params
from openpilot.system.hardware import HARDWARE


class NetworkType(IntEnum):
    """Network connection type."""
    NONE = 0
    WIFI = 1
    CELLULAR = 2
    ETHERNET = 3


class NetworkState(IntEnum):
    """Network connection state."""
    DISCONNECTED = 0
    CONNECTING = 1
    CONNECTED = 2
    FAILED = 3


@dataclass
class WiFiInfo:
    """WiFi connection info."""
    enabled: bool = False
    connected: bool = False
    ssid: str = ""
    strength: int = 0  # 0-100
    ip: str = ""
    interface: str = "wlan0"


@dataclass
class CellularInfo:
    """Cellular connection info."""
    enabled: bool = False
    connected: bool = False
    apn: str = ""
    signal: int = 0  # 0-100
    ip: str = ""
    operator: str = ""
    technology: str = ""  # 4G, 5G, etc.


class NetworkHAL:
    """Hardware abstraction for network interfaces."""

    def __init__(self):
        self.wifi_chip = getattr(HARDWARE, 'WIFI_CHIP', 'UNKNOWN')
        self.wifi_interface = getattr(HARDWARE, 'WIFI_INTERFACE', 'wlan0')
        # Auto-detect cellular modem type and interface (EC25)
        self.cellular_modem = HARDWARE.get_modem_type()
        self.cellular_interface = HARDWARE.get_cellular_interface()

        # If EC25 not detected, try power-on sequence
        if self.cellular_modem == 'unknown' or not self._interface_exists(self.cellular_interface):
            cloudlog.info("networkd: EC25 not detected, attempting power-on sequence")
            HARDWARE.modem_power_on()

        cloudlog.info(f"networkd: WiFi chip: {self.wifi_chip}, interface: {self.wifi_interface}")
        cloudlog.info(f"networkd: Cellular modem: {self.cellular_modem}, interface: {self.cellular_interface}")

    @staticmethod
    def _interface_exists(iface: str) -> bool:
        import os
        return os.path.exists(f"/sys/class/net/{iface}")


class NetworkD:
    """Network HAL daemon."""

    # D-Bus constants for NetworkManager
    NM_BUS = "org.freedesktop.NetworkManager"
    NM_PATH = "/org/freedesktop/NetworkManager"
    NM_IFACE = "org.freedesktop.NetworkManager"
    NM_DEVICE_IFACE = "org.freedesktop.NetworkManager.Device"
    NM_WIRELESS_IFACE = "org.freedesktop.NetworkManager.Device.Wireless"
    NM_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

    def __init__(self):
        set_daemon_affinity("networkd")

        self.params = Params()
        self.pm = messaging.PubMaster(['networkState'])

        # HAL abstraction
        self.hal = NetworkHAL()

        # Network state
        self.wifi = WiFiInfo(interface=self.hal.wifi_interface)
        self.cellular = CellularInfo()
        self.primary_type = NetworkType.NONE

        # Cellular retry/backoff state to avoid hammering the modem when
        # registration is taking time or the network is temporarily unavailable.
        self._cellular_retry_backoff = 5.0
        self._last_cellular_retry = 0.0
        self._cellular_retry_streak = 0
        self._CELLULAR_RETRY_THRESHOLD = 3
        self._MAX_CELLULAR_RETRY_BACKOFF = 300.0

        # DNS/routing reachability cache
        self._last_internet_check = 0.0
        self._last_internet_result = False

        # D-Bus
        self.nm_bus = None
        self.mm_bus = None

        if DBUS_AVAILABLE:
            try:
                # Note: Full async D-Bus would require asyncio loop
                # For now, use subprocess-based monitoring
                pass
            except Exception as e:
                cloudlog.warning(f"networkd: D-Bus init failed: {e}")

        cloudlog.info("networkd: Initialized")

    @staticmethod
    def _at_command(port: str, cmd: str, read_size: int = 256, timeout: float = 0.5,
                    retries: int = 3) -> str:
        """Send an AT command and return the response, with retries.

        The EC25 AT port is shared with ModemManager and other system tools;
        retries absorb transient bus contention without spamming the logs.
        """
        last_exc = None
        for _ in range(retries):
            try:
                with open(port, 'w') as f:
                    f.write(f"{cmd}\r\n")
                time.sleep(0.1)
                with open(port) as f:
                    resp = f.read(read_size)
                if cmd.strip() in resp or ':' in resp or 'OK' in resp or 'ERROR' in resp:
                    return resp
            except OSError as e:
                last_exc = e
                time.sleep(0.05)
        if last_exc is not None:
            cloudlog.debug(f"networkd: AT command {cmd!r} failed: {last_exc}")
        return ""

    def _check_internet(self, timeout: float = 2.0) -> bool:
        """Check Layer-3/4 reachability without relying on DNS alone.

        Tries a TCP connect to a well-known anycast resolver first, then falls
        back to DNS resolution of a stable host. Caches the result for one
        update cycle to avoid probing on every 1Hz tick.
        """
        now = time.monotonic()
        if now - self._last_internet_check < 0.9 and self._last_internet_check > 0:
            return self._last_internet_result
        self._last_internet_check = now

        for host in [('1.1.1.1', 53), ('8.8.8.8', 53)]:
            try:
                with socket.create_connection(host, timeout=timeout):
                    self._last_internet_result = True
                    return True
            except (OSError, TimeoutError):
                continue

        try:
            socket.getaddrinfo('cloudflare.com', None, proto=socket.IPPROTO_TCP)
            self._last_internet_result = True
            return True
        except (OSError, TimeoutError):
            pass

        self._last_internet_result = False
        return False

    def _bring_up_cellular(self, iface: str) -> None:
        """Attempt to bring up a stalled cellular data connection.

        Uses NetworkManager when available, otherwise falls back to bringing the
        interface administratively up. Backoff increases exponentially up to a
        cap so a persistent modem/network fault does not flood the system.
        """
        now = time.monotonic()
        if now - self._last_cellular_retry < self._cellular_retry_backoff:
            return
        self._last_cellular_retry = now

        cloudlog.info(f"networkd: Attempting cellular reconnect (backoff={self._cellular_retry_backoff:.0f}s)")
        try:
            # Try to activate the first NM connection tied to this interface.
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'NAME,DEVICE,TYPE', 'connection', 'show'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    parts = line.split(':')
                    if len(parts) >= 3 and iface.startswith(parts[1]) and 'gsm' in parts[2].lower():
                        subprocess.run(
                            ['nmcli', 'connection', 'up', parts[0]],
                            capture_output=True, timeout=10
                        )
                        break
        except (subprocess.TimeoutExpired, OSError) as e:
            cloudlog.debug(f"networkd: nmcli reconnect failed: {e}")

        # NetworkManager-less fallback: make sure the interface is up.
        try:
            subprocess.run(
                ['ip', 'link', 'set', iface, 'up'],
                capture_output=True, timeout=5
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

        self._cellular_retry_backoff = min(
            self._cellular_retry_backoff * 2,
            self._MAX_CELLULAR_RETRY_BACKOFF
        )

    def _get_wifi_info_nmcli(self) -> tuple[bool, str, int, str]:
        """Get WiFi info using nmcli (works without D-Bus async).

        Returns: (connected, ssid, strength, ip)
        """
        try:
            # Check active connection
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'NAME,DEVICE,TYPE', 'connection', 'show', '--active'],
                capture_output=True, text=True, timeout=5
            )

            connected = False
            ssid = ""
            ip = ""

            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if ':' in line and 'wireless' in line.lower():
                        parts = line.split(':')
                        if len(parts) >= 2:
                            ssid = parts[0]
                            connected = True
                            break

            # Get signal strength
            strength = 0
            if connected:
                result = subprocess.run(
                    ['nmcli', '-t', '-f', 'ACTIVE,SIGNAL', 'device', 'wifi'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if line.startswith('yes:'):
                            try:
                                strength = int(line.split(':')[1])
                            except (ValueError, IndexError):
                                pass
                            break

            # Get IP
            if connected:
                result = subprocess.run(
                    ['hostname', '-I'],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0:
                    ips = result.stdout.strip().split()
                    # Filter for WLAN IP (usually 192.168.x.x or 10.x.x.x)
                    for addr in ips:
                        if not addr.startswith('127.'):
                            ip = addr
                            break

            return connected, ssid, strength, ip

        except (subprocess.TimeoutExpired, OSError) as e:
            cloudlog.debug(f"networkd: WiFi info error: {e}")

        return False, "", 0, ""

    def _get_cellular_info_direct(self) -> tuple[bool, str, int, str, str]:
        """Get EC25 cellular info directly without ModemManager.

        Checks usb0/wwan0 interface and uses AT commands for signal/operator.
        Returns: (connected, operator, signal, ip, technology)
        """
        iface = self.hal.cellular_interface
        if not os.path.exists(f"/sys/class/net/{iface}"):
            return False, "", 0, "", ""

        # Check IP assignment
        ip = ""
        try:
            result = subprocess.run(
                ['ip', '-4', 'addr', 'show', iface],
                capture_output=True, text=True, timeout=2
            )
            for line in result.stdout.split('\n'):
                if 'inet ' in line:
                    ip = line.split()[1].split('/')[0]
                    break
        except (subprocess.TimeoutExpired, OSError):
            pass

        connected = bool(ip)
        operator = ""
        signal = 0
        tech = "LTE"

        at_port = "/dev/ttyUSB2"
        if os.path.exists(at_port):
            resp = self._at_command(at_port, "AT+COPS?")
            for line in resp.split('\n'):
                if '+COPS:' in line:
                    parts = line.split('"')
                    if len(parts) >= 2:
                        operator = parts[1]
                    break

            resp = self._at_command(at_port, "AT+CSQ")
            for line in resp.split('\n'):
                if '+CSQ:' in line:
                    try:
                        rssi = int(line.split(':')[1].split(',')[0].strip())
                        if 0 <= rssi <= 31:
                            signal = int((rssi / 31.0) * 100)
                    except (ValueError, IndexError):
                        pass
                    break

            resp = self._at_command(at_port, "AT+QNWINFO")
            for line in resp.split('\n'):
                if '+QNWINFO:' in line:
                    if 'LTE' in line:
                        tech = "LTE"
                    elif 'WCDMA' in line or 'UMTS' in line:
                        tech = "3G"
                    elif 'GSM' in line:
                        tech = "2G"
                    break

        return connected, operator, signal, ip, tech

    def _get_cellular_info_mmcli(self) -> tuple[bool, str, int, str, str]:
        """Get cellular info using mmcli.

        Returns: (connected, operator, signal, ip, technology)
        """
        try:
            # List modems
            result = subprocess.run(
                ['mmcli', '-L'],
                capture_output=True, text=True, timeout=5
            )

            if result.returncode != 0 or '/org/freedesktop/ModemManager' not in result.stdout:
                return False, "", 0, "", ""

            # Parse modem index
            modem_idx = None
            for line in result.stdout.split('\n'):
                if '/org/freedesktop/ModemManager' in line:
                    # Extract number from path like /org/freedesktop/ModemManager1/Modem/0
                    parts = line.strip().split('/')
                    if parts:
                        try:
                            modem_idx = int(parts[-1])
                            break
                        except ValueError:
                            pass

            if modem_idx is None:
                return False, "", 0, "", ""

            # Get modem details
            result = subprocess.run(
                ['mmcli', '-m', str(modem_idx), '-J'],
                capture_output=True, text=True, timeout=5
            )

            if result.returncode != 0:
                return False, "", 0, "", ""

            try:
                modem_data = json.loads(result.stdout)
                modem = modem_data.get('modem', {})

                # Get state
                state = modem.get('generic', {}).get('state', '')
                connected = state == 'connected'

                # Get operator
                operator = modem.get('generic', {}).get('operator-name', '')

                # Get signal
                signal_data = modem.get('generic', {}).get('signal-quality', {})
                signal = signal_data.get('value', 0) if isinstance(signal_data, dict) else 0

                # Get technology
                tech = modem.get('generic', {}).get('access-technologies', [''])[0]

                # Get IP (from bearer)
                ip = ""
                bearers = modem.get('generic', {}).get('bearers', [])
                if bearers and connected:
                    # Get first bearer details
                    bearer_result = subprocess.run(
                        ['mmcli', '-b', str(bearers[0]), '-J'],
                        capture_output=True, text=True, timeout=5
                    )
                    if bearer_result.returncode == 0:
                        bearer_data = json.loads(bearer_result.stdout)
                        ip_config = bearer_data.get('bearer', {}).get('ipv4-config', {})
                        ip = ip_config.get('address', '')

                return connected, operator, signal, ip, tech

            except json.JSONDecodeError:
                pass

        except (subprocess.TimeoutExpired, OSError) as e:
            cloudlog.debug(f"networkd: Cellular info error: {e}")

        return False, "", 0, "", ""

    def _check_wifi_enabled(self) -> bool:
        """Check if WiFi is enabled."""
        try:
            result = subprocess.run(
                ['nmcli', 'radio', 'wifi'],
                capture_output=True, text=True, timeout=2
            )
            return result.returncode == 0 and 'enabled' in result.stdout.lower()
        except (subprocess.TimeoutExpired, OSError):
            return False

    def _check_cellular_enabled(self) -> bool:
        """Check if cellular modem is available.

        First checks for EC25 usb0/wwan0 interface directly (no ModemManager needed),
        then falls back to mmcli if available.
        """
        primary_iface = getattr(self.hal, 'cellular_interface', 'wwan0')
        for iface in (primary_iface, 'wwan0', 'usb0'):
            if os.path.exists(f"/sys/class/net/{iface}"):
                return True
        try:
            result = subprocess.run(
                ['lsusb'], capture_output=True, text=True, timeout=2
            )
            if '2c7c:' in result.stdout:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass
        try:
            result = subprocess.run(
                ['mmcli', '-L'],
                capture_output=True, text=True, timeout=2
            )
            return result.returncode == 0 and 'Modem' in result.stdout
        except (subprocess.TimeoutExpired, OSError):
            return False

    def update(self):
        """Main update loop."""
        # Get WiFi info
        self.wifi.enabled = self._check_wifi_enabled()
        if self.wifi.enabled:
            self.wifi.connected, self.wifi.ssid, self.wifi.strength, self.wifi.ip = \
                self._get_wifi_info_nmcli()

        # Get cellular info
        self.cellular.enabled = self._check_cellular_enabled()
        if self.cellular.enabled:
            # Prefer direct EC25 detection (no ModemManager needed)
            self.cellular.connected, self.cellular.operator, self.cellular.signal, \
                self.cellular.ip, self.cellular.technology = self._get_cellular_info_direct()
            # Fallback to mmcli if direct method didn't find connection details
            if not self.cellular.connected and not self.cellular.ip:
                mmcli_result = self._get_cellular_info_mmcli()
                if mmcli_result[0] or mmcli_result[3]:  # connected or has IP
                    self.cellular.connected, self.cellular.operator, self.cellular.signal, \
                        self.cellular.ip, self.cellular.technology = mmcli_result

            # Retry/backoff for enabled-but-disconnected cellular.
            if self.cellular.enabled and not self.cellular.connected:
                self._cellular_retry_streak += 1
                if self._cellular_retry_streak >= self._CELLULAR_RETRY_THRESHOLD:
                    self._bring_up_cellular(self.hal.cellular_interface)
            else:
                self._cellular_retry_streak = 0
                self._cellular_retry_backoff = 5.0

        # Determine primary connection
        if self.wifi.connected:
            self.primary_type = NetworkType.WIFI
        elif self.cellular.connected:
            self.primary_type = NetworkType.CELLULAR
        else:
            self.primary_type = NetworkType.NONE

        # Publish network state
        msg = messaging.new_message('networkState', valid=True)
        ns = msg.networkState

        # WiFi state
        ns.wifiEnabled = self.wifi.enabled
        ns.wifiConnected = self.wifi.connected
        ns.wifiSsid = self.wifi.ssid
        ns.wifiStrength = self.wifi.strength
        ns.wifiIp = self.wifi.ip

        # Cellular state
        ns.cellularEnabled = self.cellular.enabled
        ns.cellularConnected = self.cellular.connected
        ns.cellularOperator = self.cellular.operator
        ns.cellularSignal = self.cellular.signal
        ns.cellularIp = self.cellular.ip
        ns.cellularTechnology = self.cellular.technology

        # Primary connection
        ns.networkType = self.primary_type.name.lower()
        ns.hasInternet = (self.wifi.connected or self.cellular.connected) and self._check_internet()

        self.pm.send('networkState', msg)

    def run(self):
        """Main daemon loop."""
        rk = Ratekeeper(1)  # 1Hz
        cloudlog.info("networkd: Running")

        while True:
            self.update()
            rk.keep_time()


def main():
    daemon = NetworkD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
