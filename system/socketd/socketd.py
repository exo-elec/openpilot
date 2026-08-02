#!/usr/bin/env python3
"""SocketCAN and OpenDBC vehicle daemon for BrownPanda hardware.

Socketd owns the SocketCAN bridge and vehicle adapter loop. BrownPanda remains
the final hardware safety layer.

The bridge proxies CAN <-> messaging (can/sendcan); the co-located adapter
publishes carState/carOutput and consumes carControl.
Works with native CAN (RK3588 SocketCAN) and other SocketCAN interfaces.

SAFETY ARCHITECTURE:
    Layer 1 (Software): socketd vehicle safety - tighter limits
    Layer 2 (Hardware): BrownPanda Gateway - hardware enforcement
    
This module (socketd) has NO safety - it just bridges CAN traffic.
"""
from __future__ import annotations

import os
import socket
import struct
import threading
import time
from collections.abc import Iterable, Sequence

import cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE
from .can_capnp import encode_can_id, sanitize_can_id

# Typing alias used for CAN tuples (address, data, bus)
CANMsg = tuple[int, bytes, int]

CAN_HEADER_FMT = "=IBB2x"
CAN_HEADER_LEN = struct.calcsize(CAN_HEADER_FMT)
CAN_MAX_DLEN = 8
CANFD_MAX_DLEN = 64
CANFD_BRS = 0x01
CANFD_FDF = 0x04
SO_RXQ_OVFL = 40
DEFAULT_BITRATE = int(os.getenv("SOCKETCAN_BITRATE", str(HARDWARE.get_can_bitrate())))


def create_raw_socket(interface: str, recv_buffer_size: int, fd: bool) -> socket.socket:
  """Create a raw SocketCAN socket following BSP proven patterns.
  
  Implementation follows LubanCat BSP quick_start/can/can_send.c:
  - Sets receive buffer size
  - Enables CAN-FD frames if requested  
  - Sets CAN_RAW_FILTER to receive all frames (NULL filter)
  - Binds to specified interface
  """
  sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
  if fd:
    sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FD_FRAMES, 1)
  
  # Set receive buffer size (BSP pattern)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, recv_buffer_size)
  
  # Enable overflow reporting (optional, for debugging)
  try:
    sock.setsockopt(socket.SOL_SOCKET, SO_RXQ_OVFL, 1)
  except OSError:
    pass
  
  # BSP Pattern: Set CAN filter to receive all frames
  # Empty filter list = receive all frames
  try:
    sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, b'')
  except OSError:
    pass
  
  # Bind to interface - Python internally converts name to ifindex
  sock.bind((interface,))
  return sock


class candevice:
  """Simple wrapper around a raw CAN socket (used by socketd).
  
  Thread-safe for send operations. Uses BSP-proven SocketCAN patterns.
  """

  def __init__(self, interface: str, bus: int, fd: bool = False, recv_buffer_size: int = 212992) -> None:
    self.interface = interface
    self.bus = bus
    self.fd = fd
    self.flags = CANFD_BRS | CANFD_FDF if fd else 0
    self.data_len = CANFD_MAX_DLEN if fd else CAN_MAX_DLEN
    self.recv_buffer_size = recv_buffer_size
    self._send_lock = threading.Lock()
    self._closed = False
    self.socket = create_raw_socket(interface, recv_buffer_size, fd)

  def send(self, address: int, data: bytes) -> None:
    """Send a CAN frame. Thread-safe."""
    if self._closed:
      raise RuntimeError(f"CAN device {self.interface} is closed")
    
    msg_len = len(data)
    if msg_len > self.data_len:
      raise ValueError(f"CAN payload too large for bus {self.bus}: {msg_len} bytes")
    
    can_id = encode_can_id(address)
    # For non-FD CAN, always use 8-byte padding (CAN frame structure)
    pad_len = CANFD_MAX_DLEN if self.fd else CAN_MAX_DLEN
    frame = struct.pack(CAN_HEADER_FMT, can_id, msg_len, self.flags) + data.ljust(pad_len, b'\x00')
    
    with self._send_lock:
      if self._closed:
        raise RuntimeError(f"CAN device {self.interface} is closed")
      self.socket.send(frame)

  def recv(self) -> list[CANMsg]:
    """Receive all available CAN frames (non-blocking).
    
    Returns list of (address, data, bus) tuples.
    """
    if self._closed:
      return []
    
    msgs: list[CANMsg] = []
    max_frames_per_call = 100  # Prevent starvation
    
    for _ in range(max_frames_per_call):
      try:
        payload = self.socket.recv(self.recv_buffer_size, socket.MSG_DONTWAIT)
        if not payload:
          break
        
        if len(payload) < CAN_HEADER_LEN:
          continue
          
        can_id, msg_len, flags = struct.unpack(CAN_HEADER_FMT, payload[:CAN_HEADER_LEN])
        data = payload[CAN_HEADER_LEN:CAN_HEADER_LEN + msg_len]
        
        address = sanitize_can_id(can_id)
        if address is None:
          continue
        msgs.append((address, data, self.bus))
        
      except BlockingIOError:
        break
      except OSError as e:
        cloudlog.warning(f"CAN recv error on {self.interface}: {e}")
        break
        
    return msgs

  def close(self) -> None:
    """Close the CAN device and release resources."""
    with self._send_lock:
      if self._closed:
        return
      self._closed = True
      try:
        self.socket.close()
      except Exception:
        pass

  def __del__(self) -> None:
    self.close()


class cansend:
  """Thread-safe helper that multicasts CAN frames to SocketCAN interfaces.

  Follows Linux SocketCAN naming convention (cansend utility).
  """

  def __init__(self, interfaces: Sequence[str] | None = None) -> None:
    if interfaces is None:
      interfaces = HARDWARE.get_can_interfaces()
    if not interfaces:
      raise RuntimeError("No SocketCAN interfaces found for direct TX")

    self._lock = threading.Lock()
    self._interfaces = list(interfaces)
    self._devices = [candevice(interface=iface, bus=idx) for idx, iface in enumerate(self._interfaces)]

  def send_batch(self, can_msgs: Iterable[CANMsg]) -> None:
    with self._lock:
      for address, data, bus in can_msgs:
        if 0 <= bus < len(self._devices):
          self._devices[bus].send(address, data)

  def close(self) -> None:
    with self._lock:
      for device in self._devices:
        device.close()
      self._devices = []
      self._interfaces = []

  def __del__(self) -> None:
    self.close()

  @property
  def interfaces(self) -> Sequence[str]:
    return list(self._interfaces)


class canbridge:
  """PLAIN CAN BRIDGE - No safety checks.
  
  Bridges SocketCAN messages to/from the messaging system.
  Safety is handled by socketd (Layer 1) and BrownPanda (Layer 2).
  """

  def __init__(self, devices: list[candevice], enable_send: bool = True):
    self.devices = devices
    self.enable_send = enable_send
    self.running = False

    self.pm = messaging.PubMaster(['can'])
    self.sm = messaging.SubMaster(['sendcan'])
    self.threads: list[threading.Thread] = []
    
    # Stats for monitoring
    self._recv_count = 0
    self._send_count = 0
    self._error_count = 0

  def start(self) -> None:
    if self.running:
      return
    self.running = True

    for device in self.devices:
      thread = threading.Thread(
        target=self._can_recv_loop, 
        args=(device,), 
        name=f"CAN-Recv-{device.interface}", 
        daemon=True
      )
      thread.start()
      self.threads.append(thread)

    if self.enable_send:
      send_thread = threading.Thread(
        target=self._can_send_loop, 
        name="CAN-Send", 
        daemon=True
      )
      send_thread.start()
      self.threads.append(send_thread)

    cloudlog.info(f"CAN bridge started ({len(self.devices)} interfaces, send={'enabled' if self.enable_send else 'disabled'})")

  def stop(self) -> None:
    """Stop the CAN bridge and clean up resources."""
    self.running = False
    for thread in self.threads:
      thread.join(timeout=2.0)
    cloudlog.info(f"CAN bridge stopped (recv={self._recv_count}, send={self._send_count}, errors={self._error_count})")

  def _can_recv_loop(self, device: candevice) -> None:
    """Receive loop for a CAN interface."""
    cloudlog.info(f"CAN receive loop started for {device.interface}")
    consecutive_errors = 0
    max_consecutive_errors = 10
    
    while self.running:
      try:
        messages = device.recv()
        if messages:
          msg = messaging.new_message('can', len(messages))
          msg.valid = True
          for i, (can_id, data, bus) in enumerate(messages):
            msg.can[i].address = can_id
            msg.can[i].dat = data
            msg.can[i].src = bus
          self.pm.send('can', msg)
          self._recv_count += len(messages)
          consecutive_errors = 0  # Reset on success
      except Exception as e:
        consecutive_errors += 1
        self._error_count += 1
        cloudlog.warning(f"CAN receive error on {device.interface} ({consecutive_errors}/{max_consecutive_errors}): {e}")
        if consecutive_errors >= max_consecutive_errors:
          cloudlog.error(f"Too many consecutive errors on {device.interface}, stopping receive loop")
          break
        time.sleep(0.01)
      time.sleep(0.0001)  # 100us yield

  def _can_send_loop(self) -> None:
    """Transmit loop: forwards sendcan messages to SocketCAN."""
    cloudlog.info("CAN send loop started (socketd safety + BrownPanda)")
    consecutive_errors = 0
    max_consecutive_errors = 10
    
    while self.running:
      try:
        self.sm.update(0)
        
        if self.sm.updated['sendcan']:
          sendcan_msg = self.sm['sendcan']
          for can_msg in sendcan_msg.can:
            bus = can_msg.src
            if 0 <= bus < len(self.devices):
              try:
                self.devices[bus].send(can_msg.address, bytes(can_msg.dat))
                self._send_count += 1
              except Exception as e:
                self._error_count += 1
                cloudlog.warning(f"CAN send error on bus {bus}: {e}")
            else:
              cloudlog.warning(f"Invalid bus number: {bus} (max {len(self.devices) - 1})")
        consecutive_errors = 0  # Reset on success
      except Exception as e:
        consecutive_errors += 1
        self._error_count += 1
        cloudlog.warning(f"CAN send loop error ({consecutive_errors}/{max_consecutive_errors}): {e}")
        if consecutive_errors >= max_consecutive_errors:
          cloudlog.error("Too many consecutive send errors, stopping send loop")
          break
        time.sleep(0.01)
      time.sleep(0.0001)  # 100us yield


class SocketD:
  """Main SocketCAN + vehicle adapter daemon."""

  def __init__(self) -> None:
    # CAN bridge runs on A76 big cores
    from openpilot.common.core_config import set_daemon_affinity
    set_daemon_affinity("socketd")
    
    self.devices: list[candevice] = []
    self.running = False
    self.message_bridge: canbridge | None = None
    self.vehicle = None
    self.disable_bridge_tx = os.getenv("SOCKETCAN_DISABLE_BRIDGE_TX", "0").lower() in ("1", "true", "on")

  def initialize(self) -> None:
    cloudlog.info("Initializing socketd CAN bridge and OpenDBC vehicle adapter")

    interfaces = HARDWARE.get_can_interfaces()
    if not interfaces:
      raise RuntimeError("No SocketCAN interfaces found")
    cloudlog.info(f"Found CAN interfaces: {interfaces}")

    status = HARDWARE.get_can_interface_status()
    for iface, info in status.items():
      cloudlog.info(f"{iface}: {'UP' if info['up'] else 'DOWN'}")

    for idx, interface in enumerate(interfaces):
      try:
        if not HARDWARE.configure_can_interface(interface, DEFAULT_BITRATE, fd=False):
          cloudlog.warning(f"Skipping {interface}; failed to configure")
          continue
        device = candevice(interface=interface, bus=idx)
        self.devices.append(device)
        cloudlog.info(f"CAN device ready: {interface} (bus {idx})")
      except Exception as e:
        cloudlog.error(f"Failed to create CAN device for {interface}: {e}")

    if not self.devices:
      raise RuntimeError("Failed to initialize any SocketCAN interfaces")

    enable_bridge_send = not self.disable_bridge_tx
    self.message_bridge = canbridge(self.devices, enable_send=enable_bridge_send)
    
    if enable_bridge_send:
      cloudlog.info("CAN bridge initialized (vehicle adapter + BrownPanda safety)")
    else:
      cloudlog.info("CAN bridge initialized (TX forwarding disabled)")

  def start(self) -> None:
    if not self.devices:
      raise RuntimeError("No CAN devices initialized - call initialize() first")

    self.running = True
    cloudlog.info(f"Starting socketd with {len(self.devices)} interfaces")

    if self.message_bridge:
      self.message_bridge.start()

    try:
      from openpilot.system.socketd.vehicle import Car
      self.vehicle = Car()
      self.vehicle.card_thread()
    except KeyboardInterrupt:
      cloudlog.info("Shutting down SocketCAN daemon")
      self.stop()

  def stop(self) -> None:
    self.running = False
    if self.message_bridge:
      self.message_bridge.stop()
    for device in self.devices:
      device.close()
    cloudlog.info("SocketCAN daemon stopped")


def main() -> int:
  try:
    daemon = SocketD()
    daemon.initialize()
    daemon.start()
  except Exception as e:
    cloudlog.exception(f"Fatal error in SocketCAN daemon: {e}")
    return 1
  return 0


if __name__ == "__main__":
  exit(main())
