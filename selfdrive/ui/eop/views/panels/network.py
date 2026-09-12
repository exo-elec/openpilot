"""WiFi and Bluetooth panels.

Port of networking.cc and widgets/bluetooth.cc. Both talk to a system daemon
over DBus -- NetworkManager and BlueZ -- and both are written so that a
machine without that daemon shows a panel saying so rather than an empty list
that looks like a broken scan.

The WiFi panel is the one piece of settings a device genuinely cannot do
without: with no way to join a network there is no way to update, no way to
pair a phone, and no way to recover except over SSH.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.components.controls import ParamStore
from openpilot.selfdrive.ui.eop.components.dialogs import (
  confirm_dialog,
  text_input_dialog,
)
from openpilot.selfdrive.ui.eop.components.list_controls import (
  ButtonControl,
  LabelControl,
  ListWidget,
)
from openpilot.selfdrive.ui.eop.components.nm import NetworkManager, Security
from openpilot.selfdrive.ui.eop.qt import QTimer, QtWidgets

# Rescanning costs a radio sweep, which interrupts traffic on the link the
# device may already be using. Every 10 s is what the C++ settled on.
SCAN_INTERVAL_MS = 10_000

UNAVAILABLE_STYLE = "color: #A0A0A0; font-size: 24px; padding: 40px 50px;"

NO_NM = "NetworkManager is not running, so WiFi cannot be configured from here."
NO_BLUEZ = "BlueZ is not running, so Bluetooth cannot be configured from here."

SECURITY_LABEL = {
  Security.OPEN: "open",
  Security.WPA: "",
  Security.ENTERPRISE: "enterprise — not supported",
  Security.UNSUPPORTED: "unsupported security",
}


def strength_bars(strength: int) -> str:
  """Signal as four blocks. Text rather than an icon because it has to read
  at a glance from the driver's seat and scale with the font."""
  filled = min(4, max(0, (strength + 24) // 25))
  return "█" * filled + "░" * (4 - filled)


class WifiPanel(ListWidget):
  """Visible networks, strongest first. Tap to join, tap again to forget."""

  def __init__(self, manager: NetworkManager | None = None, parent=None):
    super().__init__(parent=parent)
    self._nm = manager if manager is not None else NetworkManager()
    self._available = self._nm.available()

    self.notice = QtWidgets.QLabel(NO_NM)
    self.notice.setWordWrap(True)
    self.notice.setStyleSheet(UNAVAILABLE_STYLE)
    self.notice.setVisible(not self._available)
    self.add_row(self.notice)

    self._rows: dict[str, ButtonControl] = {}
    self._scan_timer = QTimer(self)
    self._scan_timer.setInterval(SCAN_INTERVAL_MS)
    self._scan_timer.timeout.connect(self.refresh)

  # ---- lifecycle --------------------------------------------------------

  def showEvent(self, event):
    super().showEvent(event)
    if not self._available:
      # Re-check on entry: NetworkManager may have come up since the panel
      # was built, and the panel is built once and kept.
      self._available = self._nm.available()
      self.notice.setVisible(not self._available)
    if self._available:
      self._nm.request_scan()
      self.refresh()
      self._scan_timer.start()

  def hideEvent(self, event):
    super().hideEvent(event)
    # Scanning while the panel is not on screen is a radio sweep nobody sees.
    self._scan_timer.stop()

  # ---- listing ----------------------------------------------------------

  def refresh(self) -> None:
    if not self._available:
      return
    self._nm.request_scan()
    active = self._nm.active_ssid()
    known = self._nm.known_connections()

    seen = set()
    for ap in self._nm.access_points():
      seen.add(ap.ssid)
      row = self._rows.get(ap.ssid)
      if row is None:
        row = ButtonControl(ap.ssid, "CONNECT")
        self.add_row(row)
        self._rows[ap.ssid] = row
      self._style_row(row, ap, connected=ap.ssid == active,
                      saved=ap.ssid in known)

    # A network that has gone out of range is hidden rather than deleted, so
    # its row can come back without rebuilding the list and losing the
    # scroll position under the user's finger.
    for ssid, row in self._rows.items():
      row.setVisible(ssid in seen)
    self.update()

  def _style_row(self, row: ButtonControl, ap, connected: bool, saved: bool) -> None:
    parts = [strength_bars(ap.strength)]
    label = SECURITY_LABEL.get(ap.security, "")
    if label:
      parts.append(label)
    if connected:
      parts.append("connected")
    elif saved:
      parts.append("saved")
    row.set_value("  ·  ".join(parts))

    if connected or saved:
      row.set_button_text("FORGET")
      row.set_enabled(True)
    else:
      row.set_button_text("CONNECT")
      # An enterprise network needs a certificate flow this panel cannot
      # drive. Offering CONNECT and then failing is worse than saying no.
      row.set_enabled(ap.connectable)

    try:
      row.clicked.disconnect()
    except TypeError:
      # Nothing was connected yet. Both bindings raise rather than no-op.
      pass
    row.clicked.connect(lambda _=False, a=ap, f=connected or saved: self._on_row(a, f))

  # ---- actions ----------------------------------------------------------

  def _on_row(self, ap, forget: bool) -> None:
    if forget:
      if confirm_dialog(f"Forget {ap.ssid}?", "Forget", self):
        self._nm.forget(ap.ssid)
        self.refresh()
      return

    password = ""
    if ap.needs_password:
      password = text_input_dialog(f"Password for {ap.ssid}", secret=True, parent=self)
      if password is None:
        return
    self._nm.connect(ap, password)
    self.refresh()


class BluetoothPanel(ListWidget):
  """Pairing state for the NavPilot companion app.

  Pairing itself is driven by system/bluetoothd, which owns the BlueZ agent
  and the GATT server. This panel only opens and closes the pairing window
  and shows what bluetoothd publishes through Params -- duplicating the agent
  here would mean two processes answering the same BlueZ callbacks.
  """

  def __init__(self, store: ParamStore | None = None, parent=None):
    super().__init__(parent=parent)
    self._store = store if store is not None else ParamStore()

    self.device_name = LabelControl("Device Name", "")
    self.add_row(self.device_name)

    self.status = LabelControl("Status", "")
    self.add_row(self.status)

    self.pairing = ButtonControl(
      "Pairing", "START",
      "Opens a pairing window so the NavPilot app can connect. The PIN appears on the driving screen while the window is open.")
    self.pairing.clicked.connect(self._toggle_pairing)
    self.add_row(self.pairing)

    self.forget = ButtonControl("Paired Device", "FORGET")
    self.forget.clicked.connect(self._forget)
    self.add_row(self.forget)
    self.add_stretch()

    self._timer = QTimer(self)
    self._timer.setInterval(2000)
    self._timer.timeout.connect(self.refresh)

  def showEvent(self, event):
    super().showEvent(event)
    self.refresh()
    self._timer.start()

  def hideEvent(self, event):
    super().hideEvent(event)
    self._timer.stop()

  def refresh(self) -> None:
    self.device_name.set_value(self._store.get_text("EOPDeviceName") or "ExoPilot")

    pairing_open = self._store.get_text("BluetoothPairingActive") == "1"
    pin = self._store.get_text("BluetoothPairingPin")
    # GATT is checked alongside SPP: SPP is reserved for legacy OBD scanners,
    # and NavPilot's actual transport is BLE GATT. Looking only at SPP is why
    # the sidebar used to read "BLE OFF" with a phone plainly connected.
    gatt = self._store.get_bool("EOPNavPilotPaired")
    spp = bool(self._store.get_text("EOPSPPPairedDevice"))

    if pairing_open and pin:
      self.status.set_value(f"pairing — PIN {pin}")
    elif gatt or spp:
      self.status.set_value("connected")
    else:
      self.status.set_value("not paired")

    self.pairing.set_button_text("STOP" if pairing_open else "START")
    self.forget.set_value(self._store.get_text("EOPSPPPairedDevice") or
                          ("NavPilot" if gatt else "none"))
    self.forget.set_enabled(gatt or spp)
    self.update()

  def _toggle_pairing(self) -> None:
    now_open = self._store.get_text("BluetoothPairingActive") == "1"
    self._store.put_bool("BluetoothPairingActive", not now_open)
    self.refresh()

  def _forget(self) -> None:
    if not confirm_dialog("Forget the paired device?", "Forget", self):
      return
    self._store.put_text("EOPSPPPairedDevice", "")
    self._store.put_bool("EOPNavPilotPaired", False)
    self._store.put_text("BluetoothPairingAddr", "")
    self.refresh()
