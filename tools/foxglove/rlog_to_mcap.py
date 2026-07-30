#!/usr/bin/env python3
"""
RLOG to MCAP Converter

Converts openpilot rlogs (raw cereal format) to Foxglove-compatible MCAP.

Usage:
    python rlog_to_mcap.py <rlog_file> -o output.mcap
    python rlog_to_mcap.py <route_name> -o output.mcap

Examples:
    python rlog_to_mcap.py /data/media/0/realdata/2026-04-07--12-00-00--123/rlog.zst
    python rlog_to_mcap.py a2a0ccea32023010|2023-07-27--13-01-19
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from openpilot.tools.lib.logreader import LogReader

try:
    from mcap.writer import Writer
    MCAP_AVAILABLE = True
except ImportError:
    MCAP_AVAILABLE = False
    print("Error: pip install mcap")
    sys.exit(1)


# ============================================================================
# Message Converters (Cereal → Foxglove JSON)
# ============================================================================

def convert_car_state(cs) -> Dict:
    """carState → /car/state"""
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
    """controlsState → /controls/state"""
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
    """livePose → /pose (Foxglove Pose format)"""
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
    """gpsLocationExternal → /gps (NavSatFix-like)"""
    return {
        "timestamp": gps.logMonoTime / 1e9,
        "latitude": gps.latitude,
        "longitude": gps.longitude,
        "altitude": gps.altitude,
        "status": 1 if gps.flags % 2 == 1 else 0,
        "accuracy": gps.accuracy,
    }

def convert_device_state(ds) -> Dict:
    """deviceState → /device/state"""
    return {
        "timestamp": ds.logMonoTime / 1e9,
        "cpu_temp_c": list(ds.cpuTempC) if hasattr(ds, 'cpuTempC') else [],
        "memory_usage_percent": getattr(ds, 'memoryUsagePercent', 0),
        "cpu_usage_percent": getattr(ds, 'cpuUsagePercent', 0),
    }


# Registry: cereal_type -> (topic, converter)
CONVERTERS = {
    'carState': ('/car/state', convert_car_state),
    'carControl': ('/car/control', convert_controls_state),
    'controlsState': ('/controls/state', convert_controls_state),
    'livePose': ('/pose', convert_live_pose),
    'gpsLocationExternal': ('/gps', convert_gps),
    'deviceState': ('/device/state', convert_device_state),
}


# ============================================================================
# MCAP Converter
# ============================================================================

@dataclass
class RlogToMcapConverter:
    """Converts rlog to Foxglove-native MCAP."""
    output_path: Path
    
    def __post_init__(self):
        self.output_path = Path(self.output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._file = open(self.output_path, "wb")
        self._writer = Writer(self._file)
        self._writer.start()
        
        self._channels: Dict[str, int] = {}
        self._register_schemas()
        
        print(f"Output: {self.output_path}")
    
    def _register_schemas(self):
        """Register Foxglove JSON schemas."""
        for cereal_type, (topic, _) in CONVERTERS.items():
            schema_json = json.dumps({"type": "object"})
            schema_id = self._writer.register_schema(
                name=f"foxglove.{cereal_type}",
                encoding="jsonschema",
                data=schema_json.encode()
            )

            channel_id = self._writer.register_channel(
                schema_id=schema_id,
                topic=topic,
                message_encoding="json"
            )
            self._channels[cereal_type] = channel_id
    
    def convert_rlog(self, rlog_path: str):
        """Convert rlog file."""
        print(f"Reading: {rlog_path}")
        
        lr = LogReader(rlog_path)
        count = 0
        converted = 0
        
        for msg in lr:
            count += 1
            which = msg.which()
            
            if which in CONVERTERS:
                topic, converter = CONVERTERS[which]
                data = converter(getattr(msg, which))
                
                channel_id = self._channels[which]
                timestamp_ns = msg.logMonoTime
                
                self._writer.write_message(
                    channel_id=channel_id,
                    log_time=timestamp_ns,
                    data=json.dumps(data).encode(),
                    publish_time=timestamp_ns
                )
                converted += 1
            
            if count % 10000 == 0:
                print(f"  Processed {count} messages, converted {converted}")
        
        print(f"Done: {count} messages read, {converted} converted")
    
    def close(self):
        if self._writer:
            self._writer.finish()
            self._file.close()
            print(f"Saved: {self.output_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Convert rlog to MCAP')
    parser.add_argument('rlog', help='rlog file or route name')
    parser.add_argument('-o', '--output', help='Output MCAP path')
    args = parser.parse_args()
    
    # Default output path
    if args.output:
        output = args.output
    else:
        base = Path(args.rlog).stem.replace('.zst', '').replace('.bz2', '')
        output = f"{base}.mcap"
    
    converter = RlogToMcapConverter(output)
    try:
        converter.convert_rlog(args.rlog)
    finally:
        converter.close()


if __name__ == '__main__':
    main()
