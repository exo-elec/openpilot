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

    def __init__(self, params: Params | None = None):
        self.params = params or (Params() if Params else None)
        self._name = 'ncp'  # used in log messages only

        # Active transports: name → thread-safe send_fn
        # Key = transport name ('spp', 'gatt'); removed on disconnect.
        self._transports: dict[str, Callable[[bytes], None]] = {}
        self._transport_lock = threading.Lock()

        # Protocol state (shared across transports — one logical session)
        self._is_paired: bool = False
        self._last_route_id: int | None = None
        self._last_oauth_token: str = ''

        # ONE pm/sm for the whole process — msgq rejects multiple publishers
        if messaging:
            self.pm = messaging.PubMaster(['obdCommand', 'voiceCommandRequest', 'ncpVehicleData'])
            self.sm = messaging.SubMaster([
                'carState', 'navInstruction', 'navRoute', 'obdState',
                'selfdriveState', 'controlsState', 'deviceState', 'alertDebug',
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
        try:
            data = frame.to_json()
            token = data.get('token', '')
            email = data.get('email', '')
            if token and self.params:
                self.params.put('NavPilotOAuthToken', token)
                self.params.put('NavPilotOAuthEmail', email)
                logger.info('%s: auth handshake for %s', self._name, email)
            return protocol.make_ack(protocol.MessageType.CMD_AUTH_HANDSHAKE)
        except Exception as e:
            return protocol.make_error(f'Auth handshake failed: {e}')

    def _handle_oauth_token(self, frame: protocol.Frame) -> protocol.Frame:
        try:
            data = frame.to_json()
            token = data.get('accessToken', '')
            email = data.get('email', '')
            if token and self.params:
                self._last_oauth_token = token
                self.params.put('NavPilotOAuthToken', token)
                self.params.put('NavPilotOAuthEmail', email)
                logger.info('%s: OAuth token stored for %s', self._name, email)
            return protocol.make_ack(protocol.MessageType.CMD_OAUTH_TOKEN)
        except Exception as e:
            return protocol.make_error(f'OAuth token failed: {e}')

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
