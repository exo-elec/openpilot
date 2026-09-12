"""A thin NetworkManager DBus client.

Port of the parts of wifi_manager.cc the WiFi panel actually uses. Kept
separate from the panel so the protocol can be exercised without a widget,
and so a machine with no NetworkManager (a dev PC, the CI container) gets a
panel that says so rather than a stack trace.

Everything is a blocking call with a short timeout. NetworkManager answers in
microseconds when it is there, and when it is not, the thing that must not
happen is the UI thread waiting on it -- so `available()` is checked once and
every call has a deadline.
"""

from __future__ import annotations

from dataclasses import dataclass

from openpilot.selfdrive.ui.eop.qt import (
  QDBusConnection,
  QDBusInterface,
)

# QDBusMessage.ErrorMessage. Hard-coded rather than imported because the two
# bindings disagree on whether the enum is scoped, and the wire value is
# fixed by the DBus specification.
_ERROR_MESSAGE = 3

SERVICE = "org.freedesktop.NetworkManager"
PATH = "/org/freedesktop/NetworkManager"
PATH_SETTINGS = "/org/freedesktop/NetworkManager/Settings"

IFACE = "org.freedesktop.NetworkManager"
IFACE_PROPERTIES = "org.freedesktop.DBus.Properties"
IFACE_SETTINGS = "org.freedesktop.NetworkManager.Settings"
IFACE_SETTINGS_CONNECTION = "org.freedesktop.NetworkManager.Settings.Connection"
IFACE_DEVICE = "org.freedesktop.NetworkManager.Device"
IFACE_DEVICE_WIRELESS = "org.freedesktop.NetworkManager.Device.Wireless"
IFACE_ACCESS_POINT = "org.freedesktop.NetworkManager.AccessPoint"

DEVICE_TYPE_WIFI = 2
DEVICE_STATE_ACTIVATED = 100
DEVICE_STATE_NEED_AUTH = 60

AP_FLAGS_PRIVACY = 0x1
AP_SEC_KEY_MGMT_PSK = 0x100
AP_SEC_KEY_MGMT_802_1X = 0x200

# Milliseconds. Short on purpose: this runs on the UI thread, and a hung
# NetworkManager must cost a dropped frame, not a frozen screen.
TIMEOUT_MS = 100


class Security:
  OPEN = "open"
  WPA = "wpa"
  ENTERPRISE = "enterprise"
  UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class AccessPoint:
  ssid: str
  strength: int            # 0..100
  security: str
  path: str = ""

  @property
  def needs_password(self) -> bool:
    return self.security == Security.WPA

  @property
  def connectable(self) -> bool:
    return self.security in (Security.OPEN, Security.WPA)


def security_of(flags: int, wpa_flags: int, rsn_flags: int) -> str:
  """Classify an AP's security from NM's three flag words.

  802.1X enterprise networks need a certificate flow this panel has no way to
  drive, and WEP is both unsupported by NM's simple path and not worth
  supporting. Both are surfaced as unjoinable rather than offered and then
  failing at connect time.
  """
  if not (flags & AP_FLAGS_PRIVACY) and wpa_flags == 0 and rsn_flags == 0:
    return Security.OPEN
  if (wpa_flags & AP_SEC_KEY_MGMT_802_1X) or (rsn_flags & AP_SEC_KEY_MGMT_802_1X):
    return Security.ENTERPRISE
  if (wpa_flags & AP_SEC_KEY_MGMT_PSK) or (rsn_flags & AP_SEC_KEY_MGMT_PSK):
    return Security.WPA
  return Security.UNSUPPORTED


def decode_ssid(raw) -> str:
  """NM gives the SSID as a byte array; it is conventionally UTF-8 but
  nothing enforces that, so an undecodable one is replaced rather than
  raising and taking the whole scan with it."""
  if isinstance(raw, str):
    return raw
  try:
    return bytes(raw).decode("utf-8", errors="replace")
  except (TypeError, ValueError):
    return ""


class NetworkManager:
  """Blocking DBus calls against NetworkManager, on the system bus."""

  def __init__(self, bus=None):
    self._bus = bus if bus is not None else QDBusConnection.systemBus()
    self._wifi_device: str | None = None

  # ---- plumbing ---------------------------------------------------------

  def _iface(self, path: str, interface: str) -> QDBusInterface:
    obj = QDBusInterface(SERVICE, path, interface, self._bus)
    obj.setTimeout(TIMEOUT_MS)
    return obj

  def _call(self, path: str, interface: str, method: str, *args):
    """Returns the reply value, or None on any DBus error.

    None rather than an exception because every caller's answer to a failure
    is the same -- show nothing -- and threading try/except through a scan
    loop would obscure the parts that matter.
    """
    obj = self._iface(path, interface)
    if not obj.isValid():
      return None
    reply = obj.call(method, *args)
    # PyQt5 exposes the enum unscoped and PySide6 as a Python enum whose
    # members compare unequal to ints, so the numeric value is compared
    # rather than the member -- ErrorMessage is 3 in both.
    if int(getattr(reply.type(), "value", reply.type())) == _ERROR_MESSAGE:
      return None
    args_out = reply.arguments()
    if not args_out:
      return None
    return args_out[0]

  def _get(self, path: str, interface: str, prop: str):
    return self._call(path, IFACE_PROPERTIES, "Get", interface, prop)

  def _get_all(self, path: str, interface: str) -> dict:
    value = self._call(path, IFACE_PROPERTIES, "GetAll", interface)
    return value if isinstance(value, dict) else {}

  def available(self) -> bool:
    """Whether NetworkManager is on the bus at all."""
    return self._bus.isConnected() and self._iface(PATH, IFACE).isValid()

  # ---- devices ----------------------------------------------------------

  def wifi_device(self) -> str | None:
    """Path of the first wireless device, cached after the first lookup."""
    if self._wifi_device is not None:
      return self._wifi_device
    devices = self._call(PATH, IFACE, "GetDevices")
    for device in devices or []:
      path = _path_str(device)
      if not path:
        continue
      if _as_int(self._get(path, IFACE_DEVICE, "DeviceType")) == DEVICE_TYPE_WIFI:
        self._wifi_device = path
        return path
    return None

  def device_state(self) -> int:
    device = self.wifi_device()
    if device is None:
      return 0
    return _as_int(self._get(device, IFACE_DEVICE, "State"))

  def connected(self) -> bool:
    return self.device_state() == DEVICE_STATE_ACTIVATED

  # ---- access points ----------------------------------------------------

  def request_scan(self) -> None:
    device = self.wifi_device()
    if device is not None:
      self._call(device, IFACE_DEVICE_WIRELESS, "RequestScan", {})

  def active_ssid(self) -> str:
    device = self.wifi_device()
    if device is None or self.device_state() != DEVICE_STATE_ACTIVATED:
      return ""
    ap = _path_str(self._get(device, IFACE_DEVICE_WIRELESS, "ActiveAccessPoint"))
    if not ap:
      return ""
    return decode_ssid(self._get(ap, IFACE_ACCESS_POINT, "Ssid"))

  def access_points(self) -> list[AccessPoint]:
    """Visible networks, strongest first, one entry per SSID.

    NM reports one AP per radio, so a mesh or a dual-band router shows up
    several times. They are collapsed to the strongest, which is what the
    driver means when they tap a name.
    """
    device = self.wifi_device()
    if device is None:
      return []
    paths = self._call(device, IFACE_DEVICE_WIRELESS, "GetAllAccessPoints") or []

    best: dict[str, AccessPoint] = {}
    for raw in paths:
      path = _path_str(raw)
      if not path:
        continue
      props = self._get_all(path, IFACE_ACCESS_POINT)
      ssid = decode_ssid(props.get("Ssid"))
      if not ssid:
        # A hidden network broadcasts an empty SSID. There is nothing useful
        # to show and nothing to tap.
        continue
      ap = AccessPoint(
        ssid=ssid,
        strength=_as_int(props.get("Strength")),
        security=security_of(_as_int(props.get("Flags")),
                             _as_int(props.get("WpaFlags")),
                             _as_int(props.get("RsnFlags"))),
        path=path,
      )
      if ssid not in best or ap.strength > best[ssid].strength:
        best[ssid] = ap

    return sorted(best.values(), key=lambda a: -a.strength)

  # ---- connections ------------------------------------------------------

  def known_connections(self) -> dict[str, str]:
    """SSID -> saved-connection path, for wifi connections only."""
    out: dict[str, str] = {}
    for raw in self._call(PATH_SETTINGS, IFACE_SETTINGS, "ListConnections") or []:
      path = _path_str(raw)
      if not path:
        continue
      settings = self._call(path, IFACE_SETTINGS_CONNECTION, "GetSettings")
      if not isinstance(settings, dict):
        continue
      if settings.get("connection", {}).get("type") != "802-11-wireless":
        continue
      ssid = decode_ssid(settings.get("802-11-wireless", {}).get("ssid"))
      if ssid:
        out[ssid] = path
    return out

  def connect(self, ap: AccessPoint, password: str = "") -> bool:
    """Join a network, saving it so it reconnects by itself next time."""
    device = self.wifi_device()
    if device is None or not ap.connectable:
      return False

    connection = {
      "connection": {"type": "802-11-wireless", "uuid": _uuid(), "id": ap.ssid},
      "802-11-wireless": {"ssid": ap.ssid.encode(), "mode": "infrastructure"},
    }
    if ap.needs_password:
      connection["802-11-wireless-security"] = {
        "key-mgmt": "wpa-psk", "auth-alg": "open", "psk": password,
      }
    reply = self._call(PATH, IFACE, "AddAndActivateConnection",
                       connection, _object_path(device), _object_path("/"))
    return reply is not None

  def forget(self, ssid: str) -> bool:
    path = self.known_connections().get(ssid)
    if path is None:
      return False
    obj = self._iface(path, IFACE_SETTINGS_CONNECTION)
    if not obj.isValid():
      return False
    obj.call("Delete")
    return True


def _as_int(value, default: int = 0) -> int:
  try:
    return int(value)
  except (TypeError, ValueError):
    return default


def _path_str(value) -> str:
  """Object paths come back as QDBusObjectPath from one binding and as a
  plain string from another, so both are accepted."""
  if value is None:
    return ""
  path = getattr(value, "path", None)
  if callable(path):
    return path()
  if isinstance(value, str):
    return value
  return str(value)


def _object_path(path: str):
  from openpilot.selfdrive.ui.eop.qt import QDBusObjectPath
  return QDBusObjectPath(path)


def _uuid() -> str:
  import uuid
  return str(uuid.uuid4())
