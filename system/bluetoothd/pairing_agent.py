#!/usr/bin/env python3
"""BlueZ D-Bus Pairing Agent — displays PIN code on ADAS UI.

Architecture:
  - Phone user opens Android Bluetooth settings
  - Taps "VisionPilot" or "OpenPilot" device to pair
  - BlueZ calls our agent's RequestPinCode / DisplayPinCode
  - We generate a 6-digit PIN, store it in Params
  - Qt/Python UI polls the param and shows the PIN on screen
  - User enters the PIN on their phone
  - BlueZ completes pairing
  - NavPilot app then connects to the already-paired device

One device pairs with exactly one phone at a time.
The device stores the last paired phone's BLE address and OAuth token.
"""
from __future__ import annotations

import random
import threading
import time
import logging
from collections.abc import Callable

try:
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    DBUS_AVAILABLE = True
except ImportError:
    dbus = None
    DBUS_AVAILABLE = False

if DBUS_AVAILABLE:
    dbus_method = dbus.service.method
else:
    # dbus.service.method() is evaluated at class-body execution time (it's a
    # decorator factory), so it must have a working no-op fallback even when
    # dbus-python isn't installed — PairingAgent.__init__ already raises in
    # that case, but the class body still needs to import cleanly.
    def dbus_method(*_args, **_kwargs):
        def decorator(func):
            return func
        return decorator

try:
    from openpilot.common.params import Params
except ImportError:
    Params = None

logger = logging.getLogger('bluetoothd.pairing_agent')

AGENT_PATH = '/org/bluez/eop_agent'
AGENT_INTERFACE = 'org.bluez.Agent1'
# DisplayOnly → SSP IO-capability matrix: initiator(KeyboardDisplay) × responder(DisplayOnly)
# resolves to Passkey Entry — device displays passkey, phone user must TYPE it.
# This is more secure than DisplayYesNo (numeric comparison = just tap OK).
CAPABILITY = 'DisplayOnly'


class PairingAgent(dbus.service.Object if DBUS_AVAILABLE else object):
    """BlueZ Agent1 implementation for PIN display pairing.

    When a phone initiates pairing, BlueZ asks our agent for a PIN
    or asks us to display a PIN. We generate a random code and
    store it so the ADAS UI can show it to the user.
    """

    def __init__(self, on_pin_displayed: Callable[[str], None] | None = None):
        if not DBUS_AVAILABLE:
            raise RuntimeError('dbus-python not available')

        bus = dbus.SystemBus()
        dbus.service.Object.__init__(self, bus, AGENT_PATH)
        self._on_pin_displayed = on_pin_displayed
        self._params = Params() if Params else None
        self._current_pin: str = ''
        self._lock = threading.Lock()

    @dbus_method(AGENT_INTERFACE, in_signature='o', out_signature='s')
    def RequestPinCode(self, device: dbus.ObjectPath) -> str:
        """Legacy pairing — we provide the PIN code.

        Generate a 6-digit PIN and return it to BlueZ.
        The same PIN is displayed on the ADAS UI.
        """
        pin = self._generate_pin()
        addr = self._device_address(device)
        logger.info(f'RequestPinCode for {addr} → PIN {pin}')
        self._store_pin(pin, addr)
        return pin

    @dbus_method(AGENT_INTERFACE, in_signature='ou', out_signature='')
    def DisplayPinCode(self, device: dbus.ObjectPath, pincode: int):
        """Legacy pairing — display PIN code (classic BT PIN pairing fallback)."""
        pin = str(pincode).zfill(6)
        addr = self._device_address(device)
        logger.info(f'DisplayPinCode for {addr} → PIN {pin}')
        self._store_pin(pin, addr)

    @dbus_method(AGENT_INTERFACE, in_signature='ouu', out_signature='')
    def DisplayPasskey(self, device: dbus.ObjectPath, passkey: int, entered: int):
        """SSP Passkey Entry — device shows passkey; phone user must TYPE it.

        Called by BlueZ during SSP when IO capabilities resolve to Passkey Entry
        (our DisplayOnly + phone's KeyboardDisplay → phone inputs, we display).
        Updated on each keystroke via `entered` count.
        """
        pin = f'{passkey:06d}'
        addr = self._device_address(device)
        logger.info(f'DisplayPasskey for {addr} → {pin} (entered={entered})')
        self._store_pin(pin, addr)

    @dbus_method(AGENT_INTERFACE, in_signature='ou', out_signature='')
    def RequestConfirmation(self, device: dbus.ObjectPath, passkey: int):
        """Numeric Comparison — reject to enforce Passkey Entry.

        With DisplayOnly on the device, this should not be called.
        If it is (e.g. phone is also DisplayOnly → Just Works), reject so
        pairing cannot complete without the user typing the code.
        """
        addr = self._device_address(device)
        logger.warning(f'RequestConfirmation for {addr} — rejecting to enforce Passkey Entry')
        raise dbus.exceptions.DBusException(
            'org.bluez.Error.Rejected',
            'Numeric comparison not permitted; use Passkey Entry',
        )

    @dbus_method(AGENT_INTERFACE, in_signature='os', out_signature='')
    def AuthorizeService(self, device: dbus.ObjectPath, uuid: str):
        """Authorize a specific service connection. Auto-accept."""
        addr = self._device_address(device)
        logger.info(f'AuthorizeService for {addr} UUID {uuid}')

    @dbus_method(AGENT_INTERFACE, in_signature='', out_signature='')
    def Cancel(self):
        """Pairing cancelled — clear the displayed PIN."""
        logger.info('Pairing cancelled')
        self._clear_pin()

    @dbus_method(AGENT_INTERFACE, in_signature='', out_signature='')
    def Release(self):
        """Agent released."""
        logger.info('Pairing agent released')

    def _generate_pin(self) -> str:
        """Generate a random 6-digit PIN."""
        return f'{random.randint(0, 999999):06d}'

    def _store_pin(self, pin: str, addr: str):
        """Store PIN in Params so the UI can display it."""
        with self._lock:
            self._current_pin = pin
        if self._params:
            self._params.put('BluetoothPairingPin', pin)
            self._params.put('BluetoothPairingAddr', addr)
            self._params.put('BluetoothPairingActive', '1')
        if self._on_pin_displayed:
            try:
                self._on_pin_displayed(pin)
            except Exception as e:
                logger.debug(f'PIN callback error: {e}')

    def _clear_pin(self):
        """Clear the displayed PIN."""
        with self._lock:
            self._current_pin = ''
        if self._params:
            self._params.put('BluetoothPairingPin', '')
            self._params.put('BluetoothPairingActive', '0')

    @staticmethod
    def _device_address(device_path: dbus.ObjectPath) -> str:
        """Extract MAC address from BlueZ device path."""
        return str(device_path).split('/')[-1].replace('_', ':')


def register_agent() -> PairingAgent | None:
    """Register the pairing agent with BlueZ.

    Returns the agent instance or None if dbus/bluez is unavailable.
    """
    if not DBUS_AVAILABLE:
        logger.warning('dbus-python not available — pairing agent disabled')
        return None

    try:
        bus = dbus.SystemBus()
        manager = dbus.Interface(
            bus.get_object('org.bluez', '/org/bluez'),
            'org.bluez.AgentManager1',
        )
        agent = PairingAgent()
        manager.RegisterAgent(AGENT_PATH, CAPABILITY)
        manager.RequestDefaultAgent(AGENT_PATH)
        logger.info(f'Pairing agent registered at {AGENT_PATH}')
        return agent
    except Exception:
        logger.exception('Failed to register pairing agent')
        return None


def unregister_agent(agent: PairingAgent | None):
    """Unregister the pairing agent from BlueZ."""
    if agent is None or not DBUS_AVAILABLE:
        return
    try:
        bus = dbus.SystemBus()
        manager = dbus.Interface(
            bus.get_object('org.bluez', '/org/bluez'),
            'org.bluez.AgentManager1',
        )
        manager.UnregisterAgent(AGENT_PATH)
        logger.info('Pairing agent unregistered')
    except Exception as e:
        logger.debug(f'Unregister agent error: {e}')


def get_displayed_pin() -> tuple[str, str]:
    """Get the currently displayed PIN and device address.

    Called by the ADAS UI to show the pairing code to the user.

    Returns:
        (pin_code, device_address) — empty strings if no active pairing.
    """
    if not Params:
        return '', ''
    params = Params()
    if params.get('BluetoothPairingActive') != b'1':
        return '', ''
    pin = (params.get('BluetoothPairingPin') or b'').decode()
    addr = (params.get('BluetoothPairingAddr') or b'').decode()
    return pin, addr


def main():
    """Run the pairing agent standalone for testing."""
    logging.basicConfig(level=logging.INFO)

    agent = register_agent()
    if not agent:
        return

    try:
        while True:
            pin, addr = get_displayed_pin()
            if pin:
                print(f'\n*** PAIRING: Enter {pin} on phone {addr} ***\n')
            time.sleep(2)
    except KeyboardInterrupt:
        unregister_agent(agent)


if __name__ == '__main__':
    main()
