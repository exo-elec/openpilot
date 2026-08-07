#!/usr/bin/env python3
"""Tests for BLE central object-datagram codec, corner→side mapping, and the
persistent corner pair set.

Pure-python tests — no D-Bus / BlueZ needed (mirrors the DBUS_AVAILABLE
guard in ble_central.py).
"""
import logging
import struct
import time

from openpilot.system.bluetoothd.ble_central import (
    HEADER_STRUCT, OBJECT_STRUCT, MAX_OBJECTS_PER_DATAGRAM, GATT_CHAR_IFACE,
    ESPRESSIF_COMPANY_ID, PAIR_DWELL_S, PAIR_RSSI_DBM,
    decode_object_datagram, corner_to_side,
    address_from_char_path, CornerPairTable, BLECentral,
    parse_wifi_mac_from_mfg_data, load_wifi_roster, check_learn_eligibility,
)


class FakeParams:
    """In-memory stand-in for openpilot.common.params.Params (get/put only)."""

    def __init__(self, store: dict | None = None):
        self.store = dict(store or {})

    def get(self, key, **_kwargs):
        v = self.store.get(key)
        return v.encode() if v else None

    def get_bool(self, key, **_kwargs):
        return self.store.get(key) in ('1', 1, True)

    def put(self, key, dat):
        self.store[key] = dat


def encode_datagram(corner_id: int, seq: int, capture_time_us: int,
                    objects: list[dict]) -> bytes:
    """Test-side encoder mirroring the ESP32 wire format (see ble_central docstring)."""
    data = HEADER_STRUCT.pack(corner_id, len(objects), seq, capture_time_us)
    for o in objects:
        flags = (0x01 if o.get('measured', False) else 0) | (0x02 if o.get('isStatic', False) else 0)
        data += OBJECT_STRUCT.pack(
            o['trackId'],
            int(round(o['rangM'] * 100)),
            int(round(o['vRel'] * 100)),
            int(round(o['azimuthDeg'] * 10)),
            int(round(o['snrDb'] * 10)),
            int(round(o['existenceProb'])),
            flags,
            0,  # reserved
        )
    return data


class TestDecodeObjectDatagram:
    """Round-trip encode→decode of the radar object datagram."""

    def test_round_trip_single_object(self):
        objs = [{
            'trackId': 42, 'rangM': 12.34, 'vRel': -3.21, 'azimuthDeg': 25.5,
            'snrDb': 18.7, 'existenceProb': 95.0, 'measured': True, 'isStatic': False,
        }]
        frame = decode_object_datagram(encode_datagram(0, 7, 123456, objs))
        assert frame is not None
        assert frame['corner_id'] == 0
        assert frame['count'] == 1
        assert frame['seq'] == 7
        assert frame['capture_time_us'] == 123456
        obj = frame['objects'][0]
        assert obj['trackId'] == 42
        assert obj['rangM'] == 12.34
        assert obj['vRel'] == -3.21
        assert obj['azimuthDeg'] == 25.5
        assert obj['snrDb'] == 18.7
        assert obj['existenceProb'] == 95.0
        assert obj['measured'] is True
        assert obj['isStatic'] is False

    def test_round_trip_max_objects(self):
        objs = [{
            'trackId': 1000 + i, 'rangM': 1.0 + i, 'vRel': 0.0, 'azimuthDeg': -45.0,
            'snrDb': 10.0, 'existenceProb': 50.0, 'measured': i % 2 == 0,
            'isStatic': i % 2 == 1,
        } for i in range(MAX_OBJECTS_PER_DATAGRAM)]
        raw = encode_datagram(3, 65535, 0, objs)
        assert len(raw) == HEADER_STRUCT.size + MAX_OBJECTS_PER_DATAGRAM * OBJECT_STRUCT.size
        frame = decode_object_datagram(raw)
        assert frame is not None
        assert frame['corner_id'] == 3
        assert frame['count'] == MAX_OBJECTS_PER_DATAGRAM
        assert frame['seq'] == 65535
        assert [o['trackId'] for o in frame['objects']] == [1000 + i for i in range(MAX_OBJECTS_PER_DATAGRAM)]
        assert frame['objects'][0]['measured'] is True
        assert frame['objects'][1]['isStatic'] is True

    def test_empty_frame(self):
        frame = decode_object_datagram(encode_datagram(2, 0, 42, []))
        assert frame is not None
        assert frame['count'] == 0
        assert frame['objects'] == []

    def test_flags_bit_packing(self):
        objs = [{'trackId': 1, 'rangM': 1.0, 'vRel': 0.0, 'azimuthDeg': 0.0,
                 'snrDb': 0.0, 'existenceProb': 0.0, 'measured': True, 'isStatic': True}]
        frame = decode_object_datagram(encode_datagram(1, 1, 1, objs))
        assert frame['objects'][0]['measured'] is True
        assert frame['objects'][0]['isStatic'] is True

    def test_i16_saturation_values(self):
        objs = [{'trackId': 0xFFFFFFFF, 'rangM': 327.67, 'vRel': -327.68,
                 'azimuthDeg': -3276.8, 'snrDb': 3276.7, 'existenceProb': 100.0,
                 'measured': False, 'isStatic': False}]
        frame = decode_object_datagram(encode_datagram(0, 0, 0, objs))
        obj = frame['objects'][0]
        assert obj['trackId'] == 0xFFFFFFFF
        assert obj['rangM'] == 327.67
        assert obj['vRel'] == -327.68


class TestMalformedDatagrams:
    """Malformed-length rejection."""

    def test_too_short(self):
        assert decode_object_datagram(b'') is None
        assert decode_object_datagram(b'\x00' * (HEADER_STRUCT.size - 1)) is None

    def test_count_exceeds_max(self):
        raw = HEADER_STRUCT.pack(0, MAX_OBJECTS_PER_DATAGRAM + 1, 0, 0)
        raw += b'\x00' * OBJECT_STRUCT.size * (MAX_OBJECTS_PER_DATAGRAM + 1)
        assert decode_object_datagram(raw) is None

    def test_length_count_mismatch_short(self):
        # count=2 but only 1 record present
        objs = [{'trackId': 1, 'rangM': 1.0, 'vRel': 0.0, 'azimuthDeg': 0.0,
                 'snrDb': 0.0, 'existenceProb': 0.0}]
        raw = bytearray(encode_datagram(0, 0, 0, objs))
        raw[1] = 2
        assert decode_object_datagram(bytes(raw)) is None

    def test_length_count_mismatch_long(self):
        # count=0 but trailing bytes present
        raw = encode_datagram(0, 0, 0, []) + b'\xAA\xBB'
        assert decode_object_datagram(raw) is None

    def test_truncated_record(self):
        objs = [{'trackId': 1, 'rangM': 1.0, 'vRel': 0.0, 'azimuthDeg': 0.0,
                 'snrDb': 0.0, 'existenceProb': 0.0}]
        raw = encode_datagram(0, 0, 0, objs)[:-1]
        assert decode_object_datagram(raw) is None


class TestCornerToSide:
    """ESP32 corner ID (0=FL,1=FR,2=RL,3=RR) → Radar2DReturn.side (0=LF,1=LR,2=RF,3=RR)."""

    def test_front_left(self):
        assert corner_to_side(0) == 0  # FL → LF

    def test_rear_left(self):
        assert corner_to_side(2) == 1  # RL → LR

    def test_front_right(self):
        assert corner_to_side(1) == 2  # FR → RF

    def test_rear_right(self):
        assert corner_to_side(3) == 3  # RR → RR

    def test_unknown_corner(self):
        assert corner_to_side(0xFF) is None
        assert corner_to_side(4) is None

    def test_bijective(self):
        sides = {corner_to_side(c) for c in range(4)}
        assert sides == {0, 1, 2, 3}


class TestModuleImportWithoutDbus:
    """ble_central must import and decode with no BlueZ on the machine."""

    def test_decode_callable_without_dbus(self):
        raw = struct.pack('<BBHI', 1, 0, 0, 0)
        frame = decode_object_datagram(raw)
        assert frame is not None and frame['corner_id'] == 1


class TestAddressFromCharPath:
    """Sender address extraction from BlueZ characteristic object paths."""

    def test_typical_path(self):
        path = '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF/service000c/char000f'
        assert address_from_char_path(path) == 'AA:BB:CC:DD:EE:FF'

    def test_lowercase_normalized(self):
        path = '/org/bluez/hci0/dev_aa_bb_cc_dd_ee_ff/char0001'
        assert address_from_char_path(path) == 'AA:BB:CC:DD:EE:FF'

    def test_no_device_in_path(self):
        assert address_from_char_path('/org/bluez/hci0') is None
        assert address_from_char_path(None) is None
        assert address_from_char_path('') is None


class TestCornerPairTable:
    """Persistent BLE address → corner_id pair set."""

    ADDR_FL = 'AA:BB:CC:DD:EE:01'
    ADDR_FR = 'AA:BB:CC:DD:EE:02'
    ADDR_NEW = 'AA:BB:CC:DD:EE:09'

    def test_unknown_first_contact_learn(self):
        params = FakeParams()
        table = CornerPairTable(params)
        table.learn(self.ADDR_FL, 0)
        assert table.as_dict() == {self.ADDR_FL: 0}
        # saved to params on change
        assert params.store[CornerPairTable.PARAM_KEY] == '{"%s": 0}' % self.ADDR_FL

    def test_learn_is_idempotent_no_rewrite(self):
        params = FakeParams()
        table = CornerPairTable(params)
        table.learn(self.ADDR_FL, 0)
        saved = params.store[CornerPairTable.PARAM_KEY]
        table.learn(self.ADDR_FL, 0)  # same mapping again
        assert table.as_dict() == {self.ADDR_FL: 0}
        assert params.store[CornerPairTable.PARAM_KEY] == saved

    def test_persist_round_trip(self):
        params = FakeParams()
        table = CornerPairTable(params)
        table.learn(self.ADDR_FL, 0)
        table.learn(self.ADDR_FR, 1)
        # reload from the same backing store (simulates restart)
        reloaded = CornerPairTable(FakeParams(params.store))
        assert reloaded.as_dict() == {self.ADDR_FL: 0, self.ADDR_FR: 1}

    def test_conflict_corner_change_updates(self, caplog):
        # strap is authoritative: same address reporting a different corner
        # updates the table (unit moved / strap re-read)
        params = FakeParams()
        table = CornerPairTable(params)
        table.learn(self.ADDR_FL, 0)
        with caplog.at_level(logging.WARNING, 'bluetoothd.ble_central'):
            table.learn(self.ADDR_FL, 2)
        assert table.as_dict() == {self.ADDR_FL: 2}
        assert any('now reports' in r.message for r in caplog.records)
        assert '"%s": 2' % self.ADDR_FL in params.store[CornerPairTable.PARAM_KEY]

    def test_duplicate_corner_claim_keeps_both(self, caplog):
        params = FakeParams()
        table = CornerPairTable(params)
        table.learn(self.ADDR_FL, 1)
        with caplog.at_level(logging.WARNING, 'bluetoothd.ble_central'):
            table.learn(self.ADDR_NEW, 1)
        assert table.as_dict() == {self.ADDR_FL: 1, self.ADDR_NEW: 1}
        assert any('also claims corner' in r.message for r in caplog.records)

    def test_empty_and_corrupt_param(self, caplog):
        assert CornerPairTable(FakeParams()).as_dict() == {}
        with caplog.at_level(logging.WARNING, 'bluetoothd.ble_central'):
            table = CornerPairTable(FakeParams({CornerPairTable.PARAM_KEY: '{not json'}))
        assert table.as_dict() == {}
        assert any('corrupt' in r.message for r in caplog.records)

    def test_invalid_corner_ids_filtered_on_load(self):
        params = FakeParams({CornerPairTable.PARAM_KEY:
                             '{"%s": 0, "%s": 7, "%s": -1}' %
                             (self.ADDR_FL, self.ADDR_FR, self.ADDR_NEW)})
        assert CornerPairTable(params).as_dict() == {self.ADDR_FL: 0}

    def test_learn_rejects_bad_inputs(self):
        params = FakeParams()
        table = CornerPairTable(params)
        table.learn(None, 0)
        table.learn(self.ADDR_FL, 0xFF)   # strap-unresolved
        table.learn(self.ADDR_FL, 4)
        assert table.as_dict() == {}
        assert CornerPairTable.PARAM_KEY not in params.store  # never written

    def test_describe_startup_line(self):
        params = FakeParams()
        table = CornerPairTable(params)
        assert table.describe() == '(empty)'
        table.learn(self.ADDR_FL.lower(), 0)  # addresses normalize to upper
        table.learn(self.ADDR_FR, 1)
        desc = table.describe()
        assert '%s→FL' % self.ADDR_FL in desc
        assert '%s→FR' % self.ADDR_FR in desc


class TestAuthorizationPredicate:
    """CornerPairTable.is_allowed — paired / bootstrap / pairing-window."""

    KNOWN = 'AA:BB:CC:DD:EE:01'
    UNKNOWN = 'AA:BB:CC:DD:EE:09'

    def _table(self, params=None):
        return CornerPairTable(FakeParams(params))

    def test_bootstrap_empty_table_admits_anything(self):
        t = self._table()
        assert t.is_allowed(self.UNKNOWN, pairing_open=False) is True

    def test_enforcement_rejects_unknown_window_closed(self):
        t = self._table({CornerPairTable.PARAM_KEY: '{"%s": 0}' % self.KNOWN})
        assert t.is_allowed(self.UNKNOWN, pairing_open=False) is False

    def test_window_open_admits_unknown(self):
        t = self._table({CornerPairTable.PARAM_KEY: '{"%s": 0}' % self.KNOWN})
        assert t.is_allowed(self.UNKNOWN, pairing_open=True) is True

    def test_known_address_always_admitted(self):
        t = self._table({CornerPairTable.PARAM_KEY: '{"%s": 0}' % self.KNOWN})
        assert t.is_allowed(self.KNOWN, pairing_open=False) is True
        assert t.is_allowed(self.KNOWN.lower(), pairing_open=False) is True
        assert t.is_allowed(self.KNOWN, pairing_open=True) is True

    def test_unidentifiable_address_never_allowed(self):
        t = self._table()
        assert t.is_allowed(None, pairing_open=True) is False
        assert t.is_allowed('', pairing_open=True) is False


class TestFramePathAuthorization:
    """BLECentral._on_properties_changed — the frame-path enforcement gate.

    Exercised directly (no D-Bus): the handler only needs the signal args."""

    ADDR = 'AA:BB:CC:DD:EE:01'
    PATH = '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01/service000c/char000f'
    WIFI_MAC = '02:00:00:00:00:01'

    def _feed(self, central: BLECentral, corner: int = 0, path: str | None = None):
        raw = encode_datagram(corner, 1, 100, [{
            'trackId': 7, 'rangM': 5.0, 'vRel': -1.0, 'azimuthDeg': 10.0,
            'snrDb': 20.0, 'existenceProb': 90.0, 'measured': True,
        }])
        central._on_properties_changed(
            GATT_CHAR_IFACE, {'Value': raw}, [], path=path or self.PATH)

    def _seed_candidate(self, central: BLECentral, addr: str | None = None,
                        dwell_s: float = PAIR_DWELL_S + 1.0,
                        rssi: int = -50, wifi_mac: str | None = WIFI_MAC):
        """Make `addr` an eligible candidate without the D-Bus discovery path."""
        now = time.monotonic()
        central._candidates[(addr or self.ADDR).upper()] = {
            'first_seen': now - dwell_s, 'last_seen': now,
            'rssi': rssi, 'wifi_mac': wifi_mac,
        }

    def test_bootstrap_frame_learns_and_creates_state(self):
        params = FakeParams()
        central = BLECentral(params)
        central._wifi_roster = None  # degraded mode: dwell+RSSI only
        self._seed_candidate(central)
        self._feed(central)
        assert 0 in central._corners
        assert central._pairs.as_dict() == {self.ADDR: 0}

    def test_unauthorized_frame_dropped_no_corner_state(self, caplog):
        # non-empty pair set, window closed, sender not in it → drop
        params = FakeParams({CornerPairTable.PARAM_KEY: '{"AA:BB:CC:DD:EE:02": 1}'})
        central = BLECentral(params)
        with caplog.at_level(logging.WARNING, 'bluetoothd.ble_central'):
            self._feed(central)
        assert central._corners == {}
        assert central._pairs.as_dict() == {'AA:BB:CC:DD:EE:02': 1}  # no learn
        assert any('unauthorized' in r.message for r in caplog.records)

    def test_unauthorized_warning_rate_limited(self, caplog):
        params = FakeParams({CornerPairTable.PARAM_KEY: '{"AA:BB:CC:DD:EE:02": 1}'})
        central = BLECentral(params)
        with caplog.at_level(logging.WARNING, 'bluetoothd.ble_central'):
            self._feed(central)
            self._feed(central)
            self._feed(central)
        warnings = [r for r in caplog.records if 'unauthorized' in r.message]
        assert len(warnings) == 1  # rate-limited, not one per frame

    def test_window_open_frame_learns(self):
        params = FakeParams({
            CornerPairTable.PARAM_KEY: '{"AA:BB:CC:DD:EE:02": 1}',
            'BLERadarPairingOpen': '1',
        })
        central = BLECentral(params)
        central._wifi_roster = None
        self._seed_candidate(central)
        self._feed(central)
        assert 0 in central._corners
        assert central._pairs.as_dict() == {'AA:BB:CC:DD:EE:02': 1, self.ADDR: 0}

    def test_pairing_open_param_reread_on_toggle(self):
        params = FakeParams({'BLERadarPairingOpen': '0'})
        central = BLECentral(params)
        assert central._pairing_open() is False
        # toggle without restart — cached within TTL, picked up after expiry
        params.store['BLERadarPairingOpen'] = '1'
        assert central._pairing_open() is False  # still cached
        central._pairing_open_cache = (False, 0.0)  # force TTL expiry
        assert central._pairing_open() is True


class TestParseWifiMac:
    """parse_wifi_mac_from_mfg_data — ESP32 identity proof extraction."""

    def test_good_payload(self):
        mfg = {ESPRESSIF_COMPANY_ID: bytes([0x02, 0x11, 0x22, 0x33, 0x44, 0x55])}
        assert parse_wifi_mac_from_mfg_data(mfg) == '02:11:22:33:44:55'

    def test_longer_payload_uses_first_6(self):
        mfg = {ESPRESSIF_COMPANY_ID: bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00, 0x00])}
        assert parse_wifi_mac_from_mfg_data(mfg) == 'AA:BB:CC:DD:EE:FF'

    def test_wrong_company_id(self):
        assert parse_wifi_mac_from_mfg_data({0x004C: b'\x01\x02\x03\x04\x05\x06'}) is None

    def test_short_payload(self):
        assert parse_wifi_mac_from_mfg_data({ESPRESSIF_COMPANY_ID: b'\x01\x02\x03'}) is None

    def test_empty_or_none(self):
        assert parse_wifi_mac_from_mfg_data({}) is None
        assert parse_wifi_mac_from_mfg_data(None) is None


class TestLoadWifiRoster:
    """load_wifi_roster — tolerant parsing, missing-file degrade."""

    def test_tolerant_parsing(self, tmp_path):
        f = tmp_path / 'ap0.accept'
        f.write_text('# our vehicle corners\n'
                     '02:11:22:33:44:55\n'
                     '\n'
                     'aa:bb:cc:dd:ee:ff   # trailing comment\n'
                     'not-a-mac\n')
        assert load_wifi_roster(str(f)) == {'02:11:22:33:44:55', 'AA:BB:CC:DD:EE:FF'}

    def test_missing_file_degrades(self, tmp_path):
        assert load_wifi_roster(str(tmp_path / 'nope')) is None


class TestCheckLearnEligibility:
    """Eligibility: dwell + WiFi-roster identity. RSSI is advisory-only."""

    NOW = 1000.0
    MAC = '02:11:22:33:44:55'

    def _candidate(self, dwell_s=PAIR_DWELL_S + 1.0, rssi=-50, wifi_mac=MAC):
        return {'first_seen': self.NOW - dwell_s, 'last_seen': self.NOW,
                'rssi': rssi, 'wifi_mac': wifi_mac}

    def test_dwell_and_roster_eligible(self):
        ok, reason = check_learn_eligibility(self._candidate(), {self.MAC}, self.NOW)
        assert ok and reason == 'ok'

    def test_not_yet_observed(self):
        ok, _ = check_learn_eligibility(None, {self.MAC}, self.NOW)
        assert not ok

    def test_dwell_too_short(self):
        ok, reason = check_learn_eligibility(
            self._candidate(dwell_s=PAIR_DWELL_S - 1.0), {self.MAC}, self.NOW)
        assert not ok and 'dwell' in reason

    def test_rssi_never_gates(self):
        # Design correction: RSSI is advisory-only (BLE RSSI error is 1-10 m,
        # RSSI is not distance) — a weak-but-own node is still eligible
        ok, reason = check_learn_eligibility(
            self._candidate(rssi=PAIR_RSSI_DBM - 30), {self.MAC}, self.NOW)
        assert ok and reason == 'ok'
        # missing RSSI likewise never holds eligibility
        ok, _ = check_learn_eligibility(self._candidate(rssi=None), {self.MAC}, self.NOW)
        assert ok

    def test_mac_not_in_roster(self):
        ok, reason = check_learn_eligibility(
            self._candidate(wifi_mac='02:99:99:99:99:99'), {self.MAC}, self.NOW)
        assert not ok and 'roster' in reason
        # no identity claim at all also fails when roster is loaded
        ok, _ = check_learn_eligibility(self._candidate(wifi_mac=None), {self.MAC}, self.NOW)
        assert not ok

    def test_degraded_no_roster_dwell_only(self):
        ok, _ = check_learn_eligibility(self._candidate(wifi_mac=None), None, self.NOW)
        assert ok
        # even with terrible RSSI — degraded mode is dwell-only
        ok, _ = check_learn_eligibility(self._candidate(rssi=-90, wifi_mac=None), None, self.NOW)
        assert ok


class TestLearnEligibilityIntegration:
    """Eligibility wired into BLECentral — dwell reset, paired bypass."""

    ADDR = 'AA:BB:CC:DD:EE:01'
    PATH = '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01/service000c/char000f'
    WIFI_MAC = '02:00:00:00:00:01'

    def _feed(self, central: BLECentral):
        raw = encode_datagram(0, 1, 100, [])
        central._on_properties_changed(
            GATT_CHAR_IFACE, {'Value': raw}, [], path=self.PATH)

    def _central(self, store=None):
        central = BLECentral(FakeParams(store))
        central._wifi_roster = {self.WIFI_MAC}
        return central

    def test_dwell_reset_on_disappearance(self):
        central = self._central()
        now = time.monotonic()
        observed = {self.ADDR: {'rssi': -50, 'wifi_mac': self.WIFI_MAC}}
        central._update_candidates(observed, now)
        first = central._candidates[self.ADDR]['first_seen']
        assert first == now
        # disappears → entry dropped; reappearing restarts the dwell clock
        central._update_candidates({}, now + 1.0)
        assert self.ADDR not in central._candidates
        central._update_candidates(observed, now + 2.0)
        assert central._candidates[self.ADDR]['first_seen'] == now + 2.0

    def test_ineligible_candidate_frame_held(self, caplog):
        # bootstrap (empty table) but dwell not yet satisfied → held, no learn
        central = self._central()
        now = time.monotonic()
        central._candidates[self.ADDR] = {
            'first_seen': now, 'last_seen': now, 'rssi': -50, 'wifi_mac': self.WIFI_MAC}
        with caplog.at_level(logging.WARNING, 'bluetoothd.ble_central'):
            self._feed(central)
        assert central._corners == {}
        assert central._pairs.as_dict() == {}
        assert any('not learn-eligible' in r.message for r in caplog.records)

    def test_roster_mismatch_frame_held(self):
        # neighbor's node: claims a MAC in the NEIGHBOR's roster, not ours
        central = self._central()
        now = time.monotonic()
        central._candidates[self.ADDR] = {
            'first_seen': now - PAIR_DWELL_S - 1, 'last_seen': now,
            'rssi': -50, 'wifi_mac': '02:99:99:99:99:99'}
        self._feed(central)
        assert central._corners == {}
        assert central._pairs.as_dict() == {}

    def test_paired_address_bypasses_eligibility(self):
        # already-paired unit: no candidate evidence needed — identity was
        # proven when learned
        central = self._central({CornerPairTable.PARAM_KEY: '{"%s": 0}' % self.ADDR})
        self._feed(central)
        assert 0 in central._corners

    def test_degraded_no_roster_admits_on_dwell(self):
        central = self._central()
        central._wifi_roster = None  # roster unreadable → dwell-only
        now = time.monotonic()
        central._candidates[self.ADDR] = {
            'first_seen': now - PAIR_DWELL_S - 1, 'last_seen': now,
            'rssi': -50, 'wifi_mac': None}
        self._feed(central)
        assert 0 in central._corners
        assert central._pairs.as_dict() == {self.ADDR: 0}

    def test_weak_rssi_eligible_but_anomaly_logged(self, caplog):
        # RSSI below the advisory threshold: frame is NOT held (RSSI never
        # gates), but a rate-limited anomaly warning is emitted
        central = self._central()
        now = time.monotonic()
        central._candidates[self.ADDR] = {
            'first_seen': now - PAIR_DWELL_S - 1, 'last_seen': now,
            'rssi': PAIR_RSSI_DBM - 15, 'wifi_mac': self.WIFI_MAC}
        with caplog.at_level(logging.WARNING, 'bluetoothd.ble_central'):
            self._feed(central)
            self._feed(central)  # second frame: warning must be rate-limited
        assert 0 in central._corners
        assert central._pairs.as_dict() == {self.ADDR: 0}
        anomalies = [r for r in caplog.records if 'weak signal' in r.message]
        assert len(anomalies) == 1

