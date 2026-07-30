#!/usr/bin/env python3
"""
RecordD - Unified Recording Daemon (DVR + Impact + Snap)

Consolidates VisionPilot's loopd, impactd, and snapd into a single daemon.

Features:
  - Loop Recording: Continuous circular buffer with H264 hardware encoding (patent-free)
  - Impact Detection: IMU-based crash detection with configurable sensitivity (0-100)
  - Snap Capture: On-demand snapshots with IMU history
  - Parking Mode: Timelapse (1fps) when ignition OFF, with wake lock management
  - Pre-impact Buffer: Always maintains video in RAM for impact events
  - Event Protection: Save clips on impact detection with pre/post buffer
  - UI Control: Full parameter control via services

Recording Modes:
    - OFF: Not recording
    - NORMAL: Standard recording while onroad (20fps)
    - TIMELAPSE: Parking mode recording (1fps)
    - EVENT: High-quality recording after trigger
    - IMPACT: Emergency recording with pre/post buffer

VisionPilot Feature Parity:
    ✓ Sensitivity 0-100 scale with interpolation
    ✓ Pre/post buffer configuration
    ✓ Parking mode with duration limits
    ✓ Clip info tracking (normal/event/timelapse/impact)
    ✓ Storage info reporting
    ✓ Manual event marking
    ✓ IMU history in snapshots
    ✓ Auto-trigger on high G-force
    ✓ Cooldown system
    ✓ Wake lock management

Architecture:
  ┌─────────────────────────────────────────────────────────────┐
  │                        RecordD                               │
  ├─────────────────────────────────────────────────────────────┤
  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
  │  │ Loop Record │  │Impact Detect│  │ Snap Capture│         │
  │  │  (DVR)      │  │  (IMU/G)    │  │  (On-demand)│         │
  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘         │
  │         │                │                │                 │
  │         └────────────────┴────────────────┘                 │
  │                          │                                  │
  │                   ┌──────┴──────┐                          │
  │                   │  Pre-impact │  ← Always buffering       │
  │                   │    Buffer   │     15s in RAM            │
  │                   └──────┬──────┘                          │
  │                          │                                  │
  │                   ┌──────┴──────┐                          │
  │                   │    MPP      │  ← Hardware H264          │
  │                   │  Encoder    │                          │
  │                   └─────────────┘                          │
  └─────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import os
import sys
import time
import json
import signal
import struct
import logging
import threading
import subprocess
from pathlib import Path
from enum import Enum
from dataclasses import dataclass, field, asdict
from collections import deque
from typing import Any
from collections.abc import Callable
from datetime import datetime

import numpy as np

import cereal.messaging as messaging
from msgq.visionipc import VisionStreamType, VisionIpcClient
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import PC, ROCKCHIP
from openpilot.system.hardware.hw import Paths

# ============================================================================
# Configuration
# ============================================================================

RECORD_ROOT = Path(os.getenv("RECORD_ROOT", os.path.join(Paths.eop_data_root(), "media", "0", "dashcam")))

# VisionIPC server mapping — zero backward compatibility, canonical names only
STREAM_SERVERS: dict[VisionStreamType, str] = {
    VisionStreamType.VISION_STREAM_ROAD:         "v4l2d",
    VisionStreamType.VISION_STREAM_WIDE_ROAD:    "v4l2d",
    VisionStreamType.VISION_STREAM_TELE_ROAD:    "v4l2d",
    VisionStreamType.VISION_STREAM_STEREO_LEFT:  "v4l2d",
    VisionStreamType.VISION_STREAM_STEREO_RIGHT: "v4l2d",
    VisionStreamType.VISION_STREAM_STEREO_DEPTH: "v4l2d",
    VisionStreamType.VISION_STREAM_DRIVER:       "uvcd",
    VisionStreamType.VISION_STREAM_SIDE_LEFT:    "uvcd",
    VisionStreamType.VISION_STREAM_SIDE_RIGHT:   "uvcd",
}

# Recording settings
NORMAL_FPS = int(os.getenv("RECORD_NORMAL_FPS", "20"))
PARKING_FPS = int(os.getenv("RECORD_PARKING_FPS", "1"))
SEGMENT_DURATION_S = int(os.getenv("RECORD_SEGMENT_S", "60"))
MAX_STORAGE_GB = float(os.getenv("RECORD_MAX_GB", "8.0"))

# Pre-impact buffer (always recording to RAM)
PRE_BUFFER_SECONDS = int(os.getenv("RECORD_PRE_BUFFER_S", "15"))
POST_BUFFER_SECONDS = int(os.getenv("RECORD_POST_BUFFER_S", "30"))

# Impact detection settings
IMPACT_COOLDOWN_S = 5.0
IMPACT_MIN_DURATION_MS = 50

# Snap settings  
SNAP_IMU_HISTORY_MS = 500  # IMU history to save with snapshot (500ms default)

# Quality presets (width, height, fps, bitrate_kbps)
QUALITY_PRESETS = {
    "low": {"width": 1280, "height": 720, "fps": 15, "bitrate": 2000},
    "medium": {"width": 1920, "height": 1080, "fps": 20, "bitrate": 4000},
    "high": {"width": 2560, "height": 1440, "fps": 25, "bitrate": 8000},
}

# Event quality (higher for impacts)
EVENT_QUALITY = {"width": 2560, "height": 1440, "fps": 30, "bitrate": 8000}
TIMELAPSE_QUALITY = {"width": 1280, "height": 720, "fps": 1, "bitrate": 1000}

# Sensitivity mapping to G-force threshold (0-100 scale)
SENSITIVITY_THRESHOLDS = {
    0: 4.0,     # Low - only severe impacts
    25: 3.0,
    50: 2.0,    # Medium (default)
    75: 1.5,
    100: 1.0,   # High - minor bumps
}


# ============================================================================
# Data Structures
# ============================================================================

class RecordingMode(Enum):
    """DVR recording modes."""
    OFF = "off"
    NORMAL = "normal"
    TIMELAPSE = "timelapse"
    EVENT = "event"
    IMPACT = "impact"


class ClipType(Enum):
    """Types of recorded clips."""
    NORMAL = "normal"
    EVENT = "event"
    TIMELAPSE = "timelapse"
    IMPACT = "impact"


@dataclass
class ClipInfo:
    """Information about a recorded clip."""
    filename: str
    timestamp: str
    duration_sec: float
    size_bytes: int
    clip_type: str = "normal"
    is_event: bool = False
    is_preserved: bool = False
    event_reason: str = ""
    impact_level: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            'filename': self.filename,
            'timestamp': self.timestamp,
            'duration_sec': round(self.duration_sec, 1),
            'size_mb': round(self.size_bytes / (1024**2), 2),
            'type': self.clip_type,
            'preserved': self.is_preserved,
            'event_reason': self.event_reason,
            'impact_level': round(self.impact_level, 2),
        }


@dataclass
class StorageInfo:
    """Storage usage information."""
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    clips_count: int = 0
    events_count: int = 0
    impact_count: int = 0
    total_duration_hours: float = 0.0
    recording_mode: str = "off"
    impact_sensitivity: int = 50

    def to_dict(self) -> dict[str, Any]:
        return {
            'total_gb': round(self.total_bytes / (1024**3), 2),
            'used_gb': round(self.used_bytes / (1024**3), 2),
            'free_gb': round(self.free_bytes / (1024**3), 2),
            'used_percent': round(self.used_bytes / self.total_bytes * 100, 1) if self.total_bytes > 0 else 0,
            'clips_count': self.clips_count,
            'events_count': self.events_count,
            'impact_count': self.impact_count,
            'total_duration_hours': round(self.total_duration_hours, 1),
            'recording_mode': self.recording_mode,
            'impact_sensitivity': self.impact_sensitivity,
        }


@dataclass
class RecordingSettings:
    """Recording settings - UI configurable."""
    enabled: bool = True
    quality: str = "medium"
    segment_duration_sec: int = 60
    total_duration_min: int = 60
    auto_start: bool = True
    auto_delete: bool = True
    
    # Impact detection
    impact_sensitivity: int = 50  # 0-100, UI configurable
    impact_pre_buffer_sec: float = 10.0
    impact_post_buffer_sec: float = 30.0
    impact_cooldown_sec: float = 5.0
    
    # Parking mode
    parking_mode_enabled: bool = True
    parking_duration_hours: float = 12.0
    timelapse_fps: int = 1
    
    # Snap
    snap_imu_history_ms: int = 500
    snap_auto_trigger_g: float = 2.0
    snap_max_snapshots: int = 100


@dataclass
class ImpactEvent:
    """Impact detection event."""
    timestamp: float
    level: float  # 0-1 severity
    g_force: float
    duration_ms: float
    triggered_by: str  # 'imu' or 'manual'
    accel: tuple[float, float, float] = (0.0, 0.0, 0.0)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            'timestamp': self.timestamp,
            'timestamp_str': datetime.fromtimestamp(self.timestamp).isoformat(),
            'level': round(self.level, 2),
            'g_force': round(self.g_force, 2),
            'duration_ms': round(self.duration_ms, 1),
            'triggered_by': self.triggered_by,
            'accel': [round(x, 2) for x in self.accel],
        }


@dataclass
class IMUSample:
    """Single IMU sample with timestamp."""
    timestamp_ns: int
    accel: np.ndarray  # m/s²
    gyro: np.ndarray   # rad/s
    
    def to_dict(self) -> dict[str, Any]:
        return {
            'timestamp_ns': self.timestamp_ns,
            'accel': {
                'x': round(float(self.accel[0]), 3),
                'y': round(float(self.accel[1]), 3),
                'z': round(float(self.accel[2]), 3),
            },
            'gyro': {
                'x': round(float(self.gyro[0]), 3),
                'y': round(float(self.gyro[1]), 3),
                'z': round(float(self.gyro[2]), 3),
            },
        }


@dataclass
class Snapshot:
    """Synchronized snapshot containing image and IMU data."""
    timestamp_ns: int
    image: np.ndarray
    imu_samples: list[IMUSample] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def compute_imu_stats(self) -> dict[str, float]:
        """Compute IMU statistics over the snapshot window."""
        if not self.imu_samples:
            return {}
        
        accel_magnitudes = []
        for sample in self.imu_samples:
            mag = np.linalg.norm(sample.accel) / 9.81  # Convert to G
            accel_magnitudes.append(mag)
        
        return {
            'max_g': max(accel_magnitudes),
            'mean_g': sum(accel_magnitudes) / len(accel_magnitudes),
            'min_g': min(accel_magnitudes),
            'sample_count': len(self.imu_samples),
        }


# ============================================================================
# Pre-impact Buffer
# ============================================================================

class PreImpactBuffer:
    """
    Circular buffer that always maintains N seconds of video in RAM.
    When impact detected, saves buffered frames to disk.
    
    Key feature: Buffer always fills at camera FPS (30fps), even when
    recording to disk is at lower FPS (timelapse mode).
    """
    
    def __init__(self, max_frames: int = 600):  # 20s at 30fps default
        self._buffer: deque = deque(maxlen=max_frames)
        self._lock = threading.Lock()
        self._frame_times: deque = deque(maxlen=max_frames)
        
    def add_frame(self, frame: np.ndarray, timestamp: float):
        """Add frame to buffer."""
        with self._lock:
            self._buffer.append(frame.copy())
            self._frame_times.append(timestamp)
    
    def get_frames(self, duration_s: float) -> list[tuple[float, np.ndarray]]:
        """Get frames from last N seconds."""
        with self._lock:
            if not self._frame_times:
                return []
            
            cutoff_time = time.monotonic() - duration_s
            frames = []
            for ts, frame in zip(self._frame_times, self._buffer):
                if ts >= cutoff_time:
                    frames.append((ts, frame.copy()))
            return frames
    
    def clear(self):
        """Clear buffer."""
        with self._lock:
            self._buffer.clear()
            self._frame_times.clear()


# ============================================================================
# Impact Detector (Internal)
# ============================================================================

class ImpactDetector:
    """
    IMU-based impact detection with VisionPilot-compatible sensitivity.
    0-100 scale with interpolation between thresholds.
    """
    
    def __init__(self, sensitivity: int = 50):
        self._sensitivity = sensitivity
        self._enabled = True
        self._cooldown_sec = IMPACT_COOLDOWN_S
        self._min_duration_ms = IMPACT_MIN_DURATION_MS
        
        self._last_impact_time: float | None = None
        self._impact_start_time: float | None = None
        self._current_g_force = 0.0
        self._max_g_force = 0.0
        self._peak_accel = (0.0, 0.0, 0.0)
        self._in_impact = False
        
        # History for UI
        self._events: deque = deque(maxlen=100)
        self._g_force_history: deque = deque(maxlen=300)  # 3s at 100Hz
        
        # Callbacks
        self._callbacks: list[Callable[[ImpactEvent], None]] = []
        
        cloudlog.info(f"ImpactDetector: sensitivity={sensitivity}, threshold={self._get_threshold():.2f}G")
    
    def set_sensitivity(self, sensitivity: int):
        """Update sensitivity (0-100)."""
        self._sensitivity = max(0, min(100, sensitivity))
        cloudlog.info(f"Impact sensitivity set to {self._sensitivity}, threshold={self._get_threshold():.2f}G")
    
    def set_enabled(self, enabled: bool):
        """Enable/disable detection."""
        self._enabled = enabled
        cloudlog.info(f"Impact detection {'enabled' if enabled else 'disabled'}")
    
    def register_callback(self, callback: Callable[[ImpactEvent], None]):
        """Register callback for impact events."""
        self._callbacks.append(callback)
    
    def _get_threshold(self) -> float:
        """Get G-force threshold from sensitivity setting (0-100)."""
        sensitivities = sorted(SENSITIVITY_THRESHOLDS.keys())
        thresholds = [SENSITIVITY_THRESHOLDS[s] for s in sensitivities]
        return float(np.interp(self._sensitivity, sensitivities, thresholds))
    
    def process_imu(self, accel: np.ndarray, gyro: np.ndarray, timestamp: float | None = None):
        """Process IMU data."""
        if not self._enabled:
            return None
        
        if timestamp is None:
            timestamp = time.monotonic()
        
        # Calculate G-force
        g_force = np.linalg.norm(accel) / 9.81
        self._current_g_force = g_force
        self._g_force_history.append((timestamp, g_force))
        
        threshold = self._get_threshold()
        
        # Check cooldown
        if self._last_impact_time and (timestamp - self._last_impact_time) < self._cooldown_sec:
            return None
        
        # Impact detection
        if g_force >= threshold:
            if not self._in_impact:
                self._in_impact = True
                self._impact_start_time = timestamp
                self._max_g_force = g_force
                self._peak_accel = (float(accel[0]), float(accel[1]), float(accel[2]))
            else:
                if g_force > self._max_g_force:
                    self._max_g_force = g_force
                    self._peak_accel = (float(accel[0]), float(accel[1]), float(accel[2]))
        else:
            if self._in_impact:
                self._in_impact = False
                duration_ms = (timestamp - (self._impact_start_time or timestamp)) * 1000
                
                if duration_ms >= self._min_duration_ms:
                    return self._trigger_impact(duration_ms)
        
        return None
    
    def _trigger_impact(self, duration_ms: float) -> ImpactEvent:
        """Trigger impact event."""
        now = time.monotonic()
        self._last_impact_time = now
        
        threshold = self._get_threshold()
        level = min(1.0, self._max_g_force / threshold - 1.0)
        
        event = ImpactEvent(
            timestamp=now,
            level=float(level),
            g_force=float(self._max_g_force),
            duration_ms=duration_ms,
            triggered_by='imu',
            accel=self._peak_accel
        )
        
        self._events.append(event)
        
        cloudlog.warning(
            f"IMPACT DETECTED: {event.g_force:.1f}G for {event.duration_ms:.0f}ms "
            f"(threshold: {threshold:.2f}G, level: {level:.2f})"
        )
        
        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                cloudlog.error(f"Impact callback error: {e}")
        
        return event
    
    def mark_manual_impact(self) -> ImpactEvent:
        """Manually mark an impact."""
        event = ImpactEvent(
            timestamp=time.monotonic(),
            level=1.0,
            g_force=float(self._current_g_force),
            duration_ms=0.0,
            triggered_by='manual',
            accel=self._peak_accel
        )
        self._events.append(event)
        
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                cloudlog.error(f"Impact callback error: {e}")
        
        return event
    
    def get_event_history(self) -> list[ImpactEvent]:
        """Get recent impact events."""
        return list(self._events)
    
    def get_status(self) -> dict[str, Any]:
        """Get detector status for UI."""
        return {
            'enabled': self._enabled,
            'sensitivity': self._sensitivity,
            'threshold_g': self._get_threshold(),
            'current_g': round(self._current_g_force, 2),
            'events_count': len(self._events),
            'in_impact': self._in_impact,
        }


# ============================================================================
# Video Encoder (InferenceD Client with RGA/MPP Hardware Acceleration)
# ============================================================================

# Try to import InferenceD client
try:
    from openpilot.system.inferenced import InferenceClient, TaskPriority
    HAS_INFERENCED = True
except ImportError:
    HAS_INFERENCED = False


class VideoEncoder:
    """
    Hardware video encoder using InferenceD client (RGA + MPP) with FFmpeg fallback.
    
    Architecture:
        recordd → InferenceClient → InferenceD → RGA/MPP backends
    
    Automatically uses hardware acceleration when available:
    1. InferenceD (RGA + MPP) - preferred (unified service)
    2. FFmpeg subprocess - fallback
    """
    
    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._output_path: Path | None = None
        self._frame_count = 0
        self._start_time: float | None = None
        
        # Hardware acceleration via InferenceD
        self._use_inferenced = HAS_INFERENCED
        self._use_ffmpeg = not HAS_INFERENCED
        
        # InferenceD client
        self._inference_client: InferenceClient | None = None
        self._file_handle: Any | None = None
        
        # Quality preset cache
        self._quality_preset: dict[str, Any | None] = None
        
        # Log what we're using
        if self._use_inferenced:
            cloudlog.info("Using InferenceD client (RGA + MPP) hardware acceleration")
        else:
            cloudlog.info("Using FFmpeg fallback encoder")
    
    def start(self, output_path: Path, quality_preset: dict[str, Any]):
        """Start encoding to file."""
        self.stop()

        self._output_path = output_path
        self._frame_count = 0
        self._start_time = time.monotonic()
        self._quality_preset = quality_preset

        # On dev PC, skip InferenceD and use ffmpeg directly (more efficient)
        if self._use_inferenced and not PC:
            self._start_inferenced(quality_preset)
        else:
            if PC and self._use_inferenced:
                cloudlog.info("Dev PC detected: using ffmpeg directly instead of InferenceD stub")
            self._use_inferenced = False
            self._use_ffmpeg = True
            self._start_ffmpeg(quality_preset)
    
    def _start_inferenced(self, quality_preset: dict[str, Any]):
        """Start InferenceD client encoder."""
        try:
            assert HAS_INFERENCED, "inferenced not available"
            self._inference_client = InferenceClient("recordd")  # type: ignore[possibly-undefined]
            assert self._output_path is not None
            self._file_handle = open(self._output_path, 'wb')
            cloudlog.info(f"InferenceD encoder started: {self._output_path}")
        except Exception as e:
            cloudlog.error(f"InferenceD start failed, falling back to ffmpeg: {e}")
            self._use_inferenced = False
            self._use_ffmpeg = True
            self._start_ffmpeg(quality_preset)
    
    def _start_ffmpeg(self, quality_preset: dict[str, Any]):
        """Start FFmpeg encoder."""
        width = quality_preset["width"]
        height = quality_preset["height"]
        fps = quality_preset["fps"]
        bitrate = quality_preset["bitrate"]

        # Select codec: hardware MPP on Rockchip, software x264 on dev PC
        # Also probe if h264_rkmpp is actually available in ffmpeg
        codec = "libx264"
        if ROCKCHIP:
            try:
                probe = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-encoders"],
                    capture_output=True, text=True, timeout=5.0
                )
                if "h264_rkmpp" in probe.stdout:
                    codec = "h264_rkmpp"
                    cloudlog.info("Using h264_rkmpp hardware encoder")
                else:
                    cloudlog.info("h264_rkmpp not available in ffmpeg, using libx264")
            except Exception:
                cloudlog.info("ffmpeg probe failed, using libx264 fallback")
        else:
            cloudlog.info("Using libx264 software encoder (dev PC)")

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", "yuv420p",
            "-s", f"{width}x{height}",
            "-r", str(fps),
            "-i", "-",
            "-c:v", codec,
            "-preset", "ultrafast" if codec == "libx264" else "fast",
            "-tune", "zerolatency",
            "-b:v", f"{bitrate}k",
            "-maxrate", f"{int(bitrate * 1.5)}k",
            "-bufsize", f"{bitrate * 2}k",
            "-an",
            str(self._output_path)
        ]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            cloudlog.info(f"FFmpeg encoder started: {self._output_path} ({width}x{height}@{fps}fps, {codec})")
        except Exception as e:
            cloudlog.error(f"FFmpeg encoder start failed: {e}")
            self._process = None
            raise
    
    def encode_frame(self, frame: np.ndarray) -> bool:
        """Encode a single frame."""
        if self._use_inferenced and self._inference_client is not None:
            return self._encode_frame_inferenced(frame)
        else:
            return self._encode_frame_ffmpeg(frame)
    
    def _encode_frame_inferenced(self, frame: np.ndarray) -> bool:
        """Encode frame using InferenceD client (MPP). Falls back to ffmpeg on failure."""
        inference_client = self._inference_client
        file_handle = self._file_handle
        quality_preset = self._quality_preset
        if inference_client is None or file_handle is None or quality_preset is None:
            return False
        try:
            import cv2

            # Convert BGR to NV12 (fast numpy vectorized path)
            height, width = frame.shape[:2]
            yuv_i420 = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420).ravel()
            y_size = width * height
            uv_size = width * height // 4

            y = yuv_i420[:y_size]
            u = yuv_i420[y_size:y_size + uv_size]
            v = yuv_i420[y_size + uv_size:y_size + 2 * uv_size]

            # Interleave U and V with numpy (much faster than Python loop)
            uv = np.empty(uv_size * 2, dtype=np.uint8)
            uv[0::2] = u
            uv[1::2] = v

            nv12 = np.concatenate([y, uv])
            nv12_2d = nv12.reshape((height * 3 // 2, width))

            # Use InferenceD client for H.264 encoding
            result = inference_client.infer_mpp_h264_encode(
                frame=nv12_2d,
                width=width,
                height=height,
                bitrate=quality_preset.get("bitrate", 4000),
                fps=quality_preset.get("fps", 20),
                priority=TaskPriority.NORMAL,  # type: ignore[possibly-undefined]
                timeout_ms=50.0
            )

            if result and result.success and 'data' in result.outputs:
                data = result.outputs['data']
                # Detect stub data (less than 20 bytes = not real H.264)
                if len(data) > 20:
                    file_handle.write(data)
                    self._frame_count += 1
                    return True
                else:
                    # MPP returned stub data on dev PC - fall back to ffmpeg
                    cloudlog.debug("InferenceD returned stub data, falling back to ffmpeg")
                    return self._encode_frame_ffmpeg(frame)

            # MPP failed entirely - fall back to ffmpeg
            cloudlog.debug("InferenceD encode failed, falling back to ffmpeg")
            return self._encode_frame_ffmpeg(frame)

        except Exception as e:
            cloudlog.error(f"InferenceD encode error: {e}, falling back to ffmpeg")
            return self._encode_frame_ffmpeg(frame)
    
    def _encode_frame_ffmpeg(self, frame: np.ndarray) -> bool:
        """Encode frame using FFmpeg. Lazily starts ffmpeg if not running."""
        # Lazy-start ffmpeg if not running (e.g., InferenceD fallback path)
        if self._process is None or self._process.poll() is not None:
            if self._quality_preset is None or self._output_path is None:
                return False
            try:
                self._start_ffmpeg(self._quality_preset)
            except Exception as e:
                cloudlog.error(f"Lazy ffmpeg start failed: {e}")
                return False

        if self._process is None or self._process.stdin is None:
            return False

        try:
            import cv2
            yuv = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420)
            self._process.stdin.write(yuv.tobytes())
            self._frame_count += 1
            return True
        except Exception as e:
            cloudlog.error(f"FFmpeg encode error: {e}")
            return False
    
    def stop(self) -> float:
        """Stop encoding and finalize file. Returns duration in seconds."""
        duration = 0.0
        
        if self._use_inferenced and self._inference_client is not None:
            duration = self._stop_inferenced()
        elif self._process:
            duration = self._stop_ffmpeg()
        
        return duration
    
    def _stop_inferenced(self) -> float:
        """Stop InferenceD encoder."""
        duration = 0.0
        try:
            if self._start_time:
                duration = time.monotonic() - self._start_time
            
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None
            
            if self._inference_client is not None:
                self._inference_client.release()
                self._inference_client = None
                
            cloudlog.info(f"InferenceD encoder stopped: {self._frame_count} frames, {duration:.1f}s")
            
        except Exception as e:
            cloudlog.warning(f"InferenceD encoder stop error: {e}")
        
        return duration
    
    def _stop_ffmpeg(self) -> float:
        """Stop FFmpeg encoder."""
        duration = 0.0
        if self._process:
            try:
                if self._start_time:
                    duration = time.monotonic() - self._start_time
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.wait(timeout=5.0)
                cloudlog.info(f"FFmpeg encoder stopped: {self._frame_count} frames, {duration:.1f}s")
            except Exception as e:
                cloudlog.warning(f"FFmpeg encoder stop error: {e}")
                self._process.kill()
            finally:
                self._process = None
        return duration

    @property
    def is_running(self) -> bool:
        """Check if encoder is active."""
        return self._process is not None and self._process.poll() is None
    
    @property
    def frame_count(self) -> int:
        return self._frame_count


# ============================================================================
# RecordD - Main Daemon
# ============================================================================

class RecordD:
    """Unified recording daemon."""
    
    def __init__(self):
        set_daemon_affinity("recordd")
        
        self.params = Params()
        # Subscribe to IMU for impact detection (impactd removed - now handled internally)
        self.sm = messaging.SubMaster(['deviceState', 'accelerometer', 'gyroscope'])
        self.pm = messaging.PubMaster(['recorddState'])
        
        # State
        self.running = False
        self.mode = RecordingMode.OFF
        self._event_mode_end_time = 0.0
        self._parking_mode_start_time: float | None = None
        
        # Settings
        self.settings = RecordingSettings()
        self._load_settings()
        
        # Components
        self._vipc: VisionIpcClient | None = None
        self._encoder = VideoEncoder()
        self._pre_buffer = PreImpactBuffer(max_frames=900)  # 30s at 30fps
        self._impact_detector = ImpactDetector(self.settings.impact_sensitivity)
        self._impact_detector.register_callback(self._on_impact)
        
        # IMU buffer for snap
        self._imu_buffer: deque = deque(maxlen=1000)  # ~5s @ 200Hz
        
        # Clips tracking
        self._clips: list[ClipInfo] = []
        self._current_clip_start: float | None = None
        self._current_clip_path: Path | None = None
        
        # Stats
        self._stats = {
            'frames_encoded': 0,
            'bytes_written': 0,
            'segments_saved': 0,
            'events_saved': 0,
            'snaps_saved': 0,
            'last_impact_time': 0.0,
            'last_impact_gforce': 0.0,
        }
        
        # Directories
        self._setup_directories()
        
        # Services
        self._services: dict[str, Callable] = {
            'mark_event': self._svc_mark_event,
            'set_impact_sensitivity': self._svc_set_sensitivity,
            'enable_parking_mode': self._svc_enable_parking,
            'trigger_snap': self._svc_trigger_snap,
            'get_storage_info': self._svc_get_storage_info,
            'get_clips': self._svc_get_clips,
        }
        
        cloudlog.info("RecordD initialized")
    
    def _setup_directories(self):
        """Create recording directories."""
        RECORD_ROOT.mkdir(parents=True, exist_ok=True)
        (RECORD_ROOT / "normal").mkdir(exist_ok=True)
        (RECORD_ROOT / "parking").mkdir(exist_ok=True)
        (RECORD_ROOT / "events").mkdir(exist_ok=True)
        (RECORD_ROOT / "snap").mkdir(exist_ok=True)
    
    def _load_settings(self):
        """Load settings from params."""
        try:
            self.settings.impact_sensitivity = int(self.params.get("EOPRecorddImpactSensitivity") or "50")
            self.settings.impact_pre_buffer_sec = float(self.params.get("EOPRecorddPreBufferSec") or "10.0")
            self.settings.impact_post_buffer_sec = float(self.params.get("EOPRecorddPostBufferSec") or "30.0")
            self.settings.parking_mode_enabled = self.params.get_bool("EOPRecorddParkingEnabled")
            self.settings.parking_duration_hours = float(self.params.get("EOPRecorddParkingHours") or "12.0")
            self.settings.quality = self.params.get("EOPRecorddQuality") or "medium"
            self.settings.snap_imu_history_ms = int(self.params.get("EOPRecorddSnapImuMs") or "500")
            self.settings.snap_auto_trigger_g = float(self.params.get("EOPRecorddSnapAutoG") or "2.0")
        except Exception as e:
            cloudlog.warning(f"Failed to load settings: {e}")
    
    def _save_settings(self):
        """Save settings to params."""
        self.params.put("EOPRecorddImpactSensitivity", str(self.settings.impact_sensitivity))
        self.params.put("EOPRecorddPreBufferSec", str(self.settings.impact_pre_buffer_sec))
        self.params.put("EOPRecorddPostBufferSec", str(self.settings.impact_post_buffer_sec))
        self.params.put_bool("EOPRecorddParkingEnabled", self.settings.parking_mode_enabled)
    
    def _discover_available_streams(self) -> dict[str, set[VisionStreamType]]:
        """Query all known servers for available streams."""
        available: dict[str, set[VisionStreamType]] = {}
        for stream, server in STREAM_SERVERS.items():
            if server not in available:
                available[server] = set()
            try:
                streams = VisionIpcClient.available_streams(server, block=False)
                available[server].update(streams)
            except Exception as e:
                cloudlog.debug(f"recordd: stream discovery failed for {server}: {e}")
        return available

    def _init_vipc(self, stream_type: VisionStreamType = VisionStreamType.VISION_STREAM_ROAD) -> bool:
        """Initialize VisionIPC for the given stream."""
        server = STREAM_SERVERS.get(stream_type, "v4l2d")
        try:
            vipc = VisionIpcClient(server, stream_type, True)
            self._vipc = vipc
            cloudlog.info(f"Connecting to VisionIPC ({server}/{stream_type})...")
            while not vipc.connect(False):
                time.sleep(0.1)
            cloudlog.info("VisionIPC connected")
            return True
        except Exception as e:
            cloudlog.error(f"VisionIPC init failed: {e}")
            return False
    
    def _get_mode(self) -> RecordingMode:
        """Determine current recording mode."""
        # Check if in event/impact mode
        if time.monotonic() < self._event_mode_end_time:
            return RecordingMode.IMPACT if self._stats['last_impact_gforce'] > 0 else RecordingMode.EVENT
        
        # Check ignition
        ignition = self.params.get_bool("EOPIgnitionOn")
        
        if ignition:
            return RecordingMode.NORMAL
        elif self.settings.parking_mode_enabled:
            # Check parking duration limit
            if self._parking_mode_start_time:
                elapsed_hours = (time.monotonic() - self._parking_mode_start_time) / 3600
                if elapsed_hours >= self.settings.parking_duration_hours:
                    return RecordingMode.OFF
            return RecordingMode.TIMELAPSE
        else:
            return RecordingMode.OFF
    
    def _get_quality_preset(self, mode: RecordingMode) -> dict[str, Any]:
        """Get quality preset for mode."""
        if mode == RecordingMode.IMPACT or mode == RecordingMode.EVENT:
            return EVENT_QUALITY
        elif mode == RecordingMode.TIMELAPSE:
            return TIMELAPSE_QUALITY
        else:
            return QUALITY_PRESETS.get(self.settings.quality, QUALITY_PRESETS["medium"])
    
    def _get_output_path(self, mode: RecordingMode) -> Path:
        """Get output path for current segment."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if mode == RecordingMode.NORMAL:
            folder = "normal"
            suffix = ""
        elif mode == RecordingMode.TIMELAPSE:
            folder = "parking"
            suffix = "_timelapse"
        elif mode == RecordingMode.IMPACT:
            folder = "events"
            suffix = "_impact"
        else:
            folder = "events"
            suffix = "_event"
        
        return RECORD_ROOT / folder / f"{timestamp}{suffix}.mp4"
    
    def _on_impact(self, event: ImpactEvent):
        """Handle impact detection."""
        # Enter impact mode for post-buffer duration
        self._event_mode_end_time = time.monotonic() + self.settings.impact_post_buffer_sec
        
        # Save pre-impact buffer
        self._save_impact_event(event)
        
        # Update stats
        self._stats['last_impact_time'] = event.timestamp
        self._stats['last_impact_gforce'] = event.g_force
        self._stats['events_saved'] += 1
    
    def _save_impact_event(self, event: ImpactEvent):
        """Save impact event with pre-impact buffer."""
        try:
            import cv2
            
            # Create event directory
            timestamp_str = datetime.fromtimestamp(event.timestamp).strftime("%Y%m%d_%H%M%S")
            event_dir = RECORD_ROOT / "events" / f"impact_{timestamp_str}"
            event_dir.mkdir(parents=True, exist_ok=True)
            
            # Save event metadata
            meta_path = event_dir / "event.json"
            with open(meta_path, 'w') as f:
                json.dump(event.to_dict(), f, indent=2)
            
            # Save pre-impact video
            frames = self._pre_buffer.get_frames(self.settings.impact_pre_buffer_sec)
            if frames:
                video_path = event_dir / "pre_impact.mp4"
                self._save_video(frames, video_path, fps=30)
                
                # Create clip info
                clip_info = ClipInfo(
                    filename=str(video_path),
                    timestamp=timestamp_str,
                    duration_sec=len(frames) / 30.0,
                    size_bytes=video_path.stat().st_size if video_path.exists() else 0,
                    clip_type="impact",
                    is_event=True,
                    is_preserved=True,
                    event_reason=f"impact_{event.triggered_by}",
                    impact_level=event.level
                )
                self._clips.append(clip_info)
                
                cloudlog.info(f"Pre-impact video saved: {video_path} ({len(frames)} frames)")
            
            cloudlog.info(f"Impact event saved: {event_dir}")
            
        except Exception as e:
            cloudlog.error(f"Failed to save impact event: {e}")
    
    def _save_video(self, frames: list[tuple[float, np.ndarray]], output_path: Path, fps: int):
        """Save frames to video file."""
        import cv2
        
        if not frames:
            return
        
        height, width = frames[0][1].shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')  # type: ignore[attr-defined]
        writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))
        
        for _, frame in frames:
            writer.write(frame)
        
        writer.release()
    
    def _finalize_clip(self):
        """Finalize current clip recording."""
        if self._current_clip_path and self._current_clip_start:
            duration = time.monotonic() - self._current_clip_start
            
            clip_info = ClipInfo(
                filename=str(self._current_clip_path),
                timestamp=datetime.fromtimestamp(self._current_clip_start).strftime("%Y%m%d_%H%M%S"),
                duration_sec=duration,
                size_bytes=self._current_clip_path.stat().st_size if self._current_clip_path.exists() else 0,
                clip_type=self.mode.value,
                is_event=(self.mode in [RecordingMode.EVENT, RecordingMode.IMPACT]),
                is_preserved=(self.mode == RecordingMode.IMPACT)
            )
            self._clips.append(clip_info)
            self._stats['segments_saved'] += 1
            
            if self._current_clip_path.exists():
                self._stats['bytes_written'] += self._current_clip_path.stat().st_size
    
    def _cleanup_storage(self):
        """Clean up old recordings."""
        try:
            # Count .mp4 files (H.264)
            total = sum(f.stat().st_size for f in RECORD_ROOT.rglob("*.mp4"))
            max_bytes = int(MAX_STORAGE_GB * 1024**3)
            
            if total > max_bytes * 0.9:
                # Delete oldest normal recordings first
                normal_dir = RECORD_ROOT / "normal"
                if normal_dir.exists():
                    # Get .mp4 files sorted by modification time
                    files = sorted(normal_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime)
                    while total > max_bytes * 0.8 and files:
                        oldest = files.pop(0)
                        try:
                            total -= oldest.stat().st_size
                            oldest.unlink()
                        except (OSError, PermissionError):
                            pass
        except Exception as e:
            cloudlog.debug(f"Storage cleanup error: {e}")
    
    def _get_storage_info(self) -> StorageInfo:
        """Get current storage info."""
        info = StorageInfo()
        info.recording_mode = self.mode.value
        info.impact_sensitivity = self.settings.impact_sensitivity
        
        try:
            import shutil
            stat = shutil.disk_usage(RECORD_ROOT)
            info.total_bytes = stat.total
            info.used_bytes = stat.used
            info.free_bytes = stat.free
            
            # Count clips (.mp4 H.264 files)
            info.clips_count = len(list((RECORD_ROOT / "normal").glob("*.mp4")))
            info.clips_count += len(list((RECORD_ROOT / "parking").glob("*.mp4")))
            info.events_count = len(list((RECORD_ROOT / "events").glob("*.mp4")))
            info.impact_count = self._stats['events_saved']
            
        except Exception as e:
            cloudlog.debug(f"Storage info error: {e}")
        
        return info
    
    # ========================================================================
    # Services (RPC handlers)
    # ========================================================================
    
    def _svc_mark_event(self, reason: str = "manual") -> dict[str, Any]:
        """Mark current time as event."""
        self._event_mode_end_time = time.monotonic() + 30.0  # 30s event mode
        cloudlog.info(f"Event marked: {reason}")
        return {'success': True, 'reason': reason}
    
    def _svc_set_sensitivity(self, sensitivity: int) -> dict[str, Any]:
        """Set impact sensitivity (0-100)."""
        self.settings.impact_sensitivity = max(0, min(100, sensitivity))
        self._impact_detector.set_sensitivity(self.settings.impact_sensitivity)
        self._save_settings()
        return {'success': True, 'sensitivity': self.settings.impact_sensitivity}
    
    def _svc_enable_parking(self, enabled: bool) -> dict[str, Any]:
        """Enable/disable parking mode."""
        self.settings.parking_mode_enabled = enabled
        self._save_settings()
        return {'success': True, 'enabled': enabled}
    
    def _svc_trigger_snap(self, reason: str = "manual") -> dict[str, Any]:
        """Trigger snapshot capture."""
        path = self.capture_snap(reason)
        return {'success': path is not None, 'path': str(path) if path else None}
    
    def _svc_get_storage_info(self) -> dict[str, Any]:
        """Get storage information."""
        return self._get_storage_info().to_dict()
    
    def _svc_get_clips(self) -> list[dict[str, Any]]:
        """Get list of recorded clips."""
        return [c.to_dict() for c in self._clips[-100:]]  # Last 100 clips
    
    # ========================================================================
    # Snap Capture
    # ========================================================================
    
    def capture_snap(self, reason: str = "manual") -> Path | None:
        """Capture snapshot with IMU history."""
        try:
            import cv2
            
            if self._vipc is None:
                return None
            
            buf = self._vipc.recv(timeout_ms=1000)
            if buf is None:
                return None
            
            # Convert to numpy
            img = np.frombuffer(buf.data, dtype=np.uint8).reshape((buf.height, buf.width, 3))
            
            # Get IMU samples from history
            history_ns = self.settings.snap_imu_history_ms * 1_000_000  # Convert to ns
            cutoff_time = time.time_ns() - history_ns
            imu_samples = [s for s in self._imu_buffer if s.timestamp_ns >= cutoff_time]
            
            # Create snapshot
            snap = Snapshot(
                timestamp_ns=time.time_ns(),
                image=img.copy(),
                imu_samples=imu_samples,
                metadata={'reason': reason, 'imu_stats': {}}
            )
            
            # Compute IMU stats
            snap.metadata['imu_stats'] = snap.compute_imu_stats()
            
            # Save
            timestamp_str = datetime.fromtimestamp(time.time()).strftime("%Y%m%d_%H%M%S")
            snap_dir = RECORD_ROOT / "snap" / f"snap_{timestamp_str}"
            snap_dir.mkdir(parents=True, exist_ok=True)
            
            # Save image
            img_path = snap_dir / "image.jpg"
            cv2.imwrite(str(img_path), snap.image)
            
            # Save metadata
            meta_path = snap_dir / "metadata.json"
            with open(meta_path, 'w') as f:
                json.dump({
                    'timestamp_ns': snap.timestamp_ns,
                    'timestamp': datetime.fromtimestamp(snap.timestamp_ns / 1e9).isoformat(),
                    'metadata': snap.metadata,
                    'imu_samples': len(snap.imu_samples),
                    'imu_history': [s.to_dict() for s in snap.imu_samples],
                }, f, indent=2)
            
            self._stats['snaps_saved'] += 1
            cloudlog.info(f"Snapshot saved: {snap_dir} ({len(imu_samples)} IMU samples)")
            return snap_dir
            
        except Exception as e:
            cloudlog.error(f"Snap capture failed: {e}")
            return None
    
    # ========================================================================
    # Main Loop
    # ========================================================================
    
    def _publish_state(self):
        """Publish recordd state to cereal."""
        try:
            msg = messaging.new_message('recorddState')
            state = msg.recorddState
            
            state.mode = self.mode.value
            state.recording = self._encoder.is_running
            preset = self._get_quality_preset(self.mode)
            state.fps = float(preset["fps"])
            state.segmentDuration = self.settings.segment_duration_sec
            
            state.preBufferSeconds = int(self.settings.impact_pre_buffer_sec)
            state.postBufferSeconds = int(self.settings.impact_post_buffer_sec)
            
            state.impactEnabled = self._impact_detector._enabled
            state.impactSensitivity = int(self.settings.impact_sensitivity)
            state.lastImpactTime = self._stats['last_impact_time']
            state.lastImpactGForce = self._stats['last_impact_gforce']
            
            state.framesEncoded = int(self._stats['frames_encoded'])
            state.segmentsSaved = int(self._stats['segments_saved'])
            state.eventsSaved = int(self._stats['events_saved'])
            state.snapsSaved = int(self._stats['snaps_saved'])
            
            # Storage info
            storage = self._get_storage_info()
            state.storageUsedGB = float(storage.to_dict()['used_gb'])
            state.storageTotalGB = float(storage.to_dict()['total_gb'])
            state.clipsCount = int(storage.clips_count)
            state.eventsCount = int(storage.events_count)
            
            # Current G-force
            state.currentGForce = float(self._impact_detector._current_g_force)
            state.inImpact = bool(self._impact_detector._in_impact)
            
            self.pm.send('recorddState', msg)
        except Exception as e:
            cloudlog.debug(f"Failed to publish state: {e}")
    
    def run(self):
        """Main recording loop."""
        if not self._init_vipc():
            cloudlog.error("Failed to initialize VisionIPC")
            return
        
        self.running = True
        
        def signal_handler(signum, frame):
            self.running = False
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Initialize mode
        self.mode = self._get_mode()
        preset = self._get_quality_preset(self.mode)
        
        segment_start = time.monotonic()
        frame_count = 0
        
        cloudlog.info(f"RecordD running (mode: {self.mode.value}, quality: {self.settings.quality})")
        
        try:
            while self.running:
                self.sm.update(0)
                
                # Process IMU data for impact detection
                if self.sm.updated['accelerometer'] and self.sm.updated['gyroscope']:
                    try:
                        acc = self.sm['accelerometer']
                        gyro = self.sm['gyroscope']
                        accel = np.array([acc.acceleration.v[0], acc.acceleration.v[1], acc.acceleration.v[2]])
                        gyro_data = np.array([gyro.gyroUncalibrated.v[0], gyro.gyroUncalibrated.v[1], gyro.gyroUncalibrated.v[2]])
                        
                        # Add to IMU buffer for snap
                        self._imu_buffer.append(IMUSample(
                            timestamp_ns=time.time_ns(),
                            accel=accel,
                            gyro=gyro_data
                        ))
                        
                        # Process for impact detection
                        impact_event = self._impact_detector.process_imu(accel, gyro_data)
                        
                        # Auto-trigger snap on high G
                        if self.settings.snap_auto_trigger_g > 0:
                            g_force = np.linalg.norm(accel) / 9.81
                            if g_force >= self.settings.snap_auto_trigger_g:
                                if not hasattr(self, '_last_auto_snap') or time.monotonic() - self._last_auto_snap > 5.0:
                                    self.capture_snap(f"auto_trigger_{g_force:.1f}g")
                                    self._last_auto_snap = time.monotonic()
                        
                    except Exception as e:
                        cloudlog.debug(f"IMU processing error: {e}")
                
                # Check for mode changes
                new_mode = self._get_mode()
                if new_mode != self.mode:
                    cloudlog.info(f"Mode: {self.mode.value} -> {new_mode.value}")
                    
                    # Finalize previous clip
                    self._finalize_clip()
                    
                    self.mode = new_mode
                    preset = self._get_quality_preset(self.mode)
                    self._encoder.stop()
                    segment_start = time.monotonic()
                    
                    # Track parking mode start
                    if new_mode == RecordingMode.TIMELAPSE:
                        self._parking_mode_start_time = time.monotonic()
                
                # Start new segment if needed
                if not self._encoder.is_running or \
                   time.monotonic() - segment_start >= self.settings.segment_duration_sec:
                    
                    # Finalize previous clip
                    if self._encoder.is_running:
                        self._finalize_clip()
                    
                    output = self._get_output_path(self.mode)
                    self._encoder.start(output, preset)
                    self._current_clip_path = output
                    self._current_clip_start = time.monotonic()
                    segment_start = time.monotonic()
                
                # Capture frame
                if self._vipc:
                    buf = self._vipc.recv(timeout_ms=100)
                    if buf:
                        import cv2
                        img = np.frombuffer(buf.data, dtype=np.uint8).reshape((buf.height, buf.width, 3))
                        
                        # Always add to pre-impact buffer at full FPS
                        self._pre_buffer.add_frame(img, time.monotonic())
                        
                        # Encode if in active mode
                        if self.mode != RecordingMode.OFF:
                            # Frame skipping for timelapse
                            skip = False
                            if self.mode == RecordingMode.TIMELAPSE:
                                skip = (frame_count % 30 != 0)  # 1fps from 30fps
                            
                            if not skip:
                                if self._encoder.encode_frame(img):
                                    frame_count += 1
                                    self._stats['frames_encoded'] += 1
                
                # Periodic cleanup and state publish
                if frame_count % 100 == 0:
                    self._cleanup_storage()
                    self._publish_state()
                    
        except Exception as e:
            cloudlog.exception("Fatal error")
        finally:
            self._finalize_clip()
            self._encoder.stop()
            if self._vipc:
                self._vipc.close()
            cloudlog.info(f"Stopped - {frame_count} frames encoded")


def main() -> int:
    try:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        RecordD().run()
        return 0
    except Exception as e:
        cloudlog.exception(f"RecordD fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
