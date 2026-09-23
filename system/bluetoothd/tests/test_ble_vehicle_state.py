#!/usr/bin/env python3
"""Tests for the host → ESP32 corner-radar vehicle-state record and the
BLERadarRoster identity-roster param (ble_central.py).

Pure-python — no D-Bus / BlueZ / hal needed. Golden bytes are the
ESP32_RADAR contract (tools/golden_vectors/radar_frames.json,
"ble_vehicle_state").
"""
import math
import types

from openpilot.system.bluetoothd.ble_central import (
    VEHICLE_STATE_STRUCT, BLECentral, encode_vehicle_state, merge_rosters,
    parse_roster_param, vehicle_state_inputs,
)

GOLDEN_VEHICLE_STATE_HEX = '05016d05fa0006120f00'  # seq 5, valid, 13.89 m/s, +0.25 rad/s, 987654 us


class FakeParams:
    def __init__(self, store=None):
        self.store = dict(store or {})

    def get(self, key, **_kwargs):
        v = self.store.get(key)
        return v.encode() if v else None

    def get_bool(self, key, **_kwargs):
        return self.store.get(key) in ('1', 1, True)

    def put(self, key, dat):
        self.store[key] = dat


class TestEncodeVehicleState:
    def test_matches_esp32_golden_frame(self):
        assert encode_vehicle_state(5, 13.89, 0.25, 987654, True).hex() == GOLDEN_VEHICLE_STATE_HEX

    def test_size_is_ten_bytes(self):
        assert VEHICLE_STATE_STRUCT.size == 10

    def test_clamps_and_wraps(self):
        seq, flags, speed, yaw, t = VEHICLE_STATE_STRUCT.unpack(
            encode_vehicle_state(300, -1.0, 99.0, 2**33 + 7, True))
        assert (seq, flags, speed, yaw, t) == (300 & 0xFF, 1, 0, 32767, 7)

    def test_non_finite_clears_valid(self):
        _, flags, speed, yaw, _ = VEHICLE_STATE_STRUCT.unpack(
            encode_vehicle_state(0, math.nan, 0.1, 0, True))
        assert (flags, speed, yaw) == (0, 0, 0)

    def test_invalid_flag(self):
        assert VEHICLE_STATE_STRUCT.unpack(encode_vehicle_state(0, 5.0, 0.0, 0, False))[1] == 0


class TestVehicleStateInputs:
    def test_device_z_down_is_negated_to_left_positive(self):
        # turning left: device-frame (z down) angular velocity is negative
        speed, yaw, valid = vehicle_state_inputs(True, 10.0, True, -0.2)
        assert valid and speed == 10.0 and yaw == 0.2

    def test_invalid_when_either_input_untrusted(self):
        assert vehicle_state_inputs(False, 10.0, True, 0.1) == (0.0, 0.0, False)
        assert vehicle_state_inputs(True, 10.0, False, 0.1) == (0.0, 0.0, False)

    def test_negative_speed_floored(self):
        assert vehicle_state_inputs(True, -0.3, True, 0.0)[0] == 0.0


class TestRosterParam:
    def test_json_list(self):
        assert parse_roster_param('["aa:bb:cc:dd:ee:ff", "bad", "11:22:33:44:55:66"]') == \
            {'AA:BB:CC:DD:EE:FF', '11:22:33:44:55:66'}

    def test_text_with_comments(self):
        raw = b'AA:BB:CC:DD:EE:01  # FL, labelled 2026-09-23\n\naa:bb:cc:dd:ee:02, AA:BB:CC:DD:EE:03'
        assert parse_roster_param(raw) == {'AA:BB:CC:DD:EE:01', 'AA:BB:CC:DD:EE:02',
                                           'AA:BB:CC:DD:EE:03'}

    def test_empty(self):
        assert parse_roster_param(None) == set()
        assert parse_roster_param('') == set()

    def test_merge(self):
        assert merge_rosters(None, set()) is None  # degraded, dwell-only
        assert merge_rosters(None, {'A'}) == {'A'}
        assert merge_rosters({'B'}, set()) == {'B'}
        assert merge_rosters({'B'}, {'A'}) == {'A', 'B'}

    def test_central_uses_param_roster(self, monkeypatch):
        import openpilot.system.bluetoothd.ble_central as bc
        monkeypatch.setattr(bc, 'load_wifi_roster', lambda *a, **k: None)
        central = BLECentral(FakeParams({'BLERadarRoster': 'AA:BB:CC:DD:EE:FF'}))
        assert central._wifi_roster == {'AA:BB:CC:DD:EE:FF'}
        cand = {'first_seen': 0.0, 'wifi_mac': 'AA:BB:CC:DD:EE:FF'}
        assert bc.check_learn_eligibility(cand, central._wifi_roster, 100.0) == (True, 'ok')
        cand['wifi_mac'] = '00:00:00:00:00:01'
        assert not bc.check_learn_eligibility(cand, central._wifi_roster, 100.0)[0]


class FakeSubMaster(dict):
    """Minimal SubMaster: update(), alive/valid maps, item access."""

    def __init__(self, car_ok=True, pose_ok=True, v_ego=13.89, z=-0.25):
        super().__init__()
        self['carState'] = types.SimpleNamespace(vEgo=v_ego)
        self['livePose'] = types.SimpleNamespace(
            inputsOK=pose_ok, sensorsOK=True,
            angularVelocityDevice=types.SimpleNamespace(z=z, valid=True))
        self.alive = {'carState': car_ok, 'livePose': True}
        self.valid = {'carState': True, 'livePose': True}

    def update(self, _timeout):
        pass


class TestVehicleStateRecord:
    def test_record_from_messages_and_seq_advances(self):
        central = BLECentral(FakeParams())
        central._sm = FakeSubMaster()
        seq0, flags, speed, yaw, _ = VEHICLE_STATE_STRUCT.unpack(central._vehicle_state_record())
        seq1 = VEHICLE_STATE_STRUCT.unpack(central._vehicle_state_record())[0]
        assert (flags, speed, yaw) == (1, 1389, 250) and seq1 == (seq0 + 1) & 0xFF

    def test_dead_car_state_sends_invalid(self):
        central = BLECentral(FakeParams())
        central._sm = FakeSubMaster(car_ok=False)
        assert VEHICLE_STATE_STRUCT.unpack(central._vehicle_state_record())[1] == 0

    def test_bad_pose_sends_invalid(self):
        central = BLECentral(FakeParams())
        central._sm = FakeSubMaster(pose_ok=False)
        assert VEHICLE_STATE_STRUCT.unpack(central._vehicle_state_record())[1] == 0
