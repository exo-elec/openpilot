#!/usr/bin/env python3
"""
SteamD External Control Inputs

Unified input abstraction for all outside-car control sources.
SteamD is the single source of external control authority.

Inputs:
  - UDP:      VR teleoperation (Pico / Quest / OpenArmX compatible)
  - Joystick: local gamepad / wheel (direct /dev/input + testJoystick fallback)
  - Keyboard: local keyboard debug control
"""

from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

import cereal.messaging as messaging
from cereal import car

logger = logging.getLogger("SteamD.inputs")

LongCtrlState = car.CarControl.Actuators.LongControlState


@dataclass
class ControlCommand:
  """Normalized external control command."""
  steer: float = 0.0          # -1.0 .. 1.0
  accel: float = 0.0          # m/s² (negative = brake)
  engage: bool = False
  disengage: bool = False
  deadman: bool = False
  view_mode: str | None = None
  assist: bool = False
  source: str = "unknown"
  timestamp: float = field(default_factory=time.monotonic)


class ControlInput(ABC):
  """Base class for an external control input source."""

  def __init__(self, name: str):
    self.name = name
    self._engaged = False
    self._last_cmd = ControlCommand(source=name)

  @property
  def engaged(self) -> bool:
    return self._engaged

  @property
  def last_cmd(self) -> ControlCommand:
    return self._last_cmd

  @abstractmethod
  def poll(self) -> ControlCommand | None:
    """Poll for a new control command. Returns None if no update."""

  def reset(self):
    """Reset engaged state and zero command."""
    self._engaged = False
    self._last_cmd = ControlCommand(source=self.name)


class JoystickInput(ControlInput):
  """Local gamepad / racing wheel input.

  Reads directly from /dev/input (USB or Bluetooth). Falls back to
  testJoystick cereal messages for remote/laptop bridge mode.

  Generic axis mapping (works across PS, Xbox, and most wheels):
    ABS_X  → steering  (left stick X or wheel rotation)
    ABS_RZ → gas       (R2 / RT / accelerator pedal)
    ABS_Z  → brake     (L2 / LT / brake pedal)
  """

  EXPO = 0.4
  DEADZONE = 0.03
  STEER_AXIS = "ABS_X"
  GAS_AXIS = "ABS_RZ"
  BRAKE_AXIS = "ABS_Z"

  def __init__(self):
    super().__init__("joystick")
    self._lock = threading.Lock()
    self._direct_axes = {"steer": 0.0, "gas": 0.0, "brake": 0.0}
    self._direct_available = False
    self._direct_thread: threading.Thread | None = None

    # Attempt direct gamepad / wheel read
    try:
      from inputs import devices
      # Accept anything that looks like a gamepad, joystick, or wheel
      candidates = list(devices.gamepads)
      if not candidates:
        candidates = [d for d in devices
                      if any(k in d.name.lower() for k in ("gamepad", "joystick",
                                                            "controller", "wheel"))]
      if candidates:
        self._direct_thread = threading.Thread(target=self._gamepad_thread, daemon=True)
        self._direct_thread.start()
        self._direct_available = True
        logger.info(f"Joystick: using device '{candidates[0].name}'")
    except Exception as e:
      logger.info(f"Joystick: direct input unavailable ({e}), falling back to testJoystick")

    # Fallback: testJoystick subscription (remote laptop / legacy bridge)
    self.sm = messaging.SubMaster(["testJoystick"])
    self._fallback_axes = [0.0, 0.0]

  def _gamepad_thread(self):
    """Background thread reading from /dev/input via inputs library."""
    try:
      from inputs import UnpluggedError, get_gamepad
    except Exception:
      return

    steer_axis = self.STEER_AXIS
    gas_axis = self.GAS_AXIS
    brake_axis = self.BRAKE_AXIS

    # Fixed hardware limits (prevents drift if a button is held)
    AXES_LIMITS = {
      steer_axis: (-32767.0, 32767.0),
      gas_axis: (0.0, 255.0),
      brake_axis: (0.0, 255.0),
    }
    axes_values = {steer_axis: 0.0, gas_axis: 0.0, brake_axis: 0.0}

    while True:
      try:
        event = get_gamepad()[0]
      except (OSError, UnpluggedError):
        with self._lock:
          self._direct_axes = {"steer": 0.0, "gas": 0.0, "brake": 0.0}
        time.sleep(0.5)
        continue

      code = event.code
      state = event.state

      if code in axes_values:
        axis_min, axis_max = AXES_LIMITS[code]

        # Normalize against fixed hardware limits
        rng = axis_max - axis_min
        if rng > 0:
          norm = 2.0 * (state - axis_min) / rng - 1.0
        else:
          norm = 0.0

        # Deadzone + expo
        norm = norm if abs(norm) > self.DEADZONE else 0.0
        axes_values[code] = self.EXPO * norm ** 3 + (1 - self.EXPO) * norm

      with self._lock:
        self._direct_axes["steer"] = axes_values[steer_axis]
        self._direct_axes["gas"] = max(0.0, min(1.0, axes_values[gas_axis]))
        self._direct_axes["brake"] = max(0.0, min(1.0, axes_values[brake_axis]))

  def poll(self) -> ControlCommand | None:
    # Prefer direct gamepad / wheel
    if self._direct_available:
      with self._lock:
        axes = dict(self._direct_axes)
      cmd = ControlCommand(source="joystick")
      cmd.accel = (axes["gas"], axes["brake"])
      cmd.steer = axes["steer"]
      self._last_cmd = cmd
      return cmd

    # Fallback to testJoystick cereal
    self.sm.update(0)
    stale = (self.sm.frame - self.sm.recv_frame["testJoystick"]) * 0.01 > 0.2
    if stale:
      if self._fallback_axes != [0.0, 0.0]:
        self._fallback_axes = [0.0, 0.0]
        self._last_cmd = ControlCommand(source="joystick")
        return self._last_cmd
      return None

    axes = self.sm["testJoystick"].axes
    if len(axes) < 2:
      return None

    self._fallback_axes = [float(np.clip(axes[0], -1, 1)), float(np.clip(axes[1], -1, 1))]
    cmd = ControlCommand(source="joystick")
    cmd.accel = 4.0 * self._fallback_axes[0]
    cmd.steer = self._fallback_axes[1]
    self._last_cmd = cmd
    return cmd


class KeyboardInput(ControlInput):
  """Local keyboard debug input. Optional; off by default."""

  def __init__(self):
    super().__init__("keyboard")
    self._kb = None
    self._axes_values = {"gb": 0.0, "steer": 0.0}
    self._axis_increment = 0.05
    try:
      from openpilot.tools.lib.kbhit import KBHit
      self._kb = KBHit()
    except Exception as e:
      logger.warning(f"Keyboard input unavailable: {e}")

  def poll(self) -> ControlCommand | None:
    if self._kb is None:
      return None

    key = self._kb.getch()
    if not key:
      return None

    key = key.lower()
    changed = False

    if key == "r":
      self._axes_values = dict.fromkeys(self._axes_values, 0.0)
      changed = True
    elif key in ("q", "\x1b"):  # q or Esc = emergency disengage
      self._axes_values = dict.fromkeys(self._axes_values, 0.0)
      self._last_cmd = ControlCommand(source="keyboard")
      return self._last_cmd
    elif key in ("w", "s"):
      axis = "gb"
      incr = self._axis_increment if key == "w" else -self._axis_increment
      self._axes_values[axis] = float(np.clip(self._axes_values[axis] + incr, -1, 1))
      changed = True
    elif key in ("a", "d"):
      axis = "steer"
      incr = self._axis_increment if key == "a" else -self._axis_increment
      self._axes_values[axis] = float(np.clip(self._axes_values[axis] + incr, -1, 1))
      changed = True

    if not changed:
      return None

    cmd = ControlCommand(source="keyboard")
    cmd.accel = 4.0 * self._axes_values["gb"]
    cmd.steer = self._axes_values["steer"]
    self._last_cmd = cmd
    return cmd

  def disengage(self) -> bool:
    """Immediately zero axes and return a zero command (called on emergency key)."""
    self._axes_values = dict.fromkeys(self._axes_values, 0.0)
    self._last_cmd = ControlCommand(source="keyboard")
    return True


# ============================================================================
# UDP Teleop Input — Pico / Quest / OpenArmX compatible
# ============================================================================

@dataclass
class UdpControllerState:
  """Parsed UDP controller state (binary + JSON + OpenArmX text)."""
  position: np.ndarray = field(default_factory=lambda: np.zeros(3))
  orientation: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0, 1.0]))
  grip: float = 0.0
  trigger: float = 0.0
  thumbstick: np.ndarray = field(default_factory=lambda: np.zeros(2))
  button_a: bool = False
  button_b: bool = False
  button_x: bool = False
  button_y: bool = False
  active: bool = False
  timestamp_ns: int = 0


class UdpInput(ControlInput):
  """UDP teleoperation input (Pico 4 / Meta Quest / OpenArmX APK).

  Supports THREE protocols on the same port:
    1. OpenArmX text protocol   — primary, for OpenArmX VR APK
    2. Binary protocol          — legacy, HumRobot / custom clients
    3. JSON protocol            — debug / web clients

  OpenArmX control mapping (hand-pose → car):
    - Right hand lateral offset (relative to calibrated center) → steering
    - Right trigger → throttle
    - Left trigger  → brake
    - Both grips held → deadman switch
    - Button A (right) → engage
    - Button B (right) → disengage
    - Button X (left)  → left camera view
    - Button Y (left)  → right camera view
    - Hold any button  → assist overlay
  """

  DEADZONE = 0.1
  STEER_EXPO = 0.4

  # Hand-pose steering: 15 cm lateral movement = full steer lock
  HAND_STEER_RANGE_M = 0.15

  def __init__(self, listen_addr: str = "0.0.0.0", listen_port: int = 5100):
    super().__init__("udp")
    self._listen_addr = listen_addr
    self._listen_port = listen_port
    self._sock: socket.socket | None = None
    self._thread: threading.Thread | None = None
    self._running = False
    self._lock = threading.Lock()

    self._left = UdpControllerState()
    self._right = UdpControllerState()
    self._last_recv_time = 0.0

    # Button-edge detection for view cycling
    self._prev_btn_a = False
    self._prev_btn_b = False
    self._prev_btn_x = False
    self._prev_btn_y = False
    self._current_view = "road"

    # OpenArmX hand-pose calibration
    self._calibrated = False
    self._calib_center_x: float = 0.0
    self._calib_deadband_m: float = 0.02  # 2 cm deadband around center

    self._start_listener()

  def _start_listener(self):
    try:
      self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
      self._sock.bind((self._listen_addr, self._listen_port))
      self._sock.settimeout(0.1)
      self._running = True
      self._thread = threading.Thread(target=self._receive_loop, daemon=True)
      self._thread.start()
      logger.info(f"UdpInput: listener on {self._listen_addr}:{self._listen_port}")
    except Exception as e:
      logger.error(f"UdpInput: failed to bind: {e}")

  def _receive_loop(self):
    while self._running:
      try:
        data, _ = self._sock.recvfrom(512)
        self._parse(data)
        self._last_recv_time = time.monotonic()
      except socket.timeout:
        continue
      except Exception as e:
        logger.debug(f"UdpInput receive error: {e}")

  def _parse(self, data: bytes):
    """Auto-detect protocol: OpenArmX text → JSON → binary."""
    if not data:
      return

    # Try OpenArmX text first (starts with ASCII letters: LEFT, RIGHT, HEAD, MODE, CALIBRATE_DONE)
    if data[0:1].isalpha():
      try:
        text = data.decode("utf-8").strip()
        self._parse_openarmx_text(text)
        return
      except Exception:
        pass

    # Try JSON next
    try:
      msg = json.loads(data.decode("utf-8"))
      hand = msg.get("hand", "left")
      state = self._left if hand == "left" else self._right
      state.position = np.array(msg.get("position", [0.0, 0.0, 0.0]))
      state.orientation = np.array(msg.get("orientation", [0.0, 0.0, 0.0, 1.0]))
      state.grip = float(msg.get("grip", 0.0))
      state.trigger = float(msg.get("trigger", 0.0))
      state.thumbstick = np.array(msg.get("thumbstick", [0.0, 0.0]))
      state.button_a = bool(msg.get("button_a", False))
      state.button_b = bool(msg.get("button_b", False))
      state.button_x = bool(msg.get("button_x", False))
      state.button_y = bool(msg.get("button_y", False))
      state.active = True
      state.timestamp_ns = int(msg.get("timestamp_ns", 0))
      return
    except (json.JSONDecodeError, UnicodeDecodeError):
      pass

    # Fallback to binary
    self._parse_binary(data)

  # ------------------------------------------------------------------ #
  # OpenArmX text protocol
  # ------------------------------------------------------------------ #

  def _parse_openarmx_text(self, text: str):
    """Parse OpenArmX text datagram.

    Formats:
      LEFT  px py pz ox oy oz ow trigger grip btn_a btn_b btn_x btn_y rate timestamp
      RIGHT px py pz ox oy oz ow trigger grip btn_a btn_b btn_x btn_y rate timestamp
      HEAD  px py pz ox oy oz ow timestamp
      MODE  mode_name timestamp
      CALIBRATE_DONE timestamp
    """
    parts = text.split()
    if not parts:
      return

    keyword = parts[0].upper()

    if keyword in ("LEFT", "L", "LEFT_ABS"):
      self._parse_openarmx_hand(parts[1:], self._left)
    elif keyword in ("RIGHT", "R", "RIGHT_ABS"):
      self._parse_openarmx_hand(parts[1:], self._right)
    elif keyword == "HEAD":
      # Head pose — not used for car control, but log first packet
      pass
    elif keyword == "MODE":
      if len(parts) >= 2:
        logger.info(f"UdpInput: OpenArmX mode = {parts[1]}")
    elif keyword == "CALIBRATE_DONE":
      self._calibrated = False  # Will re-calibrate on next hand packet
      logger.info("UdpInput: OpenArmX calibration done — will re-center on next hand pose")

  def _parse_openarmx_hand(self, tok: list[str], state: UdpControllerState):
    """Parse space-separated hand payload: pos[3] quat[4] trigger grip btn_a btn_b btn_x btn_y rate timestamp"""
    try:
      idx = 0
      # Position (x y z)
      state.position = np.array([float(tok[idx]), float(tok[idx + 1]), float(tok[idx + 2])])
      idx += 3
      # Orientation (x y z w)
      state.orientation = np.array([float(tok[idx]), float(tok[idx + 1]), float(tok[idx + 2]), float(tok[idx + 3])])
      idx += 4
      # Trigger
      state.trigger = float(tok[idx])
      idx += 1
      # Grip
      state.grip = float(tok[idx])
      idx += 1
      # Buttons
      state.button_a = int(tok[idx]) != 0
      idx += 1
      state.button_b = int(tok[idx]) != 0
      idx += 1
      state.button_x = int(tok[idx]) != 0
      idx += 1
      state.button_y = int(tok[idx]) != 0
      idx += 1
      # Rate (0.1 or 1.0)
      _rate = float(tok[idx])
      idx += 1
      # Timestamp
      state.timestamp_ns = int(tok[idx]) if idx < len(tok) else 0
      state.active = True

      # Auto-calibrate on first packet after CALIBRATE_DONE
      if not self._calibrated:
        self._calib_center_x = float(state.position[0])
        self._calibrated = True
        logger.info(f"UdpInput: steering calibrated — center X = {self._calib_center_x:.3f}m")
    except (IndexError, ValueError) as e:
      logger.debug(f"UdpInput: OpenArmX hand parse error: {e} (tokens={len(tok)})")

  # ------------------------------------------------------------------ #
  # Legacy binary protocol
  # ------------------------------------------------------------------ #

  def _parse_binary(self, data: bytes):
    if len(data) < 80:
      return
    try:
      fmt = "<BB3d4ddddddq"
      size = struct.calcsize(fmt)
      if len(data) < size:
        return
      unpacked = struct.unpack(fmt, data[:size])
      hand_idx = unpacked[0]
      flags = unpacked[1]
      state = self._left if hand_idx == 0 else self._right
      state.position = np.array(unpacked[2:5])
      state.orientation = np.array(unpacked[5:9])
      state.trigger = unpacked[9]
      state.grip = unpacked[10]
      state.thumbstick = np.array(unpacked[11:13])
      state.button_a = bool(flags & 0x01)
      state.button_b = bool(flags & 0x02)
      state.button_x = bool(flags & 0x04)
      state.button_y = bool(flags & 0x08)
      state.active = True
      state.timestamp_ns = unpacked[14]
    except struct.error:
      pass

  @property
  def last_recv_time(self) -> float:
    return self._last_recv_time

  def _edge(self, current: bool, previous: bool) -> bool:
    """Detect rising edge (press, not hold)."""
    return current and not previous

  def poll(self, steer_source: str = "position", max_roll_deg: float = 45.0, max_yaw_deg: float = 60.0) -> ControlCommand | None:
    with self._lock:
      left = self._left
      right = self._right

    if not left.active and not right.active:
      return None

    cmd = ControlCommand(source="udp")
    cmd.deadman = left.grip > 0.5 and right.grip > 0.5

    # Engage / disengage
    if left.button_b or right.button_b:
      self._engaged = False
    elif (left.button_a or right.button_a) and cmd.deadman:
      self._engaged = True
    cmd.engage = self._engaged and cmd.deadman
    cmd.disengage = not cmd.engage

    # Steering: prefer thumbstick (legacy), fallback to hand-pose/quaternion (OpenArmX)
    steer_in = self._compute_steer(left, right, steer_source=steer_source, max_roll_deg=max_roll_deg, max_yaw_deg=max_yaw_deg)
    if abs(steer_in) < self.DEADZONE:
      steer_in = 0.0
    else:
      steer_in = np.sign(steer_in) * ((abs(steer_in) - self.DEADZONE) / (1.0 - self.DEADZONE))
    cmd.steer = float(np.clip(steer_in ** 3 * (1.0 - self.STEER_EXPO) + steer_in * self.STEER_EXPO, -1.0, 1.0))

    # Throttle / brake as tuple (gas, brake) for arbiter scaling
    gas = float(np.clip(right.trigger, 0.0, 1.0))
    brake = float(np.clip(left.trigger, 0.0, 1.0))
    cmd.accel = (gas, brake)

    # Camera view mode switching (rising-edge on buttons)
    btn_a = left.button_a or right.button_a
    btn_b = left.button_b or right.button_b
    btn_x = left.button_x or right.button_x
    btn_y = left.button_y or right.button_y

    # Assist overlay: hold any face button to show center-aligned assist view
    cmd.assist = btn_a or btn_b or btn_x or btn_y

    if self._edge(btn_a, self._prev_btn_a):
      self._current_view = "road"
    elif self._edge(btn_b, self._prev_btn_b):
      self._current_view = "rear"
    elif self._edge(btn_x, self._prev_btn_x):
      self._current_view = "left"
    elif self._edge(btn_y, self._prev_btn_y):
      self._current_view = "right"

    self._prev_btn_a = btn_a
    self._prev_btn_b = btn_b
    self._prev_btn_x = btn_x
    self._prev_btn_y = btn_y

    cmd.view_mode = self._current_view
    self._last_cmd = cmd
    return cmd

  @staticmethod
  def _quat_to_euler(q: np.ndarray) -> tuple:
    """Convert quaternion (qx, qy, qz, qw) to roll, pitch, yaw in radians."""
    x, y, z, w = q
    roll = np.arctan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = np.arcsin(np.clip(2.0 * (w * y - z * x), -1.0, 1.0))
    yaw = np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return roll, pitch, yaw

  def _compute_steer(self, left: UdpControllerState, right: UdpControllerState, steer_source: str = "position", max_roll_deg: float = 45.0, max_yaw_deg: float = 60.0) -> float:
    """Compute steering from thumbstick, hand-pose lateral offset, or quaternion orientation."""
    # 1. Thumbstick has priority (legacy clients)
    if abs(left.thumbstick[0]) > 0.01:
      return float(left.thumbstick[0])

    # 2. OpenArmX quaternion steering (roll/yaw/pitch)
    if steer_source in ("roll", "yaw", "pitch"):
      hand = right if right.active else left
      if hand.active:
        roll, pitch, yaw = self._quat_to_euler(hand.orientation)
        if steer_source == "roll":
          return float(np.clip(np.rad2deg(roll) / max_roll_deg, -1.0, 1.0))
        elif steer_source == "yaw":
          return float(np.clip(np.rad2deg(yaw) / max_yaw_deg, -1.0, 1.0))
        elif steer_source == "pitch":
          return float(np.clip(np.rad2deg(pitch) / max_roll_deg, -1.0, 1.0))
      return 0.0

    # 3. OpenArmX hand-pose steering: right hand lateral offset from calibrated center
    if self._calibrated and right.active:
      dx = float(right.position[0]) - self._calib_center_x
      # Deadband around center
      if abs(dx) < self._calib_deadband_m:
        return 0.0
      # Normalize: 15 cm = full steer
      steer = np.clip(dx / self.HAND_STEER_RANGE_M, -1.0, 1.0)
      return steer

    # 4. Fallback: left hand pose if right not active
    if self._calibrated and left.active:
      dx = float(left.position[0]) - self._calib_center_x
      if abs(dx) < self._calib_deadband_m:
        return 0.0
      return float(np.clip(dx / self.HAND_STEER_RANGE_M, -1.0, 1.0))

    return 0.0

  def stop(self):
    self._running = False
    if self._thread:
      self._thread.join(timeout=1.0)
    if self._sock:
      self._sock.close()
      self._sock = None
