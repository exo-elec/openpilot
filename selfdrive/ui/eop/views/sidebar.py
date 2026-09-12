"""The classic 260px sidebar.

A direct port of sidebar.cc: settings and home/flag buttons at the top, five
signal dots and the network name, then four status cards -- TEMP, PANDA, WIFI,
BLE. Everything is painted rather than laid out with widgets, exactly as the
C++ did, because the cards are a fixed grid of rounded rects with a coloured
tab down the left edge and that is simply easier to draw than to style.

The one structural change is where the state comes from. sidebar.cc pulled it
out of a SubMaster itself and cached Bluetooth pairing behind a ParamWatcher
because a Params read per 20 Hz tick is a disk read per tick. Here the message
half arrives as a Snapshot from state.py, and the Bluetooth half is read on
the same decimated schedule for the same reason -- see BLE_POLL_MS.
"""

from __future__ import annotations

from dataclasses import dataclass

from openpilot.selfdrive.ui.eop.components.theme import (
  DANGER,
  GOOD,
  INACTIVE,
  SIDEBAR_BG,
  SIDEBAR_W,
  WARNING,
  WHITE,
  inter,
)
from openpilot.selfdrive.ui.eop.qt import (
  QBrush,
  QColor,
  QFont,
  QPainter,
  QPen,
  QRect,
  Qt,
  QTimer,
  QWidget,
  Signal,
)
from openpilot.selfdrive.ui.eop.state import Snapshot

# Card geometry, straight from sidebar.cc drawMetric().
CARD_X, CARD_W, CARD_H = 15, 230, 85
CARD_YS = (180, 265, 350, 435)      # TEMP, PANDA, WIFI, BLE
CARD_RADIUS = 14
TAB_RADIUS = 12

SETTINGS_BTN = QRect(50, 35, 200, 117)
HOME_BTN = QRect(60, 860, 180, 180)

# Bluetooth pairing state lives in Params, not in a message. sidebar.cc used a
# QFileSystemWatcher to avoid reading it every tick; a 2 s poll costs the same
# in practice and does not need a watcher per key.
BLE_POLL_MS = 2000

BLE_KEYS = ("BluetoothPairingPin", "BluetoothPairingActive",
            "EOPSPPPairedDevice", "EOPNavPilotPaired")


@dataclass(frozen=True)
class Card:
  """One status card: two lines of text and the colour of its edge tab."""
  top: str
  bottom: str
  color: QColor


def temp_card(thermal_status: str) -> Card:
  if thermal_status == "green":
    return Card("TEMP", "GOOD", GOOD)
  if thermal_status == "yellow":
    return Card("TEMP", "OK", WARNING)
  return Card("TEMP", "HIGH", DANGER)


def panda_card(connected: bool) -> Card:
  return Card("PANDA", "ON", GOOD) if connected else Card("PANDA", "OFF", DANGER)


def network_card(network_type: str, strength: int) -> Card:
  """WIFI/ETH/cell, with signal quality where the link type has one.

  sidebar.cc fell back to enumerating QNetworkInterface when deviceState
  reported NONE, because thermald does not run on a dev PC and the sidebar
  would otherwise claim the network was down while the machine was plainly
  online. That fallback is kept -- it is the difference between a useful
  sidebar and a misleading one whenever the daemon is not up.
  """
  if network_type == "wifi":
    if strength >= 3:
      return Card("WIFI", "GOOD", GOOD)
    if strength >= 1:
      return Card("WIFI", "WEAK", WARNING)
    return Card("WIFI", "LOW", WARNING)
  if network_type == "ethernet":
    return Card("ETH", "OK", GOOD)
  if network_type not in ("none", ""):
    return Card(network_type.upper()[:5], "ON", GOOD)

  wifi_up, eth_up = _interfaces_up()
  if wifi_up:
    return Card("WIFI", "ON", GOOD)
  if eth_up:
    return Card("ETH", "OK", GOOD)
  return Card("WIFI", "OFF", INACTIVE)


def _interfaces_up() -> tuple[bool, bool]:
  """(wifi, ethernet) from the kernel rather than from deviceState.

  Reads /sys rather than going through QtNetwork: QNetworkInterface lives in
  the QtNetwork module, which is a separate import for one boolean, and the
  operstate files say the same thing.
  """
  from pathlib import Path
  wifi = eth = False
  net = Path("/sys/class/net")
  if not net.is_dir():
    return False, False
  for iface in net.iterdir():
    state = iface / "operstate"
    try:
      if state.read_text().strip() != "up":
        continue
    except OSError:
      continue
    name = iface.name
    if name.startswith("wl"):
      wifi = True
    elif name.startswith(("eth", "en")):
      eth = True
  return wifi, eth


def ble_card(pairing_active: bool, pairing_pin: str,
             gatt_paired: bool, spp_paired: bool) -> Card:
  """BLE GATT is checked alongside classic SPP.

  The bug this encodes: the sidebar used to look only at EOPSPPPairedDevice,
  but SPP is reserved for legacy OBD scanners. NavPilot's actual companion-app
  transport is BLE GATT (docs/eop/04_Integration/BLE_DESIGN.md), so the card
  read "BLE OFF" for the whole time a phone was connected over the transport
  the product is built around.
  """
  if pairing_active and pairing_pin:
    return Card("BLE", "PAIRING", WARNING)
  if gatt_paired or spp_paired:
    return Card("BLE", "ON", GOOD)
  return Card("BLE", "OFF", INACTIVE)


class Sidebar(QWidget):
  """Fixed-width status strip down the left of the offroad screen."""

  settings_clicked = Signal()
  home_clicked = Signal()

  def __init__(self, store=None, parent=None):
    super().__init__(parent)
    self.setObjectName("sidebar")
    self.setFixedWidth(SIDEBAR_W)
    self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    self._store = store
    self._onroad = False
    self._net_type = "none"
    self._net_strength = 0
    self._cards = (
      temp_card("green"), panda_card(False),
      network_card("none", 0), ble_card(False, "", False, False),
    )
    self._pressed: str | None = None

    self._ble = dict.fromkeys(BLE_KEYS, "")
    self._ble_timer = QTimer(self)
    self._ble_timer.setInterval(BLE_POLL_MS)
    self._ble_timer.timeout.connect(self._refresh_ble)
    self._refresh_ble()

  # ---- state ------------------------------------------------------------

  def set_snapshot(self, snap: Snapshot) -> None:
    self._onroad = snap.started
    self._net_type = snap.network_type
    # deviceState reports 0..4; the C++ drew strength+1 dots so that "any
    # signal at all" lights two rather than one, and none lights none.
    self._net_strength = snap.network_strength + 1 if snap.network_strength > 0 else 0
    self._cards = (
      temp_card(snap.thermal_status),
      panda_card(snap.panda_connected),
      network_card(snap.network_type, snap.network_strength),
      ble_card(self._ble["BluetoothPairingActive"] == "1",
               self._ble["BluetoothPairingPin"],
               self._ble["EOPNavPilotPaired"] == "1",
               bool(self._ble["EOPSPPPairedDevice"])),
    )
    self.update()

  def _refresh_ble(self) -> None:
    if self._store is None:
      return
    for key in BLE_KEYS:
      self._ble[key] = self._store.get_text(key)

  def showEvent(self, event):
    super().showEvent(event)
    self._refresh_ble()
    self._ble_timer.start()

  def hideEvent(self, event):
    super().hideEvent(event)
    self._ble_timer.stop()

  # ---- input ------------------------------------------------------------

  def mousePressEvent(self, event):
    pos = event.pos()
    if SETTINGS_BTN.contains(pos):
      self._pressed = "settings"
    elif self._onroad and HOME_BTN.contains(pos):
      self._pressed = "home"
    else:
      self._pressed = None
    if self._pressed:
      self.update()

  def mouseReleaseEvent(self, event):
    was, self._pressed = self._pressed, None
    self.update()
    pos = event.pos()
    if was == "settings" and SETTINGS_BTN.contains(pos):
      self.settings_clicked.emit()
    elif was == "home" and HOME_BTN.contains(pos):
      self.home_clicked.emit()

  # ---- painting ---------------------------------------------------------

  def paintEvent(self, event):
    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(self.rect(), SIDEBAR_BG)

    self._draw_buttons(p)
    self._draw_network(p)
    for card, y in zip(self._cards, CARD_YS, strict=True):
      self._draw_card(p, card, y)

  def _draw_buttons(self, p: QPainter) -> None:
    # The C++ drew PNG assets here. Drawing them means shipping and loading
    # pixmaps for two shapes; they are drawn instead, which also keeps them
    # sharp if the panel ever changes scale.
    p.setPen(Qt.NoPen)
    p.setOpacity(0.65 if self._pressed == "settings" else 1.0)
    p.setBrush(QBrush(QColor(0x29, 0x29, 0x29)))
    p.drawRoundedRect(SETTINGS_BTN, 12, 12)
    p.setOpacity(1.0)
    p.setPen(WHITE)
    p.setFont(inter(26, QFont.DemiBold))
    p.drawText(SETTINGS_BTN, Qt.AlignCenter, "SETTINGS")

  def _draw_network(self, p: QPainter) -> None:
    x = 35
    p.setPen(Qt.NoPen)
    for i in range(5):
      p.setBrush(WHITE if i < self._net_strength else INACTIVE)
      p.drawEllipse(x, 115, 16, 16)
      x += 22

    p.setFont(inter(20))
    p.setPen(WHITE)
    label = "Hotspot" if self._net_type == "hotspot" else self._net_type
    p.drawText(QRect(35, 150, self.width() - 60, 30),
               Qt.AlignLeft | Qt.AlignVCenter, label)

  def _draw_card(self, p: QPainter, card: Card, y: int) -> None:
    rect = QRect(CARD_X, y, CARD_W, CARD_H)

    # Coloured tab: a rounded rect drawn wider than the clip so only its left
    # end shows, which is how the C++ got a rounded left edge and a square
    # right one without a painter path.
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(card.color))
    p.setClipRect(rect.x() + 4, rect.y(), 18, rect.height(), Qt.ReplaceClip)
    p.drawRoundedRect(QRect(rect.x() + 3, rect.y() + 3, 70, 80),
                      TAB_RADIUS, TAB_RADIUS)
    p.setClipping(False)

    pen = QPen(QColor(0xff, 0xff, 0xff, 0x55))
    pen.setWidth(2)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)

    p.setPen(WHITE)
    p.setFont(inter(22, QFont.DemiBold))
    p.drawText(rect.adjusted(22, 0, 0, 0), Qt.AlignCenter,
               f"{card.top}\n{card.bottom}")
