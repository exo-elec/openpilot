#!/usr/bin/env python3
"""SteamD configuration."""

import dataclasses


@dataclasses.dataclass
class SteamDConfig:
  """SteamD runtime configuration."""

  # Web server (status page)
  web_host: str = "0.0.0.0"
  web_port: int = 5000
  use_ssl: bool = True

  # Control limits (physical units)
  max_steering_angle: float = 90.0  # degrees
  max_accel_mps2: float = 1.5
  max_decel_mps2: float = 3.0

  # Safety timeouts
  control_timeout_sec: float = 0.5
  link_loss_ramp_ms: float = 500.0
  link_loss_kill_ms: float = 2000.0

  # Feature flags
  require_heartbeat: bool = True
  enable_sound_feedback: bool = True
  enable_joystick_input: bool = True
  enable_keyboard_input: bool = False
  enable_udp_input: bool = True
  enable_udp_stream: bool = True

  # UDP teleop input (Pico / Quest / OpenArm compatible)
  udp_listen_addr: str = "0.0.0.0"
  udp_listen_port: int = 5100

  # UDP video stream (H264 unicast to headset target)
  # Set to headset IP (LAN or WireGuard VPN).
  # Empty string disables streaming.
  udp_stream_target_addr: str = ""
  udp_stream_target_port: int = 5120
  udp_stream_fps: int = 30
  udp_stream_bitrate_kbps: int = 4000
  udp_stream_width: int = 1280   # per-eye
  udp_stream_height: int = 720
  udp_stream_encoder: str = "libx264"   # h264_rkmpp on RK3588

  # OpenArmX APK steering (no thumbstick — derive from hand quaternion)
  openarmx_steer_source: str = "roll"   # "roll" | "yaw" | "pitch" | "position"
  openarmx_max_roll_deg: float = 45.0
  openarmx_max_yaw_deg: float = 60.0
