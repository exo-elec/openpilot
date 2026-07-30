#!/usr/bin/env python3
"""
MCAPD - Parallel MCAP Logger with Real-Time Streaming

Mode A: Parallel file logging + optional real-time streaming
  rlog ──▶ loggerd (stores rlog.zst)
       └──▶ mcapd ──┬──▶ data.mcap (Foxglove file)
                    └──▶ WebSocket (real-time stream)

Features:
  - Reads live cereal messages (like loggerd)
  - Writes MCAP file (parallel to rlog)
  - Optional WebSocket streaming (like PlotJuggler)
  - Foxglove-native JSON format

Output:
  /data/media/0/mcap/<route>--<segment>/data.mcap
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog

try:
    from mcap.writer import Writer
    MCAP_AVAILABLE = True
except ImportError:
    MCAP_AVAILABLE = False
    cloudlog.warning("mcapd: mcap module not available")

try:
    import websockets
    from websockets.server import serve
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False


# ============================================================================
# Configuration
# ============================================================================

MCAP_ROOT = Path(os.getenv("MCAP_ROOT", "/data/media/0/mcap"))
SEGMENT_LENGTH_SEC = int(os.getenv("MCAP_SEGMENT_LENGTH", "60"))
WEBSOCKET_PORT = int(os.getenv("MCAPD_WS_PORT", "8765"))
ENABLE_WEBSOCKET = os.getenv("MCAPD_ENABLE_WS", "1") == "1"

MCAP_TEST = os.getenv("MCAPD_TEST") is not None
if MCAP_TEST:
    SEGMENT_LENGTH_SEC = 10

# Services: (name, topic, decimation, converter)
MCAP_SERVICES = [
    ('carState', '/car/state', 1, 'carState'),
    ('carControl', '/car/control', 1, 'controlsState'),
    ('controlsState', '/controls/state', 1, 'controlsState'),
    ('livePose', '/pose', 4, 'livePose'),
    ('gpsLocationExternal', '/gps', 10, 'gps'),
    ('deviceState', '/device/state', 2, 'deviceState'),
    ('modelV2', '/perception/model', 1, 'modelV2'),
]


# ============================================================================
# Converters
# ============================================================================

def convert_car_state(cs) -> dict:
    return {
        "timestamp": cs.logMonoTime / 1e9,
        "speed_mps": cs.vEgo,
        "steering_angle_deg": cs.steeringAngleDeg,
        "gas_pressed": cs.gasPressed,
        "brake_pressed": cs.brakePressed,
        "gear": str(cs.gearShifter),
        "left_blinker": cs.leftBlinker,
        "right_blinker": cs.rightBlinker,
        "v_ego": cs.vEgo,
        "a_ego": cs.aEgo,
    }

def convert_controls_state(cs) -> dict:
    return {
        "timestamp": cs.logMonoTime / 1e9,
        "active": cs.active,
        "state": str(cs.state),
        "v_cruise": cs.vCruise,
        "curvature": cs.curvature,
        "steer_override": cs.steerOverride,
        "enabled": cs.enabled,
    }

def convert_live_pose(lp) -> dict:
    return {
        "timestamp": lp.logMonoTime / 1e9,
        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
        "orientation": {
            "x": getattr(lp.orientationNED, 'x', 0),
            "y": getattr(lp.orientationNED, 'y', 0),
            "z": getattr(lp.orientationNED, 'z', 0),
            "w": 1.0,
        },
        "velocity": {
            "x": getattr(lp.velocity, 'x', 0),
            "y": getattr(lp.velocity, 'y', 0),
            "z": getattr(lp.velocity, 'z', 0),
        },
    }

def convert_gps(gps) -> dict:
    return {
        "timestamp": gps.logMonoTime / 1e9,
        "latitude": gps.latitude,
        "longitude": gps.longitude,
        "altitude": gps.altitude,
        "status": 1 if gps.flags % 2 == 1 else 0,
        "accuracy": gps.accuracy,
    }

def convert_device_state(ds) -> dict:
    return {
        "timestamp": ds.logMonoTime / 1e9,
        "cpu_temp_c": list(ds.cpuTempC)[:4] if hasattr(ds, 'cpuTempC') else [],
        "memory_usage_percent": getattr(ds, 'memoryUsagePercent', 0),
        "cpu_usage_percent": getattr(ds, 'cpuUsagePercent', 0),
    }

def convert_model_v2(m) -> dict:
    return {
        "timestamp": m.logMonoTime / 1e9,
        "lane_lines": len(m.laneLines) if hasattr(m, 'laneLines') else 0,
    }

CONVERTERS = {
    'carState': convert_car_state,
    'controlsState': convert_controls_state,
    'livePose': convert_live_pose,
    'gps': convert_gps,
    'deviceState': convert_device_state,
    'modelV2': convert_model_v2,
}


# ============================================================================
# WebSocket Server for Real-Time Streaming
# ============================================================================

class FoxgloveWebSocketServer:
    """WebSocket server for real-time Foxglove streaming."""
    
    def __init__(self, port: int = WEBSOCKET_PORT):
        self.port = port
        self.clients: set[websockets.WebSocketServerProtocol] = set()
        self.running = False
        self.channels: dict[str, int] = {}
        self._build_channels()
        
    def _build_channels(self):
        """Build channel definitions."""
        for i, (service, topic, _, _) in enumerate(MCAP_SERVICES):
            self.channels[topic] = i
    
    async def handle_client(self, websocket, path):
        """Handle client connection."""
        self.clients.add(websocket)
        addr = websocket.remote_address
        cloudlog.info(f"Foxglove client connected: {addr}")
        
        try:
            # Send server info
            await websocket.send(json.dumps({
                "op": "serverInfo",
                "name": "mcapd",
                "capabilities": [],
            }))
            
            # Send channel advertisements
            for service, topic, _, _ in MCAP_SERVICES:
                await websocket.send(json.dumps({
                    "op": "advertise",
                    "channels": [{
                        "id": self.channels[topic],
                        "topic": topic,
                        "encoding": "json",
                        "schemaName": f"foxglove.{service}",
                    }],
                }))
            
            # Keep alive
            while self.running and websocket.open:
                await asyncio.sleep(1)
                
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            cloudlog.info(f"Foxglove client disconnected: {addr}")
    
    async def broadcast(self, topic: str, data: dict, timestamp_ns: int):
        """Broadcast message to all clients."""
        if not self.clients:
            return
        
        chan_id = self.channels.get(topic)
        if chan_id is None:
            return
        
        msg = {
            "op": "message",
            "channelId": chan_id,
            "timestamp": timestamp_ns,
            "data": json.dumps(data),
        }
        
        disconnected = set()
        for client in self.clients:
            try:
                await client.send(json.dumps(msg))
            except Exception:
                disconnected.add(client)
        
        self.clients -= disconnected
    
    async def run(self):
        """Run WebSocket server."""
        if not WEBSOCKET_AVAILABLE:
            cloudlog.warning("WebSocket not available")
            return
        
        self.running = True
        async with serve(self.handle_client, "0.0.0.0", self.port):
            cloudlog.info(f"WebSocket server on ws://0.0.0.0:{self.port}")
            while self.running:
                await asyncio.sleep(1)
    
    def stop(self):
        self.running = False


# ============================================================================
# MCAP File Writer
# ============================================================================

@dataclass
class MCAPSegment:
    route_name: str
    segment_num: int
    file_path: Path
    writer: Writer | None = None
    file_handle: Any | None = None
    channels: dict[str, int] = field(default_factory=dict)
    start_time: float = 0.0
    msg_count: int = 0
    
    def __post_init__(self):
        self.start_time = time.monotonic()


class MCAPFileWriter:
    """Writes MCAP files."""
    
    def __init__(self, output_dir: Path = MCAP_ROOT):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.segment: MCAPSegment | None = None
        self.route_name: str = ""
        self.segment_num = 0
        
    def generate_route_name(self) -> str:
        """Get or generate route name."""
        params = Params()
        route = params.get("CurrentRoute")
        if route:
            return route.decode() if isinstance(route, bytes) else route
        
        now = datetime.now()
        rand = os.urandom(4).hex()[:8]
        return f"{now.strftime('%Y-%m-%d--%H-%M-%S')}--{rand}"
    
    def start_route(self) -> bool:
        """Start new route."""
        self.route_name = self.generate_route_name()
        self.segment_num = 0
        cloudlog.info(f"MCAP: Route {self.route_name}")
        return self.rotate_segment()
    
    def rotate_segment(self) -> bool:
        """Rotate segment."""
        if self.segment:
            self._close()
        
        segment_dir = self.output_dir / f"{self.route_name}--{self.segment_num}"
        segment_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = segment_dir / "data.mcap"
        
        try:
            f = open(file_path, "wb")
            writer = Writer(f)
            writer.start()
            
            self.segment = MCAPSegment(
                route_name=self.route_name,
                segment_num=self.segment_num,
                file_path=file_path,
                writer=writer,
                file_handle=f
            )
            
            # Register channels
            schema_id = 1
            for service, topic, _, _ in MCAP_SERVICES:
                writer.register_schema(
                    name=f"foxglove.{service}",
                    encoding="jsonschema",
                    data=json.dumps({"type": "object"}).encode()
                )
                chan_id = writer.register_channel(
                    schema_id=schema_id,
                    topic=topic,
                    message_encoding="json"
                )
                self.segment.channels[service] = chan_id
                schema_id += 1
            
            # Metadata
            meta_schema = writer.register_schema(name="Metadata", encoding="json", data=b"{}")
            meta_chan = writer.register_channel(schema_id=meta_schema, topic="/metadata", message_encoding="json")
            ts = int(time.monotonic() * 1e9)
            writer.write_message(
                channel_id=meta_chan,
                log_time=ts,
                data=json.dumps({"route": self.route_name, "segment": self.segment_num}).encode(),
                publish_time=ts
            )
            
            cloudlog.info(f"MCAP: Segment {self.segment_num}: {file_path}")
            self.segment_num += 1
            return True
            
        except Exception as e:
            cloudlog.error(f"MCAP: Failed: {e}")
            return False
    
    def write(self, service: str, data: dict, timestamp_ns: int):
        """Write message."""
        if not self.segment:
            return
        
        chan_id = self.segment.channels.get(service)
        if chan_id is None:
            return
        
        self.segment.writer.write_message(
            channel_id=chan_id,
            log_time=timestamp_ns,
            data=json.dumps(data).encode(),
            publish_time=timestamp_ns
        )
        self.segment.msg_count += 1
    
    def _close(self):
        if self.segment:
            try:
                if self.segment.writer:
                    self.segment.writer.finish()
                if self.segment.file_handle:
                    self.segment.file_handle.close()
                cloudlog.info(f"MCAP: Closed seg {self.segment.segment_num} ({self.segment.msg_count} msgs)")
            except Exception as e:
                cloudlog.error(f"MCAP: Close error: {e}")
            self.segment = None
    
    def close(self):
        self._close()
    
    def should_rotate(self) -> bool:
        if not self.segment:
            return True
        return (time.monotonic() - self.segment.start_time) >= SEGMENT_LENGTH_SEC


# ============================================================================
# MCAPD - Main Daemon
# ============================================================================

class MCAPD:
    """MCAP daemon - parallel logging with optional real-time streaming."""
    
    def __init__(self, enable_websocket: bool = ENABLE_WEBSOCKET):
        self.params = Params()
        self.file_writer = MCAPFileWriter()
        self.enable_websocket = enable_websocket
        self.ws_server: FoxgloveWebSocketServer | None = None
        
        # Subscribe to services
        services = [s[0] for s in MCAP_SERVICES]
        self.sm = messaging.SubMaster(services)
        
        # Decimation counters
        self.counters = defaultdict(int)
        
        # Stats
        self.msg_count = 0
        self.start_time = time.monotonic()
        self.running = False
        
        cloudlog.info(f"MCAPD initialized (WebSocket: {enable_websocket})")
    
    async def run_async(self):
        """Async main loop."""
        if not MCAP_AVAILABLE:
            cloudlog.error("MCAPD: mcap not available")
            return
        
        self.running = True
        
        # Start file writer
        if not self.file_writer.start_route():
            cloudlog.error("MCAPD: Failed to start")
            return
        
        self.params.put("CurrentMCAPRoute", self.file_writer.route_name)
        
        # Start WebSocket if enabled
        tasks = []
        if self.enable_websocket and WEBSOCKET_AVAILABLE:
            self.ws_server = FoxgloveWebSocketServer()
            tasks.append(asyncio.create_task(self.ws_server.run()))
        
        # Main processing loop
        tasks.append(asyncio.create_task(self._processing_loop()))
        
        await asyncio.gather(*tasks)
    
    async def _processing_loop(self):
        """Process messages."""
        rk = Ratekeeper(20.0)
        
        while self.running:
            self.sm.update()
            
            # Check rotation
            if self.file_writer.should_rotate():
                self.file_writer.rotate_segment()
            
            # Process messages
            for service, topic, decimation, converter_name in MCAP_SERVICES:
                if self.sm.updated[service]:
                    self.counters[service] += 1
                    
                    if decimation == 1 or self.counters[service] % decimation == 0:
                        msg = self.sm[service]
                        
                        # Convert
                        converter = CONVERTERS.get(converter_name) if converter_name else None
                        if converter:
                            try:
                                data = converter(msg)
                            except Exception:
                                continue
                        else:
                            data = {"timestamp": msg.logMonoTime / 1e9}
                        
                        timestamp_ns = int(data['timestamp'] * 1e9)
                        
                        # Write to file
                        self.file_writer.write(service, data, timestamp_ns)
                        
                        # Broadcast to WebSocket clients
                        if self.ws_server:
                            await self.ws_server.broadcast(topic, data, timestamp_ns)
                        
                        self.msg_count += 1
            
            if self.msg_count % 10000 == 0 and self.msg_count > 0:
                elapsed = time.monotonic() - self.start_time
                cloudlog.debug(f"MCAPD: {self.msg_count} msgs, {self.msg_count/elapsed:.1f}/sec")
            
            rk.keep_time()
    
    def shutdown(self):
        cloudlog.info("MCAPD: Shutting down")
        self.running = False
        self.file_writer.close()
        if self.ws_server:
            self.ws_server.stop()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='MCAPD - Parallel MCAP logging')
    parser.add_argument('--no-websocket', action='store_true', help='Disable WebSocket')
    parser.add_argument('--port', type=int, default=WEBSOCKET_PORT, help='WebSocket port')
    args = parser.parse_args()
    
    enable_ws = not args.no_websocket
    
    daemon = MCAPD(enable_websocket=enable_ws)
    
    def signal_handler(signum, frame):
        daemon.shutdown()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    asyncio.run(daemon.run_async())


if __name__ == "__main__":
    main()
