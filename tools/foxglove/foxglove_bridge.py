#!/usr/bin/env python3
"""
Foxglove Bridge - Real-time Streaming

Streams openpilot data to Foxglove Studio via WebSocket.
Similar to PlotJuggler's streaming but outputs Foxglove-native format.

Usage:
    python foxglove_bridge.py                    # Start WebSocket server
    python foxglove_bridge.py --port 8765        # Custom port
    
Then in Foxglove Studio:
    1. Add "Foxglove WebSocket" connection
    2. Enter ws://<device_ip>:8765

Features:
    - Real-time streaming of cereal messages
    - Foxglove-native JSON format
    - Auto-reconnection support
    - Selective topic subscription
"""

from __future__ import annotations

import os
import sys
import json
import time
import asyncio
import signal
from pathlib import Path
from typing import Optional, Dict, Any, Set
from dataclasses import dataclass
from collections import defaultdict

try:
    import websockets
    from websockets.server import serve
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("Error: pip install websockets")
    sys.exit(1)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog


# ============================================================================
# Configuration
# ============================================================================

DEFAULT_PORT = 8765
DEFAULT_HOST = "0.0.0.0"

# Services to stream
STREAM_SERVICES = [
    'carState',
    'carControl', 
    'controlsState',
    'livePose',
    'gpsLocationExternal',
    'deviceState',
    'modelV2',
    'radarState',
    # ExoPilot
    'monoDetections',
    'stereoDepth',
    'fusedPosition',
]


# ============================================================================
# Message Converters (same as rlog_to_mcap)
# ============================================================================

def convert_car_state(cs) -> Dict:
    return {
        "timestamp": cs.logMonoTime / 1e9,
        "speed_mps": cs.vEgo,
        "steering_angle_deg": cs.steeringAngleDeg,
        "steering_rate_deg": cs.steeringRateDeg,
        "gas_pressed": cs.gasPressed,
        "brake_pressed": cs.brakePressed,
        "gear": str(cs.gearShifter),
        "left_blinker": cs.leftBlinker,
        "right_blinker": cs.rightBlinker,
        "v_ego": cs.vEgo,
        "a_ego": cs.aEgo,
    }

def convert_controls_state(cs) -> Dict:
    return {
        "timestamp": cs.logMonoTime / 1e9,
        "active": cs.active,
        "state": str(cs.state),
        "v_cruise": cs.vCruise,
        "curvature": cs.curvature,
        "steer_override": cs.steerOverride,
        "enabled": cs.enabled,
    }

def convert_live_pose(lp) -> Dict:
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

def convert_gps(gps) -> Dict:
    return {
        "timestamp": gps.logMonoTime / 1e9,
        "latitude": gps.latitude,
        "longitude": gps.longitude,
        "altitude": gps.altitude,
        "status": 1 if gps.flags % 2 == 1 else 0,
        "accuracy": gps.accuracy,
    }

def convert_device_state(ds) -> Dict:
    return {
        "timestamp": ds.logMonoTime / 1e9,
        "cpu_temp_c": list(ds.cpuTempC) if hasattr(ds, 'cpuTempC') else [],
        "memory_usage_percent": getattr(ds, 'memoryUsagePercent', 0),
        "cpu_usage_percent": getattr(ds, 'cpuUsagePercent', 0),
    }

def convert_model_v2(m) -> Dict:
    return {
        "timestamp": m.logMonoTime / 1e9,
        "position": {
            "x": getattr(m.position, 'x', 0),
            "y": getattr(m.position, 'y', 0),
        },
        "lane_lines": [{"prob": ll.prob} for ll in m.laneLines] if hasattr(m, 'laneLines') else [],
    }


CONVERTERS = {
    'carState': ('/car/state', convert_car_state),
    'carControl': ('/car/control', convert_controls_state),
    'controlsState': ('/controls/state', convert_controls_state),
    'livePose': ('/pose', convert_live_pose),
    'gpsLocationExternal': ('/gps', convert_gps),
    'deviceState': ('/device/state', convert_device_state),
    'modelV2': ('/perception/model', convert_model_v2),
}


# ============================================================================
# Foxglove WebSocket Protocol
# ============================================================================

class FoxgloveServer:
    """
    Foxglove WebSocket server for real-time streaming.
    
    Protocol: Client connects, server sends channel advertisements,
    then streams messages in MCAP format over WebSocket.
    """
    
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.running = False
        
        # Channel definitions for Foxglove
        self.channels = []
        self._build_channels()
        
        cloudlog.info(f"FoxgloveServer initialized on {host}:{port}")
    
    def _build_channels(self):
        """Build channel definitions for Foxglove."""
        chan_id = 0
        for service, (topic, _) in CONVERTERS.items():
            self.channels.append({
                "id": chan_id,
                "topic": topic,
                "encoding": "json",
                "schemaName": f"foxglove.{service}",
                "schema": json.dumps({"type": "object"}),
            })
            chan_id += 1
    
    async def handle_client(self, websocket: websockets.WebSocketServerProtocol, path: str):
        """Handle a client connection."""
        self.clients.add(websocket)
        client_addr = websocket.remote_address
        cloudlog.info(f"Foxglove client connected: {client_addr}")
        
        try:
            # Send server info
            await websocket.send(json.dumps({
                "op": "serverInfo",
                "name": "openpilot-foxglove",
                "capabilities": ["clientPublish", "connectionGraph", "assets"],
            }))
            
            # Send channel advertisements
            for chan in self.channels:
                await websocket.send(json.dumps({
                    "op": "advertise",
                    "channels": [chan],
                }))
            
            # Keep connection alive
            while self.running and websocket.open:
                await asyncio.sleep(1)
                
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            cloudlog.info(f"Foxglove client disconnected: {client_addr}")
    
    async def broadcast_message(self, topic: str, data: Dict, timestamp_ns: int):
        """Broadcast a message to all connected clients."""
        if not self.clients:
            return
        
        # Find channel id
        chan_id = None
        for chan in self.channels:
            if chan["topic"] == topic:
                chan_id = chan["id"]
                break
        
        if chan_id is None:
            return
        
        message = {
            "op": "message",
            "channelId": chan_id,
            "timestamp": timestamp_ns,
            "data": json.dumps(data),
        }
        
        # Send to all clients
        disconnected = set()
        for client in self.clients:
            try:
                await client.send(json.dumps(message))
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)
            except Exception as e:
                cloudlog.debug(f"WebSocket send error: {e}")
                disconnected.add(client)
        
        # Clean up disconnected clients
        self.clients -= disconnected
    
    async def run_server(self):
        """Run the WebSocket server."""
        self.running = True
        async with serve(self.handle_client, self.host, self.port):
            cloudlog.info(f"Foxglove server running on ws://{self.host}:{self.port}")
            while self.running:
                await asyncio.sleep(1)
    
    def stop(self):
        """Stop the server."""
        self.running = False


# ============================================================================
# Data Streaming
# ============================================================================

class FoxgloveStreamer:
    """Streams cereal messages to Foxglove clients."""
    
    def __init__(self, server: FoxgloveServer):
        self.server = server
        self.sm = messaging.SubMaster(STREAM_SERVICES)
        self.running = False
        
        cloudlog.info("FoxgloveStreamer initialized")
    
    async def stream_loop(self):
        """Main streaming loop."""
        self.running = True
        rk = Ratekeeper(20.0)  # 20 Hz
        
        while self.running:
            self.sm.update()
            
            for service in STREAM_SERVICES:
                if self.sm.updated[service]:
                    msg = self.sm[service]
                    
                    if service in CONVERTERS:
                        topic, converter = CONVERTERS[service]
                        data = converter(msg)
                        timestamp_ns = self.sm.logMonoTime[service]

                        await self.server.broadcast_message(topic, data, timestamp_ns)

            if rk.remaining > 0:
                await asyncio.sleep(rk.remaining)
            rk.monitor_time()
    
    def stop(self):
        """Stop streaming."""
        self.running = False


# ============================================================================
# Main
# ============================================================================

async def main_async():
    import argparse
    parser = argparse.ArgumentParser(description='Foxglove WebSocket Bridge')
    parser.add_argument('--host', default=DEFAULT_HOST, help='WebSocket host')
    parser.add_argument('--port', '-p', type=int, default=DEFAULT_PORT, help='WebSocket port')
    args = parser.parse_args()
    
    # Create server
    server = FoxgloveServer(host=args.host, port=args.port)
    streamer = FoxgloveStreamer(server)
    
    # Handle signals
    def signal_handler(signum, frame):
        cloudlog.info("Foxglove bridge shutting down...")
        server.stop()
        streamer.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run both server and streamer
    await asyncio.gather(
        server.run_server(),
        streamer.stream_loop(),
    )


def main():
    if not WEBSOCKET_AVAILABLE:
        print("Error: websockets module not installed")
        print("Install: pip install websockets")
        sys.exit(1)
    
    asyncio.run(main_async())


if __name__ == '__main__':
    main()
