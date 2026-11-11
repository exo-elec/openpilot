#!/usr/bin/env python3
"""Tests for the radar pairing service tool — RADAR_PAIR_CONTROL (0x0610)
and RADAR_PAIR_STATUS (0x0611).

Pure/D-Bus-free like the rest of the suite: NCPSession + BLECentral with
FakeParams, candidates seeded directly.
"""
import time

from openpilot.system.bluetoothd import protocol
from openpilot.system.bluetoothd.ble_central import (
    CornerPairTable, BLECentral, PAIR_DWELL_S,
)
from openpilot.system.bluetoothd.ncp_session import (
    NCPSession, build_radar_pair_status,
)


class FakeParams:
    """In-memory stand-in for openpilot.common.params.Params."""

    def __init__(self, store: dict | None = None):
        self.store = dict(store or {})

    def get(self, key, **_kwargs):
        v = self.store.get(key)
        return v.encode() if v else None

    def get_bool(self, key, **_kwargs):
        return self.store.get(key) in ('1', 1, True)

    def put(self, key, dat):
        self.store[key] = dat


ADDR_A = 'AA:BB:CC:DD:EE:01'
ADDR_B = 'AA:BB:CC:DD:EE:02'
WIFI_A = '02:00:00:00:00:01'
WIFI_B = '02:00:00:00:00:02'


def make_central(store=None, roster=frozenset({WIFI_A, WIFI_B})):
    central = BLECentral(FakeParams(store))
    central._wifi_roster = set(roster) if roster is not None else None
    return central


def seed_candidate(central, addr, wifi_mac, dwell_s):
    now = time.monotonic()
    central._candidates[addr] = {
        'first_seen': now - dwell_s, 'last_seen': now,
        'rssi': -50, 'wifi_mac': wifi_mac,
    }


class TestMessageTypes:
    def test_values(self):
        assert protocol.MessageType.RADAR_PAIR_CONTROL == 0x0610
        assert protocol.MessageType.RADAR_PAIR_STATUS == 0x0611

    def test_control_frame_round_trip(self):
        frame = protocol.Frame.from_json(protocol.MessageType.RADAR_PAIR_CONTROL, {'open': True})
        decoded, _ = protocol.Frame.decode(frame.encode())
        assert decoded.msg_type == protocol.MessageType.RADAR_PAIR_CONTROL
        assert decoded.to_json() == {'open': True}


class TestBLECentralAccessors:
    """ble_central service-tool surface (D-Bus-free)."""

    def test_pairs_dump(self):
        central = make_central({CornerPairTable.PARAM_KEY:
                                f'{{"{ADDR_A}": 0, "{ADDR_B}": 3}}'})
        assert central.pairs_dump() == [
            {'address': ADDR_A, 'corner': 0},
            {'address': ADDR_B, 'corner': 3},
        ]

    def test_candidates_dump_shape(self):
        central = make_central()
        seed_candidate(central, ADDR_A, WIFI_A, PAIR_DWELL_S + 5.0)
        cands = central.candidates_dump()
        assert len(cands) == 1
        c = cands[0]
        assert set(c) == {'address', 'wifiMac', 'corner', 'dwellS', 'eligible', 'reason'}
        assert c['address'] == ADDR_A
        assert c['wifiMac'] == WIFI_A
        assert c['corner'] is None          # not connected/learned yet
        assert c['dwellS'] >= PAIR_DWELL_S + 4.0
        assert c['eligible'] is True
        assert c['reason'] == 'ok'

    def test_candidates_dump_ineligible_reason(self):
        central = make_central()
        seed_candidate(central, ADDR_A, '02:99:99:99:99:99', PAIR_DWELL_S + 1.0)
        c = central.candidates_dump()[0]
        assert c['eligible'] is False
        assert 'roster' in c['reason']

    def test_candidates_dump_corner_after_learn(self):
        central = make_central({CornerPairTable.PARAM_KEY: f'{{"{ADDR_A}": 2}}'})
        seed_candidate(central, ADDR_A, WIFI_A, PAIR_DWELL_S + 1.0)
        assert central.candidates_dump()[0]['corner'] == 2

    def test_pairing_window_open_accessor(self):
        central = make_central({'BLERadarPairingOpen': '1'})
        central._pairing_open_cache = (False, 0.0)  # force re-read
        assert central.pairing_window_open() is True


class TestRadarPairControl:
    """Inbound 0x0610 — sets/clears BLERadarPairingOpen, ACK semantics."""

    def _session(self, params):
        return NCPSession(params=params, ble_central=make_central())

    def test_open_sets_param_and_acks(self):
        params = FakeParams()
        session = self._session(params)
        resp = session.handle_frame(protocol.Frame.from_json(
            protocol.MessageType.RADAR_PAIR_CONTROL, {'open': True}))
        assert params.store['BLERadarPairingOpen'] == '1'
        assert resp is not None
        assert resp.msg_type == protocol.MessageType.RESPONSE_ACK

    def test_close_clears_param(self):
        params = FakeParams({'BLERadarPairingOpen': '1'})
        session = self._session(params)
        session.handle_frame(protocol.Frame.from_json(
            protocol.MessageType.RADAR_PAIR_CONTROL, {'open': False}))
        assert params.store['BLERadarPairingOpen'] == '0'

    def test_missing_open_defaults_closed(self):
        params = FakeParams({'BLERadarPairingOpen': '1'})
        session = self._session(params)
        session.handle_frame(protocol.Frame.from_json(
            protocol.MessageType.RADAR_PAIR_CONTROL, {}))
        assert params.store['BLERadarPairingOpen'] == '0'


class TestRadarPairStatus:
    """Outbound 0x0611 — pinned payload shape, cadence rules."""

    def test_payload_shape_pinned(self):
        central = make_central({CornerPairTable.PARAM_KEY: f'{{"{ADDR_A}": 0}}'})
        seed_candidate(central, ADDR_B, WIFI_B, 2.0)  # short dwell → ineligible
        p = build_radar_pair_status(central)
        assert set(p) == {'windowOpen', 'pairs', 'candidates'}
        assert p['windowOpen'] is False
        assert p['pairs'] == [{'address': ADDR_A, 'corner': 0}]
        assert len(p['candidates']) == 1
        assert set(p['candidates'][0]) == {'address', 'wifiMac', 'corner',
                                           'dwellS', 'eligible', 'reason'}
        assert p['candidates'][0]['corner'] is None

    def test_no_central_returns_none(self):
        assert build_radar_pair_status(None) is None

    def test_cadence_quiet_then_transition(self):
        params = FakeParams()
        central = make_central()
        session = NCPSession(params=params, ble_central=central)
        sent = []
        session._send_frame = lambda t, d: sent.append((t, d))

        session._maybe_send_radar_pair_status()
        assert len(sent) == 1                       # first call: initial state emitted once
        assert sent[0][0] == protocol.MessageType.RADAR_PAIR_STATUS
        assert set(sent[0][1]) >= {'windowOpen', 'pairs', 'candidates'}

        session._maybe_send_radar_pair_status()     # window closed, nothing changed → quiet
        assert len(sent) == 1

        # pair-set change → one frame even with window closed
        central._pairs.learn(ADDR_A, 0)
        session._maybe_send_radar_pair_status()
        assert len(sent) == 2
        assert sent[1][1]['pairs'] == [{'address': ADDR_A, 'corner': 0}]

    def test_cadence_open_window_1hz(self):
        params = FakeParams()
        central = make_central({'BLERadarPairingOpen': '1'})
        session = NCPSession(params=params, ble_central=central)
        sent = []
        session._send_frame = lambda t, d: sent.append((t, d))

        session._maybe_send_radar_pair_status()     # transition → emit
        assert len(sent) == 1
        session._maybe_send_radar_pair_status()     # inside 1 s → throttled
        assert len(sent) == 1
        session._pair_status_last_emit -= 2.0       # simulate 2 s elapsed
        session._maybe_send_radar_pair_status()     # window open → 1 Hz cadence
        assert len(sent) == 2
        assert sent[1][1]['windowOpen'] is True
