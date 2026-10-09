"""OpenPilot Bluetooth Daemon.

Provides SPP (mobile telemetry) services for companion app connectivity.
"""

from openpilot.system.bluetoothd.bluetoothd import BluetoothD
from openpilot.system.bluetoothd import protocol, device, spp, ncp_session

__all__ = ['BluetoothD', 'protocol', 'device', 'spp', 'ncp_session']
