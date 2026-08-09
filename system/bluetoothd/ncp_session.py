#!/usr/bin/env python3
"""NCP session — shared NCP v4.1 protocol logic for all transports.

ONE NCPSession lives per bluetoothd process, shared by SPPD and GATTD.
This ensures a single PubMaster per service (msgq rejects multiple publishers
for the same service in one process).

Transport layer calls add_transport/remove_transport; telemetry is broadcast
to all active transports. Command responses go back via the per-call reply_fn
already handled by each transport's receive path.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from typing import Callable

try:
    import cereal.messaging as messaging
    from openpilot.common.params import Params
    from openpilot.common.realtime import Ratekeeper
except ImportError:
    messaging = None
    Params = None
    Ratekeeper = None

from openpilot.system.bluetoothd import protocol

logger = logging.getLogger('bluetoothd.ncp')

# radar2d legacy-return side → navpilot payload key (cereal side: 0=LF,1=LR,2=RF,3=RR)
_RADAR_SIDE_KEYS = {0: 'frontLeft', 1: 'rearLeft', 2: 'frontRight', 3: 'rearRight'}


def _blindspot_side(bsa, prefix: str) -> dict:
    """One side of the BlindSpot payload. distance/relativeSpeed are null when
    nothing is detected — radar_zones' ZoneState.off uses distance_m=inf as
    its absence sentinel, so non-finite values also map to null."""
    detected = bool(getattr(bsa, f'{prefix}Detected'))
    distance = float(getattr(bsa, f'{prefix}Distance'))
    v_rel = float(getattr(bsa, f'{prefix}RelativeSpeed'))
    return {
        'alertLevel': int(getattr(bsa, f'{prefix}AlertLevel')),
        'detected': detected,
        'distanceM': round(distance, 2) if detected and math.isfinite(distance) else None,
        'relativeSpeedMps': round(v_rel, 2) if detected and math.isfinite(v_rel) else None,
    }


def build_blindspot_payload(bsa, radar2d, decision_valid: bool) -> dict:
    """BlindSpot (0x0602) JSON payload — CROSS-REPO WIRE CONTRACT with the
    navpilot Flutter app: do not rename fields.

    decision_valid=False is the parked / walk-around mode: controlsd runs
    ignition_on only, so blindSpotAlert goes stale when parked while
    bluetoothd (always_run) keeps receiving radar2d — the decision fields are
    then nulled but radarPresence is STILL populated.
    """
    null_side = {'alertLevel': 0, 'detected': False, 'distanceM': None, 'relativeSpeedMps': None}
    valid = decision_valid and bsa is not None
    payload = {
        'valid': valid,
        'left': _blindspot_side(bsa, 'left') if valid else dict(null_side),
        'right': _blindspot_side(bsa, 'right') if valid else dict(null_side),
        # cereal carries a single rearCrossTrafficDetected (no per-side split) —
        # both keys faithfully mirror it
        'rearCrossTraffic': {
            'left': bool(bsa.rearCrossTrafficDetected) if valid else False,
            'right': bool(bsa.rearCrossTrafficDetected) if valid else False,
        },
        'lcaBlocked': {
            'left': bool(bsa.lcaBlockedLeft) if valid else False,
            'right': bool(bsa.lcaBlockedRight) if valid else False,
        },
        'radarPresence': {'frontLeft': False, 'frontRight': False,
                          'rearLeft': False, 'rearRight': False},
    }
    if radar2d is not None:
        for ret in radar2d.returns:
            key = _RADAR_SIDE_KEYS.get(int(ret.side))
            if key:
                payload['radarPresence'][key] = payload['radarPresence'][key] or bool(ret.present)
    return payload


RADAR_PAIR_STATUS_HZ = 1.0  # status cadence while the pairing window is open


def build_radar_pair_status(ble_central, now: float | None = None) -> dict | None:
    """RADAR_PAIR_STATUS (0x0611) payload — CROSS-REPO WIRE CONTRACT with the
    navpilot Flutter app: do not rename fields. Returns None when no
    BLECentral is wired (corner-radar feature absent)."""
    if ble_central is None:
        return None
    return {
        'windowOpen': ble_central.pairing_window_open(),
        'pairs': ble_central.pairs_dump(),
        'candidates': ble_central.candidates_dump(now),
    }


class NCPSession:
    """NCP v4.1 protocol handler, transport-agnostic.

    Usage:
        session = NCPSession(params, name='spp')
        session.start()                        # start telemetry thread

        # when transport connects:
        session.on_connect(send_fn)            # send_fn: bytes → None, thread-safe

        # incoming data from transport:
        response = session.handle_frame(frame) # returns Frame or None
        if response:
            send_fn(response.encode())

        # when transport disconnects:
        session.on_disconnect()

        session.stop()                         # clean shutdown
    """

    TELEMETRY_RATE = 10.0  # Hz

    def __init__(self, params: Params | None = None, ble_central=None):
        self.params = params or (Params() if Params else None)
        self._name = 'ncp'  # used in log messages only

        # Radar pairing service tool (RADAR_PAIR_CONTROL/STATUS) — the
        # BLECentral instance owned by bluetoothd; None when not wired
        self._ble_central = ble_central
        self._pair_status_fingerprint = None   # last emitted (windowOpen, pairs)
        self._pair_status_last_emit = 0.0

        # Active transports: name → thread-safe send_fn
        # Key = transport name ('spp', 'gatt'); removed on disconnect.
        self._transports: dict[str, Callable[[bytes], None]] = {}
        self._transport_lock = threading.Lock()

        # Protocol state (shared across transports — one logical session)
        self._is_paired: bool = False
        self._last_route_id: int | None = None

        # ONE pm/sm for the whole process — msgq rejects multiple publishers
        if messaging:
            self.pm = messaging.PubMaster(['obdCommand', 'voiceCommandRequest', 'ncpVehicleData'])
            self.sm = messaging.SubMaster([
                'carState', 'navInstruction', 'navRoute', 'obdState',
                'selfdriveState', 'controlsState', 'deviceState', 'alertDebug',
                'blindSpotAlert', 'radar2d',
            ])
        else:
            self.pm = None
            self.sm = None

        self._running = False
        self._telem_thread: threading.Thread | None = None

    # ── Transport registration ────────────────────────────────────────────────

    def add_transport(self, name: str, send_fn: Callable[[bytes], None]) -> None:
        """Register a transport. Call when a phone connects on any channel."""
        with self._transport_lock:
            self._transports[name] = send_fn
        # Reset per-session route state so the new client gets a fresh route push
        self._last_route_id = None
        logger.info('NCP: transport "%s" connected (%d active)', name, len(self._transports))

    def remove_transport(self, name: str) -> None:
        """Deregister a transport. Call when a phone disconnects."""
        with self._transport_lock:
            self._transports.pop(name, None)
            count = len(self._transports)
        if count == 0:
            self._is_paired = False
        logger.info('NCP: transport "%s" disconnected (%d remaining)', name, count)

    def is_connected(self) -> bool:
        with self._transport_lock:
            return bool(self._transports)

    # ── Send ─────────────────────────────────────────────────────────────────

    def _broadcast(self, data: bytes) -> None:
        """Send encoded frame data to ALL currently active transports."""
        with self._transport_lock:
            fns = list(self._transports.values())
        for fn in fns:
            try:
                fn(data)
            except Exception as e:
                logger.debug('NCP broadcast error: %s', e)

    def _send_frame(self, msg_type: int, data: dict) -> None:
        """Broadcast a JSON frame to all active transports (used by telemetry)."""
        if not self.is_connected():
            return
        try:
            self._broadcast(protocol.Frame.from_json(msg_type, data).encode())
        except Exception as e:
            logger.debug('NCP send_frame error: %s', e)

    # ── Frame dispatch ────────────────────────────────────────────────────────

    def handle_frame(self, frame: protocol.Frame) -> protocol.Frame | None:
        """Dispatch incoming NCP frame → return response frame or None."""
        t = frame.msg_type
        if t == protocol.MessageType.CMD_NAVIGATE:
            return self._handle_navigate(frame)
        if t == protocol.MessageType.CMD_CANCEL_NAV:
            return self._handle_cancel_nav(frame)
        if t == protocol.MessageType.CMD_OBD_REQUEST:
            return self._handle_obd(frame)
        if t == protocol.MessageType.CMD_GET_VEHICLE_INFO:
            return self._handle_get_vehicle_info(frame)
        if t == protocol.MessageType.CMD_VEHICLE_DATA:
            return self._handle_vehicle_data(frame)
        if t == protocol.MessageType.CMD_VOICE_INTENT:
            return self._handle_voice_intent(frame)
        if t == protocol.MessageType.CMD_DRIVING_PROFILE:
            return self._handle_driving_profile(frame)
        if t == protocol.MessageType.CMD_MISSION_GUIDANCE:
            return self._handle_mission_guidance(frame)
        if t == protocol.MessageType.CMD_AUTH_HANDSHAKE:
            return self._handle_auth_handshake(frame)
        if t == protocol.MessageType.CMD_OAUTH_TOKEN:
            return self._handle_oauth_token(frame)
        if t == protocol.MessageType.CMD_PAIR:
            return self._handle_pair(frame)
        if t == protocol.MessageType.CMD_UNPAIR:
            return self._handle_unpair(frame)
        if t == protocol.MessageType.CMD_CONVOY_LEAD:
            return self._handle_convoy_lead(frame)
        if t == protocol.MessageType.CMD_CONVOY_CANCEL:
            return self._handle_convoy_cancel(frame)
        if t == protocol.MessageType.RADAR_PAIR_CONTROL:
            return self._handle_radar_pair_control(frame)
        if t == protocol.MessageType.DEVICE_CAPABILITIES:
            return protocol.Frame.from_json(
                protocol.MessageType.RESPONSE_DEVICE_INFO,
                protocol.get_device_info(requires_pairing=True, is_paired=self._is_paired),
            )
        if t == protocol.MessageType.SEARCH_REQUEST:
            return self._handle_search_fallback(frame)
        if t == protocol.MessageType.PING:
            return protocol.Frame(protocol.MessageType.PONG, b'')
        return None

    # ── Command handlers ──────────────────────────────────────────────────────

    def _handle_navigate(self, frame: protocol.Frame) -> protocol.Frame:
        try:
            data = frame.to_json()
            lat = data.get('lat') or data.get('latitude')
            lon = data.get('lon') or data.get('longitude')
            name = data.get('name', '')
            if lat is None or lon is None:
                return protocol.make_error('Missing lat/lon')
            if self.params:
                self.params.put('NavDestination', json.dumps({
                    'latitude': float(lat), 'longitude': float(lon), 'place_name': name,
                }))
                self.params.remove('NavDestinationWaypoints')
            logger.info('%s: navigate → %s (%.5f, %.5f)', self._name, name, lat, lon)
            return protocol.make_ack(protocol.MessageType.CMD_NAVIGATE)
        except Exception as e:
            logger.error('%s: navigate error: %s', self._name, e)
            return protocol.make_error(f'Navigate failed: {e}')

    def _handle_cancel_nav(self, frame: protocol.Frame) -> protocol.Frame:
        try:
            if self.params:
                self.params.remove('NavDestination')
                self.params.remove('NavDestinationWaypoints')
            return protocol.make_ack(protocol.MessageType.CMD_CANCEL_NAV)
        except Exception as e:
            return protocol.make_error(f'Cancel failed: {e}')

    def _handle_convoy_lead(self, frame: protocol.Frame) -> protocol.Frame:
        """Lead friend's live position → moving destination (dedicated convoy path).

        Same effect as CMD_NAVIGATE (writes NavDestination, which navd re-routes to);
        the dedicated type just lets NavPilot distinguish "following a friend" from a
        one-off destination without touching the planner.
        """
        try:
            data = frame.to_json()
            lat = data.get('latitude')
            lon = data.get('longitude')
            friend_id = data.get('friendId', '')
            if lat is None or lon is None:
                return protocol.make_error('Missing lat/lon')
            if self.params:
                self.params.put('NavDestination', json.dumps({
                    'latitude': float(lat), 'longitude': float(lon),
                    'place_name': f'Convoy: {friend_id}' if friend_id else 'Convoy',
                }))
                self.params.remove('NavDestinationWaypoints')
            logger.info('%s: convoy lead → %s (%.5f, %.5f)', self._name, friend_id, lat, lon)
            return protocol.make_ack(protocol.MessageType.CMD_CONVOY_LEAD)
        except Exception as e:
            logger.error('%s: convoy lead error: %s', self._name, e)
            return protocol.make_error(f'Convoy lead failed: {e}')

    def _handle_convoy_cancel(self, frame: protocol.Frame) -> protocol.Frame:
        try:
            if self.params:
                self.params.remove('NavDestination')
                self.params.remove('NavDestinationWaypoints')
            logger.info('%s: convoy cancelled', self._name)
            return protocol.make_ack(protocol.MessageType.CMD_CONVOY_CANCEL)
        except Exception as e:
            return protocol.make_error(f'Convoy cancel failed: {e}')

    def _handle_obd(self, frame: protocol.Frame) -> protocol.Frame:
        if not self.pm or not messaging:
            return protocol.make_error('OBD not available')
        try:
            cmd = frame.to_json().get('command', '')
            if cmd:
                msg = messaging.new_message('obdCommand')
                msg.obdCommand.command = cmd
                self.pm.send('obdCommand', msg)
                return protocol.make_ack(protocol.MessageType.CMD_OBD_REQUEST)
        except Exception as e:
            logger.error('%s: OBD error: %s', self._name, e)
        return protocol.make_error('Invalid OBD command')

    def _handle_get_vehicle_info(self, frame: protocol.Frame) -> protocol.Frame:
        if not self.sm:
            return protocol.make_error('Vehicle info not available')
        self.sm.update(0)
        if not self.sm.updated['obdState']:
            return protocol.make_error('Vehicle not detected yet')
        try:
            obd = self.sm['obdState']
            return protocol.make_vehicle_info(vin=obd.vin, vehicle_type=obd.vehicleType, make=obd.make)
        except Exception as e:
            return protocol.make_error(f'Vehicle info error: {e}')

    def _handle_vehicle_data(self, frame: protocol.Frame) -> protocol.Frame:
        if not self.pm or not messaging:
            return protocol.make_error('Vehicle data not available')
        try:
            data = frame.to_json()
            msg = messaging.new_message('ncpVehicleData')
            vd = msg.ncpVehicleData
            vd.valid             = data.get('valid', True)
            vd.batterySoc        = data.get('batterySoc', -1.0)
            vd.batterySoh        = data.get('batterySoh', -1.0)
            vd.batteryVoltage    = data.get('batteryVoltage', -1.0)
            vd.batteryCurrent    = data.get('batteryCurrent', 0.0)
            vd.batteryTempMax    = data.get('batteryTempMax', -273.0)
            vd.batteryTempMin    = data.get('batteryTempMin', -273.0)
            vd.batteryPower      = data.get('batteryPower', 0.0)
            vd.chargingStatus    = data.get('chargingStatus', 0)
            vd.chargingPower     = data.get('chargingPower', 0.0)
            vd.rangeRemaining    = data.get('rangeRemaining', -1.0)
            vd.motorRpm          = data.get('motorRpm', -1.0)
            vd.motorTemp         = data.get('motorTemp', -273.0)
            vd.inverterTemp      = data.get('inverterTemp', -273.0)
            vd.auxBatteryVoltage = data.get('auxBatteryVoltage', -1.0)
            vd.engineRpm         = data.get('engineRpm', -1.0)
            vd.coolantTemp       = data.get('coolantTemp', -273.0)
            vd.throttlePos       = data.get('throttlePos', -1.0)
            vd.engineLoad        = data.get('engineLoad', -1.0)
            vd.fuelLevel         = data.get('fuelLevel', -1.0)
            vd.vehicleSpeed      = data.get('vehicleSpeed', -1.0)
            vd.odometer          = data.get('odometer', -1.0)
            vd.vin               = data.get('vin', '')
            vd.vehicleType       = data.get('vehicleType', '')
            vd.timestamp         = data.get('timestamp', int(time.time() * 1e9))
            self.pm.send('ncpVehicleData', msg)
            return protocol.make_ack(protocol.MessageType.CMD_VEHICLE_DATA)
        except Exception as e:
            logger.error('%s: vehicle data error: %s', self._name, e)
            return protocol.make_error('Vehicle data failed')

    def _handle_voice_intent(self, frame: protocol.Frame) -> protocol.Frame:
        if not self.pm or not messaging:
            return protocol.make_error('Voice not available')
        try:
            data = frame.to_json()
            msg = messaging.new_message('voiceCommandRequest')
            msg.voiceCommandRequest.command = json.dumps({
                'type': 'voice_intent',
                'action': data.get('action', ''),
                'params': data.get('params', {}),
                'commandId': data.get('commandId', ''),
            })
            self.pm.send('voiceCommandRequest', msg)
            return protocol.make_ack(protocol.MessageType.CMD_VOICE_INTENT)
        except Exception as e:
            return protocol.make_error(f'Voice intent failed: {e}')

    def _handle_driving_profile(self, frame: protocol.Frame) -> protocol.Frame:
        try:
            profile = frame.to_json().get('profile', 'standard')
            _MAP = {
                'aggressive': '0', 'standard': '1', 'relaxed': '2', 'traffic': '3',
                'eco': '2', 'sport': '0', 'normal': '1', 'range_saver': '3',
            }
            if self.params:
                self.params.put_nonblocking('LongitudinalPersonality', _MAP.get(profile, '1'))
            logger.info('%s: driving profile → %s', self._name, profile)
            return protocol.make_ack(protocol.MessageType.CMD_DRIVING_PROFILE)
        except Exception as e:
            return protocol.make_error(f'Driving profile failed: {e}')

    def _handle_mission_guidance(self, frame: protocol.Frame) -> protocol.Frame:
        try:
            data = frame.to_json()
            if self.pm and messaging:
                msg = messaging.new_message('voiceCommandRequest')
                msg.voiceCommandRequest.command = json.dumps({
                    'type': 'mission_guidance',
                    'guidanceType': data.get('guidanceType', ''),
                    'message': data.get('message', ''),
                    'targetSoc': data.get('targetSoc'),
                    'suggestedSpeed': data.get('suggestedSpeed'),
                })
                self.pm.send('voiceCommandRequest', msg)
            return protocol.make_ack(protocol.MessageType.CMD_MISSION_GUIDANCE)
        except Exception as e:
            return protocol.make_error(f'Mission guidance failed: {e}')

    def _handle_auth_handshake(self, frame: protocol.Frame) -> protocol.Frame:
        # Retired: NavPilotOAuthToken/NavPilotOAuthEmail params were written
        # here but never read anywhere in this repo (checked this session —
        # no consumer). The phone-side sender (sendAuthHandshake) was also
        # dead code (zero call sites) and has been removed from navpilot.
        # Ack-only for wire compat with any older phone app still sending it.
        return protocol.make_ack(protocol.MessageType.CMD_AUTH_HANDSHAKE)

    def _handle_oauth_token(self, frame: protocol.Frame) -> protocol.Frame:
        # Retired: device-direct-Gemini OAuth relay. The phone no longer
        # sends this (see navpilot's frame_protocol.dart comment on
        # cmdOAuthToken) — superseded by the account-linked device
        # credential flow (CMD_DEVICE_CREDENTIAL). Ack-only for wire compat.
        return protocol.make_ack(protocol.MessageType.CMD_OAUTH_TOKEN)

    def _handle_pair(self, frame: protocol.Frame) -> protocol.Frame:
        try:
            code = frame.to_json().get('code', '')
            stored = b''
            if self.params:
                stored = self.params.get('BluetoothPairingPin') or b''
            stored_str = stored.decode() if isinstance(stored, bytes) else stored
            if stored_str and code == stored_str:
                self._is_paired = True
                if self.params:
                    self.params.put('EOPNavPilotPaired', '1')
                    self.params.put('BluetoothPairingActive', '0')
                logger.info('%s: NCP pair accepted', self._name)
                return protocol.Frame.from_json(protocol.MessageType.RESPONSE_PAIR, {'success': True})
            logger.warning('%s: NCP pair rejected (code mismatch)', self._name)
            return protocol.Frame.from_json(protocol.MessageType.RESPONSE_PAIR,
                                            {'success': False, 'error': 'invalid_code'})
        except Exception as e:
            return protocol.make_error(f'Pair failed: {e}')

    def _handle_unpair(self, frame: protocol.Frame) -> protocol.Frame:
        self._is_paired = False
        if self.params:
            self.params.put('EOPNavPilotPaired', '0')
        logger.info('%s: unpaired', self._name)
        return protocol.make_ack(protocol.MessageType.CMD_UNPAIR)

    def _handle_search_fallback(self, frame: protocol.Frame) -> protocol.Frame:
        try:
            request_id = frame.to_json().get('requestId', 'unknown')
            return protocol.Frame.from_json(protocol.MessageType.SEARCH_RESPONSE, {
                'protocolVersion': protocol.PROTOCOL_VERSION,
                'msgType': 'SearchResponse',
                'requestId': request_id,
                'success': False,
                'error': {'code': 'SEARCH_UNAVAILABLE', 'message': 'Device-side search not supported'},
                'fallback': True,
                'results': [],
            })
        except Exception as e:
            return protocol.make_error(f'Search error: {e}')

    def _handle_radar_pair_control(self, frame: protocol.Frame) -> protocol.Frame:
        """navpilot service tool: {"open": bool} → persisted BLERadarPairingOpen
        param (ble_central re-reads it within its TTL). Mirrors the ACK/error
        reply semantics of the other inbound commands."""
        try:
            open_window = bool(frame.to_json().get('open', False))
            if self.params:
                self.params.put('BLERadarPairingOpen', '1' if open_window else '0')
            logger.info('%s: radar pairing window %s (service tool)',
                        self._name, 'OPEN' if open_window else 'CLOSED')
            return protocol.make_ack(protocol.MessageType.RADAR_PAIR_CONTROL)
        except Exception as e:
            logger.error('%s: radar pair control error: %s', self._name, e)
            return protocol.make_error(f'Radar pair control failed: {e}')

    def _maybe_send_radar_pair_status(self) -> None:
        """RADAR_PAIR_STATUS cadence: ~1 Hz while the window is open, plus one
        frame on any windowOpen / pair-set transition — quiet otherwise."""
        if self._ble_central is None:
            return
        try:
            payload = build_radar_pair_status(self._ble_central)
            fingerprint = (payload['windowOpen'],
                           tuple((p['address'], p['corner']) for p in payload['pairs']))
            now = time.monotonic()
            due = payload['windowOpen'] and \
                  (now - self._pair_status_last_emit) >= 1.0 / RADAR_PAIR_STATUS_HZ
            changed = fingerprint != self._pair_status_fingerprint
            if due or changed:
                self._pair_status_last_emit = now
                self._pair_status_fingerprint = fingerprint
                self._send_frame(protocol.MessageType.RADAR_PAIR_STATUS, {
                    'protocolVersion': protocol.PROTOCOL_VERSION,
                    'msgType': 'RadarPairStatus',
                    **payload,
                })
        except Exception as e:
            logger.debug('%s: radar pair status error: %s', self._name, e)

    # ── Telemetry ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return  # idempotent — safe to call from both bluetoothd and SPPD standalone
        self._running = True
        self._telem_thread = threading.Thread(
            target=self._telemetry_loop, daemon=True, name=f'{self._name}_telem',
        )
        self._telem_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._telem_thread:
            self._telem_thread.join(timeout=2.0)
            self._telem_thread = None

    def _telemetry_loop(self) -> None:
        if not Ratekeeper:
            return
        rk = Ratekeeper(self.TELEMETRY_RATE)
        while self._running:
            if self.is_connected():
                try:
                    self._send_telemetry()
                except Exception as e:
                    logger.debug('%s: telemetry error: %s', self._name, e)
            rk.keep_time()

    def _send_telemetry(self) -> None:
        if not self.sm:
            return
        self.sm.update(0)

        if self.sm.updated['carState'] or self.sm.updated.get('obdState'):
            try:
                cs = self.sm['carState']
                obd = self.sm['obdState'] if self.sm.updated.get('obdState') else None
                data: dict = {
                    'protocolVersion': protocol.PROTOCOL_VERSION,
                    'msgType': 'VehicleTelemetry',
                    'speed': round(cs.vEgo, 2),
                    'steering': round(cs.steeringAngleDeg, 2),
                    'gear': str(cs.gearShifter),
                }
                if obd is not None and obd.obdConnected:
                    if obd.engineRpm > 0:      data['engineRpm']      = round(obd.engineRpm, 1)
                    if obd.coolantTemp > -40:  data['coolantTemp']    = round(obd.coolantTemp, 1)
                    if obd.fuelLevel >= 0:     data['fuelLevel']      = round(obd.fuelLevel, 1)
                    if obd.throttlePos >= 0:   data['throttlePos']    = round(obd.throttlePos, 1)
                    if obd.odometer > 0:       data['odometer']       = round(obd.odometer, 1)
                    if obd.batterySoc >= 0:    data['batterySoc']     = round(obd.batterySoc, 1)
                    if obd.batteryVoltage > 0: data['batteryVoltage'] = round(obd.batteryVoltage, 1)
                    if obd.batteryCurrent != 0: data['batteryCurrent'] = round(obd.batteryCurrent, 1)
                    if obd.batteryTempMax > -40: data['batteryTempMax'] = round(obd.batteryTempMax, 1)
                    if obd.rangeRemaining > 0: data['rangeRemaining'] = round(obd.rangeRemaining, 1)
                    if obd.motorTemp > -40:    data['motorTemp']      = round(obd.motorTemp, 1)
                    if obd.inverterTemp > -40: data['inverterTemp']   = round(obd.inverterTemp, 1)
                self._send_frame(protocol.MessageType.TELEMETRY_VEHICLE, data)
            except Exception as e:
                logger.debug('%s: carState telem error: %s', self._name, e)

        if self.sm.updated['navInstruction']:
            try:
                nav = self.sm['navInstruction']
                self._send_frame(protocol.MessageType.TELEMETRY_NAV, {
                    'protocolVersion': protocol.PROTOCOL_VERSION,
                    'msgType': 'NavManeuver',
                    'instruction': nav.maneuverPrimaryText,
                    'secondaryInstruction': nav.maneuverSecondaryText,
                    'type': nav.maneuverType,
                    'modifier': nav.maneuverModifier,
                    'distanceM': round(nav.maneuverDistance, 1),
                    'distanceRemainingM': round(nav.distanceRemaining, 1),
                    'timeRemainingS': round(nav.timeRemaining, 1),
                    'speedLimitMs': round(nav.speedLimit, 2) if nav.speedLimit > 0 else None,
                    'showFull': nav.showFull,
                })
            except Exception as e:
                logger.debug('%s: navInstruction telem error: %s', self._name, e)

        if self.sm.updated['navRoute']:
            try:
                route = self.sm['navRoute']
                route_id = route.routeId
                if route_id != self._last_route_id and route.coordinates:
                    self._last_route_id = route_id
                    self._send_frame(protocol.MessageType.TELEMETRY_ROUTE, {
                        'protocolVersion': protocol.PROTOCOL_VERSION,
                        'msgType': 'NavRouteGeometry',
                        'routeId': route_id,
                        'coordinates': [
                            {'lat': round(c.latitude, 6), 'lon': round(c.longitude, 6)}
                            for c in route.coordinates
                        ],
                    })
            except Exception as e:
                logger.debug('%s: navRoute telem error: %s', self._name, e)

        if self.sm.updated['selfdriveState'] or self.sm.updated['controlsState']:
            try:
                ss = self.sm['selfdriveState']
                cs_ctrl = self.sm['controlsState']
                self._send_frame(protocol.MessageType.TELEMETRY_ADAS, {
                    'protocolVersion': protocol.PROTOCOL_VERSION,
                    'msgType': 'ADASState',
                    'enabled': ss.enabled,
                    'state': ss.state,
                    'engaged': cs_ctrl.active,
                    'vCruise': round(cs_ctrl.vCruise, 2),
                    'latActive': cs_ctrl.latActive,
                    'longActive': cs_ctrl.longActive,
                })
            except Exception as e:
                logger.debug('%s: ADAS telem error: %s', self._name, e)

        if self.sm.updated['deviceState']:
            try:
                ds = self.sm['deviceState']
                self._send_frame(protocol.MessageType.TELEMETRY_SYSTEM, {
                    'protocolVersion': protocol.PROTOCOL_VERSION,
                    'msgType': 'SystemHealth',
                    'cpuTempC': round(ds.cpuTempC[0], 1) if ds.cpuTempC else None,
                    'gpuTempC': round(ds.gpuTempC[0], 1) if ds.gpuTempC else None,
                    'freeSpacePercent': round(ds.freeSpacePercent, 1) if hasattr(ds, 'freeSpacePercent') else None,
                    'memoryUsagePercent': round(ds.memoryUsagePercent, 1) if hasattr(ds, 'memoryUsagePercent') else None,
                })
            except Exception as e:
                logger.debug('%s: deviceState telem error: %s', self._name, e)

        if self.sm.updated.get('alertDebug'):
            try:
                alert = self.sm['alertDebug']
                if alert.active:
                    self._send_frame(protocol.MessageType.TELEMETRY_ALERT, {
                        'protocolVersion': protocol.PROTOCOL_VERSION,
                        'msgType': 'SafetyAlert',
                        'active': alert.active,
                        'severity': alert.severity,
                        'text1': alert.text1,
                        'text2': alert.text2,
                    })
            except Exception as e:
                logger.debug('%s: alert telem error: %s', self._name, e)

        # Blind-spot feed — fused decision from controlsd (ignition_on only)
        # plus raw corner presence from radar2d (always_run). Emitted at
        # telemetry cadence whenever either source updates. Parked /
        # walk-around mode: blindSpotAlert stale → valid=false, decision
        # fields nulled, radarPresence still sent (see build_blindspot_payload).
        if self.sm.updated.get('blindSpotAlert') or self.sm.updated.get('radar2d'):
            try:
                decision_valid = bool(self.sm.valid['blindSpotAlert'] and
                                      self.sm.alive['blindSpotAlert'])
                bsa = self.sm['blindSpotAlert'] if decision_valid else None
                radar2d = self.sm['radar2d'] if self.sm.alive.get('radar2d') else None
                self._send_frame(protocol.MessageType.BLIND_SPOT, {
                    'protocolVersion': protocol.PROTOCOL_VERSION,
                    'msgType': 'BlindSpot',
                    **build_blindspot_payload(bsa, radar2d, decision_valid),
                })
            except Exception as e:
                logger.debug('%s: blindspot telem error: %s', self._name, e)

        # Radar pairing service tool — self-throttling (1 Hz open / transitions)
        self._maybe_send_radar_pair_status()
