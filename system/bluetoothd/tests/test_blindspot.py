#!/usr/bin/env python3
"""Tests for the BlindSpot (0x0602) NCP feed — blindSpotAlert decision +
radar2d corner presence → navpilot payload.

Builds synthetic cereal messages and verifies the emitted payload schema
(the cross-repo wire contract with the navpilot Flutter app). No D-Bus.
"""
import math

from openpilot.system.bluetoothd import protocol
from openpilot.system.bluetoothd.ncp_session import (
    NCPSession, build_blindspot_payload,
)


def make_bsa(**overrides) -> dict:
    """Plain-attribute stand-in for a blindSpotAlert reader (capnp builders
    need msgq context in unit tests; getattr-based mapping works on both)."""
    class _Bsa:
        leftAlertLevel = 0
        rightAlertLevel = 0
        leftDetected = False
        rightDetected = False
        leftDistance = math.inf      # radar_zones ZoneState.off absence sentinel
        rightDistance = math.inf
        leftRelativeSpeed = 0.0
        rightRelativeSpeed = 0.0
        rearCrossTrafficDetected = False
        lcaBlockedLeft = False
        lcaBlockedRight = False
    bsa = _Bsa()
    for k, v in overrides.items():
        setattr(bsa, k, v)
    return bsa


def make_radar2d(side_present: dict[int, bool]):
    """Plain-attribute stand-in for a radar2d reader with legacy returns."""

    class _Return:
        def __init__(self, side, present):
            self.side = side
            self.present = present

    class _Radar2D:
        def __init__(self, returns):
            self.returns = returns

    return _Radar2D([_Return(side, present) for side, present in side_present.items()])


class TestMessageType:
    """protocol.py — 0x0602 reclaims the reserved v3.x slot."""

    def test_blind_spot_value(self):
        assert protocol.MessageType.BLIND_SPOT == 0x0602

    def test_frame_round_trip_16bit_type(self):
        frame = protocol.Frame.from_json(protocol.MessageType.BLIND_SPOT, {'valid': True})
        decoded, consumed = protocol.Frame.decode(frame.encode())
        assert decoded is not None
        assert consumed == len(frame.encode())
        assert decoded.msg_type == protocol.MessageType.BLIND_SPOT
        assert decoded.to_json() == {'valid': True}


class TestBuildBlindspotPayload:
    """build_blindspot_payload — field mapping, parked-degraded mode."""

    def test_valid_path_mapping(self):
        bsa = make_bsa(
            leftAlertLevel=2, leftDetected=True,
            leftDistance=3.25, leftRelativeSpeed=-2.5,
            rightAlertLevel=1, rightDetected=False,  # not detected → floats null
            rearCrossTrafficDetected=True,
            lcaBlockedLeft=True,
        )
        p = build_blindspot_payload(bsa, None, decision_valid=True)
        assert p['valid'] is True
        assert p['left'] == {'alertLevel': 2, 'detected': True,
                             'distanceM': 3.25, 'relativeSpeedMps': -2.5}
        assert p['right'] == {'alertLevel': 1, 'detected': False,
                              'distanceM': None, 'relativeSpeedMps': None}
        # cereal has a single rearCrossTrafficDetected — both keys mirror it
        assert p['rearCrossTraffic'] == {'left': True, 'right': True}
        assert p['lcaBlocked'] == {'left': True, 'right': False}
        assert p['radarPresence'] == {'frontLeft': False, 'frontRight': False,
                                      'rearLeft': False, 'rearRight': False}

    def test_detected_with_inf_distance_maps_null(self):
        bsa = make_bsa(leftDetected=True, leftDistance=math.inf, leftRelativeSpeed=1.0)
        p = build_blindspot_payload(bsa, None, decision_valid=True)
        assert p['left']['detected'] is True
        assert p['left']['distanceM'] is None            # inf = absent
        assert p['left']['relativeSpeedMps'] == 1.0

    def test_stale_decision_parked_mode_radar_still_sent(self):
        # blindSpotAlert stale (parked) → valid=false, decision fields nulled,
        # radarPresence STILL populated from radar2d (walk-around mode)
        radar2d = make_radar2d({0: True, 1: False, 2: True, 3: False})
        p = build_blindspot_payload(None, radar2d, decision_valid=False)
        assert p['valid'] is False
        assert p['left'] == {'alertLevel': 0, 'detected': False,
                             'distanceM': None, 'relativeSpeedMps': None}
        assert p['right'] == p['left']
        assert p['rearCrossTraffic'] == {'left': False, 'right': False}
        assert p['lcaBlocked'] == {'left': False, 'right': False}
        assert p['radarPresence'] == {'frontLeft': True, 'rearLeft': False,
                                      'frontRight': True, 'rearRight': False}

    def test_radar2d_side_key_mapping_all_corners(self):
        radar2d = make_radar2d({0: True, 1: True, 2: True, 3: True})
        p = build_blindspot_payload(None, radar2d, decision_valid=False)
        assert p['radarPresence'] == {'frontLeft': True, 'rearLeft': True,
                                      'frontRight': True, 'rearRight': True}

    def test_radar_presence_none_when_no_radar2d(self):
        p = build_blindspot_payload(None, None, decision_valid=False)
        assert all(v is False for v in p['radarPresence'].values())

    def test_payload_schema_keys_exact(self):
        # the wire contract with navpilot — pin the exact key set
        p = build_blindspot_payload(make_bsa(), make_radar2d({0: True}),
                                    decision_valid=True)
        assert set(p) == {'valid', 'left', 'right', 'rearCrossTraffic',
                          'lcaBlocked', 'radarPresence'}
        assert set(p['left']) == {'alertLevel', 'detected', 'distanceM', 'relativeSpeedMps'}
        assert set(p['rearCrossTraffic']) == {'left', 'right'}
        assert set(p['lcaBlocked']) == {'left', 'right'}
        assert set(p['radarPresence']) == {'frontLeft', 'frontRight', 'rearLeft', 'rearRight'}


class TestNCPSessionWiring:
    """Session subscribes to both source services (single shared SubMaster)."""

    def test_submaster_services(self):
        session = NCPSession(params=None)
        if session.sm is None:
            return  # cereal.messaging unavailable in this environment
        assert 'blindSpotAlert' in session.sm.services
        assert 'radar2d' in session.sm.services
