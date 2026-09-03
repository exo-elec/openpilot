#!/usr/bin/env python3
"""BLE GATT central for ESP32-S3 corner radars — publishes cereal `radar2d`.

This module is the BLE CENTRAL counterpart to ble_gatt.py (which is the
PERIPHERAL for the NavPilot phone app). It connects to up to 4 ESP32-S3
corner radar nodes, each of which runs an on-node tracker and streams an
ARS408-style tracked-object list over GATT notifications. Cross-repo
reference: ESP32_RADAR/docs/ble-tracked-objects.md.

GATT CONTRACT (the ESP32 firmware stage-2 BLE server implements against this)
============================================================================
    Service:  7DC4D9A1-E436-4A4F-A191-E7361E7C5F23  (custom, EOP corner radar)
    Char:     D907C96A-E0DE-400F-A0A3-B665E72D120F  (notify only, one frame per
                                                     notification, up to 20 Hz)

Each notification carries ONE binary datagram, little-endian throughout:

    Header (8 bytes):
        u8  corner_id        0=FL, 1=FR, 2=RL, 3=RR, 0xFF=unresolved strap
        u8  count            0..12 object records following
        u16 seq              monotonic per-node frame counter, wraps at 65536
        u32 capture_time_us  esp_timer_get_time() at CFAR/track completion —
                             monotonic per node, NOT wall-clock, NOT synced
                             across corners

    Object record (16 bytes) × count:
        u32 track_id         stable across frames, no reuse within a boot
        i16 range_cm         range_m * 100
        i16 vel_mps_x100     vRel * 100, NEGATIVE = approaching
        i16 azimuth_deg_x10  azimuth * 10, 0=forward, +left
        i16 snr_db_x10       snr * 10
        u8  existence_prob   0..100 (percent)
        u8  flags            bit0=measured (fresh detection, not coasted),
                             bit1=is_static (stationary clutter),
                             bit2=ttc_valid (sender computes ttc_ms, so it is
                             authoritative INCLUDING 0 — see below)
        u16 ttc_ms           node-computed time-to-collision, ms. 0 = no closing
                             trajectory, 65535 = saturated (~65.5 s). Took over
                             the u16 that was `reserved`, so the record is still
                             16 bytes (4+2+2+2+2+1+1+2).
                             The NODE computes this because we cannot: the fields
                             above are polar position plus RADIAL Doppler, and
                             radial rate cannot separate an object converging on
                             us from one crossing harmlessly past. That needs the
                             Cartesian [vx,vy] the node's Kalman tracker holds
                             and does not transmit.
                             ttc_valid is what makes 0 actionable: set, 0 means
                             "node evaluated it, not closing" and we must NOT
                             substitute our own range/rate estimate (that is the
                             over-alarming estimate this field replaces); clear,
                             the sender predates TTC and we keep our estimate.

Max 12 objects per datagram → 8 + 12*16 = 200 bytes, fitting a 244-byte
BLE 5 ATT MTU with margin. Fixed-point conventions (cm, deg×10, x100
scalings) deliberately mirror ESP32_RADAR/00_common/wire_format so both
repositories share one house convention.

PAIRING: ESP32 corner nodes MUST use no-input-no-output (Just-Works) BLE
pairing — they have no display/keyboard. BlueZ handles Just-Works bonding
automatically under the existing DisplayOnly pairing agent; no PIN is ever
involved on this path.

PAIR SET (CornerPairTable): a node's BLE public address is its factory eFuse
BT MAC — unique per IC, stable across flashes/OTA — so it is the UNIT
identity, while the corner_id in each datagram (from the resistor strap) is
the POSITION identity. Every valid datagram teaches the table
{ble_address: corner_id}, persisted in the BLECornerPairs param (JSON) so the
mapping survives restarts — the BLE analogue of the WiFi fleet's MAC pairing
(hostapd macaddr_acl + exopilot's pair_corner_nodes.sh). Conflict semantics
(kept deliberately simple):
    - corner_id from the strap is AUTHORITATIVE: if a known address reports
      a different corner (unit physically moved, or strap re-read), a warning
      is logged and the table is updated — operation continues.
    - if two different addresses claim the SAME corner simultaneously, both
      stay connected with a warning logged; the per-corner liveness logic
      (CORNER_STALE_S) already arbitrates whose frames get published.

AUTHORIZATION (mirrors the WiFi fleet's hostapd MAC-ACL semantics — only
known units may feed openpilot). A sender is authorized if:
    - its address is already in the pair set, OR
    - the pair set is EMPTY (first-run bootstrap: with nothing paired yet,
      any unit may be learned — otherwise the fleet could never be
      commissioned), OR
    - BLERadarPairingOpen=1 (pairing window open).
Operator workflow — same ritual as exopilot's pair_corner_nodes.sh adding a
MAC to ap0.accept: set BLERadarPairingOpen=1 during calibration/pairing,
power the node, confirm `paired <addr> → corner <name>` in the log, then set
it back to 0. Enforcement is applied twice (defense in depth): the connect
sweep skips unauthorized peripherals, and the frame path drops their
datagrams before any corner state is touched.
NOTE: this is a host-side SOFTWARE check — BLE gating here is NOT
radio-level admission control. WiFi's MAC ACL (hostapd denies association)
remains the stronger boundary; a rogue BLE peripheral can still occupy the
link layer, it just never reaches radar2d.
BLERadarPairingOpen is deliberately NOT EOPBluetoothPairWindow — the latter
is the PHONE discoverable window in bluetoothd.py, a different concern.

CROSS-VEHICLE CONFUSION PROTECTION (parking-lot threat): a neighboring
identical fleet vehicle's corner nodes advertise the SAME service UUID. The
gate above protects us AFTER pairing; the gap is during bootstrap or an open
pairing window, when a NEIGHBOR's node could be learned. So learning a NEW
unit additionally requires these eligibility factors:
    1. DWELL (presence stability) — the candidate is continuously observed
       advertising for >= PAIR_DWELL_S; disappearing resets the timer.
       (TPMS auto-learn precedent: sustained presence before adoption.)
    2. IDENTITY (THE identity mechanism) — the node's BLE scan response
       carries manufacturer data [u16 LE company id 0x02E5 (Espressif)]
       [6-byte WiFi STA MAC]; the claimed MAC must be in OUR vehicle's WiFi
       roster (/etc/hostapd/ap0.accept, maintained by pair_corner_nodes.sh).
       A neighbor's node claims a MAC in the NEIGHBOR's roster, not ours.
    3. RSSI — ADVISORY ONLY, never a gate. BLE RSSI error is 1-10 m: a
       neighbor one parking slot over would pass any threshold, and an own
       node with an obstructed host antenna could fail one. RSSI is not
       distance. An eligible candidate with RSSI < PAIR_RSSI_DBM gets a
       rate-limited anomaly warning ("check antenna/placement") — the frame
       is NEVER held on RSSI alone.
If the roster is absent/unreadable (dev PC!) the identity factor is skipped
and eligibility degrades to dwell-only — ONE warning is logged at startup,
because without the roster cross-vehicle protection is much weaker
(presence-based only). An already-paired unit is trusted WITHOUT
re-qualification: it proved its identity when it was learned.

MSGQ SINGLE-PUBLISHER WARNING: msgq allows exactly ONE publisher per
service. When EOPBluetoothRadarEnabled is set, THIS module owns `radar2d`.
Any WiFi/UDP corner-radar daemon (visionpilot's radar_corner_node path)
MUST NOT publish `radar2d` at the same time or msgq will reject one of them.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time

try:
    import dbus
    import dbus.mainloop.glib
    from gi.repository import GLib
    DBUS_AVAILABLE = True
except ImportError:
    dbus = None
    GLib = None
    DBUS_AVAILABLE = False

try:
    import cereal.messaging as messaging
    from openpilot.common.params import Params
except ImportError:
    messaging = None
    Params = None

try:
    from hal.drivers.radar.radar2d import CORNER_UNKNOWN, decode_object_datagram
    HAL_RADAR2D_AVAILABLE = True
except ImportError:
    HAL_RADAR2D_AVAILABLE = False
    CORNER_UNKNOWN = 0xFF

    def decode_object_datagram(_data: bytes) -> dict | None:
        return None

logger = logging.getLogger('bluetoothd.ble_central')

# ── GATT contract UUIDs (see module docstring — cross-repo contract) ──────────
RADAR_SERVICE_UUID = '7dc4d9a1-e436-4a4f-a191-e7361e7c5f23'
RADAR_OBJECTS_CHAR_UUID = 'd907c96a-e0de-400f-a0a3-b665e72d120f'

# ── BlueZ / D-Bus interfaces ──────────────────────────────────────────────────
BLUEZ_SVC       = 'org.bluez'
ADAPTER_IFACE   = 'org.bluez.Adapter1'
DEVICE_IFACE    = 'org.bluez.Device1'
GATT_CHAR_IFACE = 'org.bluez.GattCharacteristic1'
PROPS_IFACE     = 'org.freedesktop.DBus.Properties'
OBJMGR_IFACE    = 'org.freedesktop.DBus.ObjectManager'
ADAPTER_PATH    = '/org/bluez/hci0'

# ── Wire format ─────────────────────────────────────────────────────────────
# Struct layout + decode_object_datagram() live in hal.drivers.radar.radar2d
# (pure wire decode, no D-Bus/Params — shared-hal ownership pattern, same as
# radar3d.py/radar4d.py in that package). CORNER_UNKNOWN imported above.

CORNER_NAMES = {0: 'FL', 1: 'FR', 2: 'RL', 3: 'RR'}

# BlueZ characteristic paths embed the sender's address:
# /org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF/service000c/char000f
_CHAR_PATH_ADDR_RE = re.compile(r'dev_((?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2})')

# ── Cross-vehicle learn eligibility (see docstring CROSS-VEHICLE section) ────
ESPRESSIF_COMPANY_ID = 0x02E5   # BLE mfg-data company id; nodes append their
                                # 6-byte WiFi STA MAC as the payload
WIFI_PAIR_ROSTER_PATH = '/etc/hostapd/ap0.accept'  # radio-level WiFi MAC ACL
PAIR_DWELL_S = 10.0    # candidate must be continuously observed this long —
                       # with the WiFi-roster identity check, this is the whole
                       # eligibility decision (constants, NOT params: tune in code)
PAIR_RSSI_DBM = -70    # ADVISORY anomaly threshold only — never gates learning:
                       # BLE RSSI error is 1-10 m, so RSSI is not distance

_MAC_RE = re.compile(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}')


def address_from_char_path(path: str | None) -> str | None:
    """Extract the sender BLE address ('AA:BB:CC:DD:EE:FF') from a BlueZ
    characteristic object path, or None if the path doesn't carry one."""
    if not path:
        return None
    m = _CHAR_PATH_ADDR_RE.search(str(path))
    return m.group(1).replace('_', ':').upper() if m else None


def parse_wifi_mac_from_mfg_data(mfg_data) -> str | None:
    """Extract the claimed WiFi STA MAC from BlueZ Device1 ManufacturerData
    (dict of u16 company id → bytes; BlueZ merges advertisement + scan
    response). ESP32 nodes broadcast their 6-byte WiFi STA MAC under
    ESPRESSIF_COMPANY_ID as cross-vehicle identity proof. Pure, D-Bus-free."""
    if not mfg_data:
        return None
    payload = None
    for key, value in mfg_data.items():
        if int(key) == ESPRESSIF_COMPANY_ID:
            payload = bytes(bytearray(value))
            break
    if payload is None or len(payload) < 6:
        return None
    return ':'.join(f'{b:02X}' for b in payload[:6])


def load_wifi_roster(path: str = WIFI_PAIR_ROSTER_PATH) -> set[str] | None:
    """Load our vehicle's paired WiFi MAC roster — one MAC per line, '#'
    comments and blank lines tolerated. Returns None when the file is absent
    or unreadable (dev PC!): the caller logs ONE warning and degrades to
    dwell+RSSI-only eligibility (weaker, proximity-based protection)."""
    try:
        roster: set[str] = set()
        with open(path) as f:
            for line in f:
                line = line.split('#', 1)[0].strip()
                if not line:
                    continue
                mac = line.split()[0].upper()  # tolerate trailing tokens
                if _MAC_RE.fullmatch(mac):
                    roster.add(mac)
        return roster
    except OSError:
        return None


def check_learn_eligibility(candidate: dict | None, roster: set[str] | None,
                            now: float) -> tuple[bool, str]:
    """Eligibility for LEARNING a new unit (cross-vehicle protection — module
    docstring): dwell >= PAIR_DWELL_S AND claimed WiFi MAC in the roster
    (identity factor skipped in degraded no-roster mode). RSSI is NOT part of
    the decision — it is advisory-only (BLE RSSI error is 1-10 m; RSSI is not
    distance); the caller emits an anomaly warning for weak-but-eligible
    candidates. Returns (eligible, reason)."""
    if candidate is None:
        return False, 'not yet observed advertising'
    dwell = now - candidate['first_seen']
    if dwell < PAIR_DWELL_S:
        return False, f'dwell {dwell:.0f}s < {PAIR_DWELL_S:.0f}s'
    if roster is not None and candidate.get('wifi_mac') not in roster:
        return False, f"claimed WiFi MAC {candidate.get('wifi_mac')} not in roster"
    return True, 'ok'

# ESP32 corner IDs (wire): 0=FL, 1=FR, 2=RL, 3=RR
# Radar2DReturn.side (cereal custom.capnp): 0=LF, 1=LR, 2=RF, 3=RR
# The two enums number the four corners DIFFERENTLY (ESP32 groups fronts
# first, cereal groups lefts first) — this table is the ONLY place the
# translation happens. Do not "simplify" it away.
CORNER_TO_SIDE = {
    0: 0,  # FL → LF
    2: 1,  # RL → LR
    1: 2,  # FR → RF
    3: 3,  # RR → RR
}

PUBLISH_HZ = 20.0               # matches cereal services.py radar2d rate
CORNER_STALE_S = 0.5            # corner with no frame this long drops out of returns
RECONNECT_SCAN_S = 5.0          # discovery/connect sweep period
BACKOFF_INITIAL_S = 1.0         # per-node reconnect backoff…
BACKOFF_MAX_S = 60.0            # …capped here (doubling)

PAIRING_OPEN_PARAM = 'BLERadarPairingOpen'  # pairing window — NOT the phone's
                                            # EOPBluetoothPairWindow (docstring)
PAIRING_OPEN_TTL_S = 2.0        # param re-read interval — toggles take effect
                                # without restarting bluetoothd
UNAUTH_LOG_INTERVAL_S = 60.0    # rate-limit unauthorized warnings per address


def corner_to_side(corner_id: int) -> int | None:
    """Translate ESP32 corner ID (0=FL,1=FR,2=RL,3=RR) to Radar2DReturn.side
    (0=LF,1=LR,2=RF,3=RR). Returns None for unknown/strap-unresolved IDs."""
    return CORNER_TO_SIDE.get(corner_id)


# ── Persistent pair set (BLE address → corner_id) ────────────────────────────

class CornerPairTable:
    """Learns and persists which BLE address serves which corner.

    Pure python (no D-Bus) so it is unit-testable on a dev PC. Conflict
    semantics are documented in the module docstring's PAIR SET section:
    strap corner_id is authoritative (update + warn on change); duplicate
    corner claims warn but keep both addresses.
    """

    PARAM_KEY = 'BLECornerPairs'

    def __init__(self, params=None):
        self._params = params
        self._table: dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        if self._params is None:
            return {}
        raw = self._params.get(self.PARAM_KEY)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            return {str(a).upper(): int(c) for a, c in data.items()
                    if int(c) in CORNER_NAMES}
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning('BLE central: corrupt %s param (%s) — starting with empty pair set',
                           self.PARAM_KEY, e)
            return {}

    def _save(self) -> None:
        """Persist the table — only called when it actually changed."""
        if self._params is None:
            return
        try:
            self._params.put(self.PARAM_KEY, json.dumps(self._table, sort_keys=True))
        except Exception as e:
            logger.warning('BLE central: could not save %s: %s', self.PARAM_KEY, e)

    def learn(self, address: str | None, corner_id: int) -> None:
        """Record address → corner_id from a valid datagram. No-op (and no
        param write) when the mapping is already known."""
        if not address or corner_id not in CORNER_NAMES:
            return
        address = address.upper()
        known = self._table.get(address)
        if known == corner_id:
            return
        if known is None:
            logger.info('BLE central: paired %s → corner %s', address, CORNER_NAMES[corner_id])
        else:
            # strap is authoritative — unit moved or strap re-read; update and continue
            logger.warning('BLE central: %s was corner %s, now reports %s — updating pair set',
                           address, CORNER_NAMES[known], CORNER_NAMES[corner_id])
        for other, c in self._table.items():
            if c == corner_id and other != address:
                logger.warning('BLE central: %s also claims corner %s — both kept, ' +
                               'liveness arbitrates', other, CORNER_NAMES[corner_id])
        self._table[address] = corner_id
        self._save()

    def as_dict(self) -> dict[str, int]:
        return dict(self._table)

    def is_paired(self, address: str | None) -> bool:
        """True if this address is already a known/paired unit."""
        return bool(address) and address.upper() in self._table

    def is_allowed(self, address: str | None, pairing_open: bool = False) -> bool:
        """THE authorization predicate — all admission decisions go through here.
        Allowed if the address is paired, OR the pair set is empty (first-run
        bootstrap), OR the operator opened the pairing window. An address we
        cannot identify at all is never allowed."""
        if not address:
            return False
        if address.upper() in self._table:
            return True
        if not self._table:
            return True  # bootstrap — see module docstring AUTHORIZATION
        return pairing_open

    def describe(self) -> str:
        """One-line 'AA:BB…→FL, …' summary for startup logging."""
        if not self._table:
            return '(empty)'
        return ', '.join(f'{a}→{CORNER_NAMES.get(c, c)}'
                         for a, c in sorted(self._table.items()))


class BLECentral:
    """BLE central for up to 4 ESP32-S3 corner radar peripherals.

    Lifecycle mirrors GATTD: __init__ (reads params) → setup(bus) → start()
    → stop(). No-ops entirely when the EOPBluetoothRadarEnabled param is off.
    """

    def __init__(self, params: Params | None = None):
        self.params = params or (Params() if Params else None)
        self.enabled = bool(self.params and self.params.get_bool('EOPBluetoothRadarEnabled'))

        self._bus = None
        self._running = False
        self._pm = None  # created in start() — never a publisher while disabled

        # corner_id → {'objects': [...], 'last_rx': monotonic, 'seq': int}
        self._corners: dict[int, dict] = {}
        self._lock = threading.Lock()

        # device path → {'connected': bool, 'backoff_s': float, 'next_try': monotonic}
        self._devices: dict[str, dict] = {}
        self._signals_subscribed = False

        # Persistent BLE address → corner pair set (loaded once at startup)
        self._pairs = CornerPairTable(self.params)
        logger.info('BLE central: pair set: %s', self._pairs.describe())

        # Pairing-window param cache + rate-limit state for unauthorized warnings
        self._pairing_open_cache: tuple[bool, float] = (False, 0.0)  # (value, monotonic expiry)
        self._unauth_log: dict[str, float] = {}  # key → last warning monotonic

        # Cross-vehicle learn eligibility: WiFi roster (None = degraded mode,
        # dwell-only — logged once here, not per candidate) + per-address
        # advertising observation state: {'first_seen','last_seen','rssi','wifi_mac'}
        self._wifi_roster = load_wifi_roster()
        if self._wifi_roster is None:
            logger.warning('BLE central: %s unreadable — cross-vehicle protection ' +
                           'degraded to dwell-only (no identity check)',
                           WIFI_PAIR_ROSTER_PATH)
        self._candidates: dict[str, dict] = {}

        self._has_objects_field: bool | None = None  # detected on first publish

    # ── Authorization (paired / bootstrap / pairing window — see docstring) ───

    def _pairing_open(self) -> bool:
        """BLERadarPairingOpen, re-read at most every PAIRING_OPEN_TTL_S so
        toggling it takes effect without restarting bluetoothd."""
        value, expiry = self._pairing_open_cache
        now = time.monotonic()
        if now >= expiry:
            value = bool(self.params and self.params.get_bool(PAIRING_OPEN_PARAM))
            self._pairing_open_cache = (value, now + PAIRING_OPEN_TTL_S)
        return value

    def _is_authorized(self, address: str | None) -> bool:
        return self._pairs.is_allowed(address, self._pairing_open())

    def _log_limited(self, key: str, message: str, *args) -> None:
        """Rate-limited warning — once per key per UNAUTH_LOG_INTERVAL_S,
        not on every 5 s sweep / 20 Hz frame."""
        now = time.monotonic()
        if now - self._unauth_log.get(key, -UNAUTH_LOG_INTERVAL_S) >= UNAUTH_LOG_INTERVAL_S:
            self._unauth_log[key] = now
            logger.warning(message, *args)

    def _warn_unauthorized(self, address: str | None, where: str) -> None:
        self._log_limited(f'unauth:{address or "?"}',
                          'BLE central: unauthorized unit %s %s — not paired and ' +
                          'pairing window closed (%s=0)',
                          address or '?', where, PAIRING_OPEN_PARAM)

    def _update_candidates(self, observed: dict[str, dict], now: float) -> None:
        """Track advertising dwell per address (discovery path). Disappearing
        resets the dwell timer — the entry is dropped entirely."""
        for gone in set(self._candidates) - set(observed):
            del self._candidates[gone]
        for address, info in observed.items():
            cand = self._candidates.get(address)
            if cand is None:
                cand = self._candidates[address] = {'first_seen': now}
            cand['last_seen'] = now
            cand['rssi'] = info.get('rssi')
            cand['wifi_mac'] = info.get('wifi_mac')

    def _learn_eligible(self, address: str | None) -> tuple[bool, str]:
        """Eligibility gate for LEARNING a new unit — dwell + WiFi-roster
        identity (RSSI advisory only). Paired units never come through here."""
        key = (address or '?').upper()
        cand = self._candidates.get(key)
        ok, reason = check_learn_eligibility(cand, self._wifi_roster, time.monotonic())
        if ok:
            # Advisory anomaly: eligible by dwell+roster but weak signal —
            # likely obstructed host antenna or bad node placement. Rate-
            # limited, and NEVER holds the frame (RSSI is not distance).
            rssi = cand.get('rssi')
            if rssi is not None and rssi < PAIR_RSSI_DBM:
                self._log_limited(f'rssi:{key}',
                                  'BLE central: %s eligible but weak signal ' +
                                  '(RSSI %d < %d dBm) — check antenna/placement',
                                  key, rssi, PAIR_RSSI_DBM)
        return ok, reason

    # ── Pairing service-tool surface (navpilot RADAR_PAIR_STATUS) ─────────────
    # D-Bus-free accessors — ncp_session builds its status payload from these.

    def pairing_window_open(self) -> bool:
        """Current pairing-window state (TTL-cached param read)."""
        return self._pairing_open()

    def pairs_dump(self) -> list[dict]:
        """Pair table as [{'address', 'corner'}] — corner = ESP32 id
        0=FL,1=FR,2=RL,3=RR. Sorted for a stable wire image."""
        return [{'address': a, 'corner': c}
                for a, c in sorted(self._pairs.as_dict().items())]

    def candidates_dump(self, now: float | None = None) -> list[dict]:
        """Discovery-stage candidates as [{'address', 'wifiMac', 'corner',
        'dwellS', 'eligible', 'reason'}]. corner is None until the node
        connects and its datagrams identify it (i.e. it has been learned)."""
        now = time.monotonic() if now is None else now
        pairs = self._pairs.as_dict()
        out = []
        for address in sorted(self._candidates):
            cand = self._candidates[address]
            eligible, reason = check_learn_eligibility(cand, self._wifi_roster, now)
            out.append({
                'address': address,
                'wifiMac': cand.get('wifi_mac'),
                'corner': pairs.get(address),
                'dwellS': round(now - cand['first_seen'], 1),
                'eligible': eligible,
                'reason': reason,
            })
        return out

    # ── D-Bus / BlueZ setup ───────────────────────────────────────────────────

    def setup(self, bus) -> bool:
        """Subscribe to BlueZ signals and kick off the connect loop."""
        if not DBUS_AVAILABLE:
            logger.warning('dbus-python / gi not available — BLE central disabled')
            return False
        try:
            self._bus = bus
            bus.add_signal_receiver(
                self._on_properties_changed,
                signal_name='PropertiesChanged',
                dbus_interface=PROPS_IFACE,
                path_keyword='path',
            )
            self._signals_subscribed = True
            logger.info('BLE central: subscribed to BlueZ property signals')
            return True
        except Exception:
            logger.exception('BLE central setup failed')
            return False

    # ── Connect / discovery loop ──────────────────────────────────────────────

    def _find_radar_devices(self) -> dict[str, dict]:
        """BlueZ objects for peripherals advertising our service UUID.
        Returns device_path → {'proxy', 'address', 'connected'}."""
        found: dict[str, dict] = {}
        try:
            mgr = dbus.Interface(
                self._bus.get_object(BLUEZ_SVC, '/'), OBJMGR_IFACE)
            objects = mgr.GetManagedObjects()
            for path, ifaces in objects.items():
                dev = ifaces.get(DEVICE_IFACE)
                if dev is None:
                    continue
                uuids = [str(u).lower() for u in dev.get('UUIDs', [])]
                if RADAR_SERVICE_UUID not in uuids:
                    continue
                found[str(path)] = {
                    'proxy': self._bus.get_object(BLUEZ_SVC, path),
                    'address': str(dev.get('Address', '?')),
                    'connected': bool(dev.get('Connected', False)),
                    # Learn-eligibility evidence (cross-vehicle protection):
                    'rssi': int(dev['RSSI']) if 'RSSI' in dev else None,
                    'wifi_mac': parse_wifi_mac_from_mfg_data(dev.get('ManufacturerData', {})),
                }
        except Exception as e:
            logger.warning('BLE central: device enumeration failed: %s', e)
        return found

    def _start_discovery(self) -> None:
        try:
            adapter = dbus.Interface(
                self._bus.get_object(BLUEZ_SVC, ADAPTER_PATH), ADAPTER_IFACE)
            adapter.SetDiscoveryFilter(dbus.Dictionary({
                'UUIDs': dbus.Array([RADAR_SERVICE_UUID], signature='s'),
                'Transport': dbus.String('le'),
            }, signature='sv'))
            adapter.StartDiscovery()
        except Exception as e:
            logger.debug('BLE central: StartDiscovery: %s', e)

    def _connect_device(self, path: str, proxy) -> None:
        """Connect one peripheral and subscribe to its objects characteristic."""
        dev = dbus.Interface(proxy, DEVICE_IFACE)
        dev.Connect()
        logger.info('BLE central: connected %s', path)

        # Wait briefly for GATT service resolution
        props = dbus.Interface(proxy, PROPS_IFACE)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if bool(props.Get(DEVICE_IFACE, 'ServicesResolved')):
                break
            time.sleep(0.1)
        else:
            logger.warning('BLE central: %s services never resolved', path)
            return

        # Locate the notify characteristic and subscribe
        mgr = dbus.Interface(self._bus.get_object(BLUEZ_SVC, '/'), OBJMGR_IFACE)
        for cpath, ifaces in mgr.GetManagedObjects().items():
            char = ifaces.get(GATT_CHAR_IFACE)
            if char is None or str(char.get('UUID', '')).lower() != RADAR_OBJECTS_CHAR_UUID:
                continue
            if not str(cpath).startswith(path):
                continue
            char_proxy = self._bus.get_object(BLUEZ_SVC, cpath)
            dbus.Interface(char_proxy, GATT_CHAR_IFACE).StartNotify()
            with self._lock:
                self._devices[path]['connected'] = True
                self._devices[path]['backoff_s'] = BACKOFF_INITIAL_S
            logger.info('BLE central: subscribed to %s', cpath)
            return
        logger.warning('BLE central: %s has no objects characteristic', path)

    def _connect_sweep(self) -> bool:
        """GLib timeout: discover radar nodes, (re)connect with per-node backoff."""
        if not self._running:
            return False
        try:
            self._start_discovery()
            devices = self._find_radar_devices()
            # Feed dwell tracking (learn eligibility) before any gating
            self._update_candidates(
                {d['address'].upper(): d for d in devices.values()}, time.monotonic())
            for path, dev in devices.items():
                if not self._is_authorized(dev['address']):
                    self._warn_unauthorized(dev['address'], '(connect skipped)')
                    continue
                with self._lock:
                    state = self._devices.setdefault(path, {
                        'connected': False,
                        'backoff_s': BACKOFF_INITIAL_S,
                        'next_try': 0.0,
                    })
                if dev['connected'] and state['connected']:
                    continue
                now = time.monotonic()
                if now < state['next_try']:
                    continue
                try:
                    self._connect_device(path, dev['proxy'])
                except Exception as e:
                    with self._lock:
                        state['backoff_s'] = min(state['backoff_s'] * 2, BACKOFF_MAX_S)
                        state['next_try'] = now + state['backoff_s']
                        state['connected'] = False
                    logger.warning('BLE central: connect %s failed (%s) — retry in %.0fs',
                                   dev['address'], e, state['backoff_s'])
        except Exception:
            logger.exception('BLE central: sweep error')
        return True  # keep the GLib timeout alive

    # ── Notification receive path ─────────────────────────────────────────────

    def _on_properties_changed(self, interface, changed, _invalidated, path=None):
        if interface != GATT_CHAR_IFACE or 'Value' not in changed:
            return
        try:
            frame = decode_object_datagram(bytes(bytearray(changed['Value'])))
        except Exception as e:
            logger.debug('BLE central: decode error: %s', e)
            return
        if frame is None:
            logger.debug('BLE central: malformed datagram from %s', path)
            return
        corner = frame['corner_id']
        if corner_to_side(corner) is None:
            logger.debug('BLE central: datagram with unknown corner_id 0x%02X', corner)
            return
        # Authorization gate (defense in depth with the connect sweep): drop
        # frames from unpaired units before any corner state is touched.
        address = address_from_char_path(path)
        if self._pairs.is_paired(address):
            pass  # paired unit — trusted without re-qualification (it proved
                  # its identity when learned); connect path unchanged
        elif self._pairs.is_allowed(address, self._pairing_open()):
            # bootstrap / open-window LEARN path: cross-vehicle eligibility
            # (dwell + RSSI + WiFi-roster identity) also required
            ok, reason = self._learn_eligible(address)
            if not ok:
                self._log_limited(f'elig:{(address or "?").upper()}',
                                  'BLE central: %s not learn-eligible (%s) — frame held',
                                  address or '?', reason)
                return
        else:
            self._warn_unauthorized(address, '(frame dropped)')
            return
        # Learn sender → corner (unit MAC ↔ strap position) for the pair set
        self._pairs.learn(address, corner)
        with self._lock:
            state = self._corners.setdefault(corner, {'objects': [], 'last_rx': 0.0, 'seq': -1})
            state['objects'] = frame['objects']
            state['last_rx'] = time.monotonic()
            state['seq'] = frame['seq']

    # ── radar2d publish ───────────────────────────────────────────────────────

    def _fill_objects(self, r2d, entries: list[tuple[int, dict]]) -> None:
        """Fill the extended objects list. The `objects` field is being added
        to custom.Radar2D in parallel — detect it once and degrade silently to
        legacy returns-only publishing until that schema lands."""
        if self._has_objects_field is False:
            return
        try:
            objects = r2d.init('objects', len(entries))
            self._has_objects_field = True
        except AttributeError:
            self._has_objects_field = False
            logger.warning('BLE central: custom.Radar2D has no objects field yet — ' +
                           'publishing legacy returns only until schema lands')
            return
        for i, (corner, obj) in enumerate(entries):
            objects[i].trackId = obj['trackId']
            objects[i].corner = corner
            objects[i].rangM = obj['rangM']
            objects[i].azimuthDeg = obj['azimuthDeg']
            objects[i].vRel = obj['vRel']
            objects[i].aRel = math.nan          # not carried on the BLE datagram
            objects[i].snrDb = obj['snrDb']
            objects[i].existenceProb = obj['existenceProb']
            objects[i].measured = obj['measured']
            # ARS-style dynProp: 0=stationary, 1=moving — derived from is_static
            objects[i].dynProp = 0 if obj['isStatic'] else 1
            objects[i].lengthM = 0.0            # unknown — not carried on the datagram
            objects[i].widthM = 0.0
            # Node-computed TTC (the node is the only side that can — see the
            # ttcS comment in custom.capnp). NaN when the node reports no
            # closing trajectory, and also when it predates the field: it
            # zero-fills what was a reserved u16 and the hal decoder maps that
            # zero to NaN rather than to 0.0 s, which would read as an imminent
            # collision on every legacy frame. .get() additionally tolerates an
            # older hal decoder that does not emit the key at all.
            objects[i].ttcS = obj.get('ttcS', math.nan)
            objects[i].ttcValid = bool(obj.get('ttcValid', False))

    def _publish(self) -> bool:
        """GLib timeout: publish merged radar2d at PUBLISH_HZ from latest frames."""
        if not self._running:
            return False
        now = time.monotonic()
        with self._lock:
            live = {c: s for c, s in self._corners.items()
                    if now - s['last_rx'] <= CORNER_STALE_S}

        msg = messaging.new_message('radar2d')
        r2d = msg.radar2d

        # Extended objects list (parallel schema work — see _fill_objects)
        entries = [(c, o) for c in sorted(live) for o in live[c]['objects']]
        self._fill_objects(r2d, entries)

        # Legacy returns: one per cereal side, derived from live corners.
        # present = any measured object from that corner;
        # vRel = strongest approaching (most negative vRel) measured object.
        returns = r2d.init('returns', len(CORNER_TO_SIDE))
        for corner, side in sorted(CORNER_TO_SIDE.items(), key=lambda kv: kv[1]):
            ret = returns[side]
            ret.side = side
            measured = [o for o in live.get(corner, {}).get('objects', []) if o['measured']]
            ret.present = bool(measured)
            approaching = [o['vRel'] for o in measured if o['vRel'] < 0.0]
            ret.vRel = min(approaching) if approaching else math.nan
        self._pm.send('radar2d', msg)
        return True

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self.enabled:
            logger.info('BLE central: disabled (EOPBluetoothRadarEnabled off)')
            return
        if messaging is None:
            logger.error('BLE central: cereal.messaging not available — disabled')
            return
        if not DBUS_AVAILABLE or self._bus is None:
            logger.warning('BLE central: no D-Bus — disabled')
            return
        # Single radar2d publisher for this process — see module docstring warning
        self._pm = messaging.PubMaster(['radar2d'])
        self._running = True
        GLib.timeout_add(int(1000 / PUBLISH_HZ), self._publish)
        GLib.timeout_add(int(RECONNECT_SCAN_S * 1000), self._connect_sweep)
        GLib.idle_add(self._connect_sweep)  # first sweep immediately
        logger.info('BLE central started (%.0f Hz publish, %.0f s rescan)',
                    PUBLISH_HZ, RECONNECT_SCAN_S)

    def stop(self) -> None:
        self._running = False  # GLib timeouts self-terminate on False return
        if self._bus and self._signals_subscribed:
            try:
                adapter = dbus.Interface(
                    self._bus.get_object(BLUEZ_SVC, ADAPTER_PATH), ADAPTER_IFACE)
                adapter.StopDiscovery()
            except Exception:
                pass
        logger.info('BLE central stopped')
