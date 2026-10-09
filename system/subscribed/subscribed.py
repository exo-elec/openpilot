#!/usr/bin/env python3
"""
subscribed — Subscription, Remote Command & Upload Bridge Daemon
=================================================================
A single daemon that replaces both comma's `athenad` (cloud agent)
and `prime` (subscription manager) for the EOP/NavPilot ecosystem.

Responsibilities
----------------
1. Hardware authentication   — eMMC CID / RK OTP fingerprinting
2. Subscription management   — tier, expiry, NavPilot pairing state
3. JSON-RPC command dispatch — remote commands over BLE SPP channel
4. Upload queue management   — stage files for NavPilot to drain
5. Subscription state pub    — 1 Hz cereal message for UI/gating

Transport
---------
- Commands arrive via BLE SPP (system/bluetoothd)
- subscribed reads/writes params that bluetoothd updates
- Upload items are queued locally; NavPilot drains via BLE binary frames

Architecture Comparison
-----------------------
  comma athena        →  WebSocket → athena.comma.ai  → comma connect
  EOP subscribed      →  BLE SPP   → NavPilot (phone)  → user cloud (optional)

Usage
-----
  Managed by system/manager/process_config.py (always_run).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from queue import PriorityQueue, Empty
from typing import Any

from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog

try:
  import cereal.messaging as messaging
  from cereal import log
except ImportError:
  messaging = None
  log = None

# Reuse the hardware fingerprint utility
from openpilot.system.hardware.rk_device_id import compute_device_fingerprint


# =============================================================================
# Enums
# =============================================================================

class SubscriptionTier(IntEnum):
  NONE = 0
  BASIC = 1
  PREMIUM = 2


class SubscriptionStatus(IntEnum):
  UNKNOWN = 0
  INVALID = 1
  EXPIRED = 2
  ACTIVE_BASIC = 3
  ACTIVE_PREMIUM = 4


# =============================================================================
# Upload Queue (adapted from comma athena)
# =============================================================================

@dataclass(order=True)
class UploadItem:
  """Priority queue item for file uploads."""
  # priority field must be first for PriorityQueue sorting
  priority: int
  path: str = field(compare=False)
  url: str = field(compare=False)
  headers: dict = field(compare=False, default_factory=dict)
  created_at: float = field(compare=False, default_factory=time.time)  # noqa: TID251
  id: str | None = field(compare=False, default=None)
  retry_count: int = field(compare=False, default=0)
  current: bool = field(compare=False, default=False)
  progress: float = field(compare=False, default=0.0)
  allow_cellular: bool = field(compare=False, default=True)


# =============================================================================
# JSON-RPC Dispatcher
# =============================================================================

class JSONRPCDispatcher:
  """Minimal JSON-RPC 2.0 dispatcher (inspired by comma athena)."""

  def __init__(self):
    self._methods: dict[str, Callable] = {}

  def add_method(self, fn: Callable) -> Callable:
    """Decorator to register a method."""
    self._methods[fn.__name__] = fn
    return fn

  def handle(self, request: dict) -> dict | None:
    """Handle a JSON-RPC request dict. Returns response or None for notifications."""
    req_id = request.get("id")
    method_name = request.get("method")
    params = request.get("params", [])

    if not isinstance(params, (list, dict)):
      return self._error(req_id, -32602, "Invalid params")

    if method_name not in self._methods:
      return self._error(req_id, -32601, f"Method not found: {method_name}")

    try:
      if isinstance(params, dict):
        result = self._methods[method_name](**params)
      else:
        result = self._methods[method_name](*params)
      if req_id is not None:
        return {"jsonrpc": "2.0", "result": result, "id": req_id}
      return None  # notification
    except Exception as e:
      cloudlog.exception(f"subscribed: RPC {method_name} failed")
      if req_id is not None:
        return self._error(req_id, -32000, str(e))
      return None

  @staticmethod
  def _error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id}


# =============================================================================
# Main Daemon
# =============================================================================

class SubscribedD:
  """Subscription + command + upload daemon."""

  DEFAULT_CHECK_INTERVAL = 30.0
  GRACE_PERIOD_S = 300.0
  UPLOAD_QUEUE_MAX = 1000
  BLOB_CHUNK_SIZE = 65536

  def __init__(self):
    self.params = Params()
    self._exit_event = threading.Event()

    # Messaging
    if messaging:
      self.pm = messaging.PubMaster(["subscriptionState"])
      self.sm = messaging.SubMaster(["deviceState", "sppStatus", "carState"])
    else:
      self.pm = None
      self.sm = None

    # State
    self._tier = SubscriptionTier.NONE
    self._status = SubscriptionStatus.UNKNOWN
    self._device_id: str = "unknown"
    self._expiry: str = ""
    self._navpilot_paired = False
    self._navpilot_addr: str = ""
    self._last_check = 0.0

    # Upload queue (thread-safe)
    self._upload_queue: PriorityQueue[UploadItem] = PriorityQueue(maxsize=self.UPLOAD_QUEUE_MAX)
    self._upload_current: UploadItem | None = None
    self._upload_thread: threading.Thread | None = None

    # RPC dispatcher
    self._rpc = JSONRPCDispatcher()
    self._register_rpc_methods()

    # Hardware fingerprint (computed once)
    self._hw_fingerprint: dict[str, Any] | None = None

  # ------------------------------------------------------------------
  # Hardware identity
  # ------------------------------------------------------------------

  def _load_hardware_fingerprint(self) -> dict[str, Any]:
    try:
      fp = compute_device_fingerprint()
      cloudlog.info(f"subscribed: HW fingerprint source={fp['primary_source']} id={fp['primary_id']}")
      return fp
    except Exception as e:
      cloudlog.error(f"subscribed: HW fingerprint failed: {e}")
      return {"primary_id": "unknown", "primary_source": "none", "platform": "unknown"}

  def _get_device_id(self) -> str:
    if self._hw_fingerprint is None:
      self._hw_fingerprint = self._load_hardware_fingerprint()
    return self._hw_fingerprint.get("primary_id") or "unknown"

  # ------------------------------------------------------------------
  # Signature verification (offline official-hardware check)
  # ------------------------------------------------------------------

  def _verify_subscription_token(self, device_id: str, token: str | None) -> tuple[bool, SubscriptionTier]:
    """Verify a NavPilot-issued subscription token.

    Business model: openpilot device code is open source, but NavPilot
    (mobile app + backend) is the monetization layer. Users pay monthly
    for NavPilot BASIC/PREMIUM. The NavPilot backend signs a JWT-like
    token containing {device_id, tier, expiry} with a private key. This
    daemon verifies it with the PUBLIC key baked into the open-source
    device image.

    Because the public key is in open source, a determined developer can
    patch the check — but they'd also need to recompile and maintain a
    fork. For most users, paying the subscription is far easier.

    Anti-sharing: the token contains the device_id (eMMC CID). A token
    issued for device A will NOT validate on device B.
    """
    if self.params.get_bool("EOPSubscriptionSkipVerify"):
      return True, SubscriptionTier.PREMIUM

    if not token:
      return False, SubscriptionTier.NONE

    # TODO: replace with real Ed25519 / ECDSA verification against
    # a public key embedded at build time. For now we use a simple
    # HMAC-like construction so the architecture is in place.
    #
    # PRODUCTION: Use PyNaCl (nacl.signing) or cryptography library
    # to verify a JWT signed by NavPilot backend.
    build_secret = os.getenv("EOP_BUILD_SECRET", "")
    if not build_secret:
      return False, SubscriptionTier.NONE

    try:
      payload = json.loads(token)
      if payload.get("deviceId") != device_id:
        return False, SubscriptionTier.NONE

      expected = hashlib.sha256(
        f"{payload.get('deviceId')}:{payload.get('tier')}:{payload.get('expiry')}:{build_secret}".encode()
      ).hexdigest()
      if payload.get("sig") == expected:
        tier = SubscriptionTier(int(payload.get("tier", 0)))
        return True, tier
    except Exception:
      pass

    return False, SubscriptionTier.NONE

  # ------------------------------------------------------------------
  # Subscription state machine
  # ------------------------------------------------------------------

  def _load_params(self) -> None:
    tier_str = self.params.get("EOPSubscriptionTier") or "none"
    self._tier = {
      "none": SubscriptionTier.NONE,
      "basic": SubscriptionTier.BASIC,
      "premium": SubscriptionTier.PREMIUM,
    }.get(tier_str.lower(), SubscriptionTier.NONE)

    self._expiry = self.params.get("EOPSubscriptionExpiry") or ""
    self._navpilot_paired = self.params.get_bool("EOPNavPilotPaired")
    self._navpilot_addr = self.params.get("EOPNavPilotAddress") or ""

  def _save_params(self) -> None:
    tier_map = {SubscriptionTier.NONE: "none", SubscriptionTier.BASIC: "basic", SubscriptionTier.PREMIUM: "premium"}
    self.params.put("EOPSubscriptionTier", tier_map.get(self._tier, "none"))
    self.params.put("EOPSubscriptionExpiry", self._expiry)
    self.params.put_bool("EOPNavPilotPaired", self._navpilot_paired)
    self.params.put("EOPNavPilotAddress", self._navpilot_addr)

  def _is_expired(self) -> bool:
    if not self._expiry:
      return False
    try:
      expiry_dt = datetime.fromisoformat(self._expiry.replace("Z", "+00:00"))
      now = datetime.now(timezone.utc)
      return (now - expiry_dt).total_seconds() > self.GRACE_PERIOD_S
    except ValueError:
      return True

  def _validate(self) -> SubscriptionStatus:
    device_id = self._get_device_id()
    if device_id == "unknown":
      return SubscriptionStatus.INVALID

    current_stored = self.params.get("EOPSubscriptionDeviceId") or ""
    if device_id != current_stored:
      self.params.put("EOPSubscriptionDeviceId", device_id)

    token = self.params.get("EOPSubscriptionToken")
    is_valid, token_tier = self._verify_subscription_token(device_id, token)

    # If NavPilot is not paired, downgrade to free tier unless we have
    # a valid standalone token (e.g., lifetime license)
    if not self._navpilot_paired:
      if is_valid and self._tier == SubscriptionTier.PREMIUM:
        # Allow premium without active BLE if token says so
        pass
      else:
        self._tier = SubscriptionTier.NONE
        self._update_feature_gates()
        return SubscriptionStatus.INVALID

    if self._is_expired():
      self._tier = SubscriptionTier.NONE
      self._update_feature_gates()
      return SubscriptionStatus.EXPIRED

    # Token determines the ceiling; NavPilot param can also set tier
    if is_valid:
      self._tier = min(token_tier, self._tier)
    else:
      # No valid token but NavPilot paired → basic at most
      if self._tier == SubscriptionTier.PREMIUM:
        self._tier = SubscriptionTier.BASIC

    self._update_feature_gates()

    if self._tier == SubscriptionTier.PREMIUM:
      return SubscriptionStatus.ACTIVE_PREMIUM
    if self._tier == SubscriptionTier.BASIC:
      return SubscriptionStatus.ACTIVE_BASIC

    return SubscriptionStatus.INVALID

  def _update_feature_gates(self) -> None:
    """Set per-feature params based on current subscription tier.

    Product strategy (open source device + paid NavPilot app):
      FREE    — Basic ADAS + offline navigation (no NavPilot required)
      BASIC   — Vehicle telemetry dashboard synced to NavPilot
      PREMIUM — AI voice assistant ("Hey Pilot, take me home")
                NavPilot executes voice intents via linkage with ExoPilot

    Other daemons and UI read these params to enable/disable features.
    This is the central gating logic for the entire openpilot system.
    """
    tier = self._tier

    # FREE tier — always enabled (open source core value)
    #   Basic ADAS, lane keeping, ACC, local DVR, offline maps
    #   These are NOT gated — they work even without NavPilot

    # BASIC tier — Telemetry & data features
    self.params.put_bool("EOPFeatureTelemetry", tier >= SubscriptionTier.BASIC)
    self.params.put_bool("EOPFeatureTripSync", tier >= SubscriptionTier.BASIC)
    self.params.put_bool("EOPFeatureRouteReplay", tier >= SubscriptionTier.BASIC)
    self.params.put_bool("EOPFeatureObdDashboard", tier >= SubscriptionTier.BASIC)
    self.params.put_bool("EOPFeatureDrivingScore", tier >= SubscriptionTier.BASIC)

    # PREMIUM tier — advanced integration
    self.params.put_bool("EOPFeatureAiRouting", tier >= SubscriptionTier.PREMIUM)
    self.params.put_bool("EOPFeatureLiveTraffic", tier >= SubscriptionTier.PREMIUM)
    self.params.put_bool("EOPFeatureCloudBackup", tier >= SubscriptionTier.PREMIUM)
    self.params.put_bool("EOPFeatureAdvancedAnalytics", tier >= SubscriptionTier.PREMIUM)
    self.params.put_bool("EOPFeatureFleetMode", tier >= SubscriptionTier.PREMIUM)
    self.params.put_bool("EOPFeatureRemoteCamera", tier >= SubscriptionTier.PREMIUM)
    self.params.put_bool("EOPFeatureLiveLocationShare", tier >= SubscriptionTier.PREMIUM)
    self.params.put_bool("EOPFeatureEmergencyAlert", tier >= SubscriptionTier.PREMIUM)
    self.params.put_bool("EOPFeaturePriorityTiles", tier >= SubscriptionTier.PREMIUM)
    self.params.put_bool("EOPFeatureCrossDeviceSync", tier >= SubscriptionTier.PREMIUM)

    cloudlog.info(f"subscribed: feature gates updated for tier={tier.name}")

  # ------------------------------------------------------------------
  # NavPilot BLE integration
  # ------------------------------------------------------------------

  def _update_navpilot_status(self) -> None:
    paired = self.params.get_bool("EOPNavPilotPaired")
    addr = self.params.get("EOPNavPilotAddress") or ""
    if paired != self._navpilot_paired or addr != self._navpilot_addr:
      self._navpilot_paired = paired
      self._navpilot_addr = addr
      cloudlog.info(f"subscribed: NavPilot paired={paired} addr={addr}")

  def _handle_navpilot_handshake(self, tier: SubscriptionTier, expiry: str, token: str) -> bool:
    """Accept a subscription token from NavPilot and validate it."""
    device_id = self._get_device_id()
    is_valid, token_tier = self._verify_subscription_token(device_id, token)
    if not is_valid:
      cloudlog.warning("subscribed: handshake rejected — invalid token")
      return False

    self._tier = min(tier, token_tier)
    self._expiry = expiry
    self.params.put("EOPSubscriptionToken", token)
    self._save_params()
    cloudlog.info(f"subscribed: handshake accepted tier={self._tier.name} expiry={expiry}")
    return True

  # ------------------------------------------------------------------
  # JSON-RPC Methods (adapted from comma athena)
  # ------------------------------------------------------------------

  def _register_rpc_methods(self) -> None:
    """Register all JSON-RPC methods."""
    self._rpc.add_method(self._rpc_get_message)
    self._rpc.add_method(self._rpc_get_version)
    self._rpc.add_method(self._rpc_take_snapshot)
    self._rpc.add_method(self._rpc_list_data_directory)
    self._rpc.add_method(self._rpc_upload_file_to_url)
    self._rpc.add_method(self._rpc_list_upload_queue)
    self._rpc.add_method(self._rpc_cancel_upload)
    self._rpc.add_method(self._rpc_get_device_info)
    self._rpc.add_method(self._rpc_get_networks)
    self._rpc.add_method(self._rpc_get_subscription_state)
    self._rpc.add_method(self._rpc_set_subscription)

  def _rpc_get_message(self, service: str, timeout: int = 1000) -> dict | None:
    """Fetch latest message from a cereal service (like athena's getMessage)."""
    if self.sm is None:
      return None
    if not self.sm.updated.get(service, False):
      self.sm.update(timeout)
    if self.sm.updated.get(service, False):
      return self.sm[service].to_dict()
    return None

  def _rpc_get_version(self) -> dict[str, str]:
    from openpilot.system.version import get_build_metadata
    bm = get_build_metadata()
    return {
      "version": bm.openpilot.version,
      "gitCommit": bm.openpilot.git_commit,
      "gitBranch": bm.channel,
      "deviceType": self._hw_fingerprint.get("platform", "unknown") if self._hw_fingerprint else "unknown",
    }

  def _rpc_take_snapshot(self, camera: str = "road") -> dict[str, str] | None:
    """Trigger a camera snapshot. Returns base64 JPEG or error."""
    # TODO: integrate with v4l2d snapshot mechanism
    # For now, return a placeholder
    cloudlog.info(f"subscribed: takeSnapshot requested for {camera}")
    return {"status": "queued", "camera": camera}

  def _rpc_list_data_directory(self, prefix: str = "") -> list[str]:
    """List files in /data/media/0/realdata."""
    base = Path("/data/media/0/realdata")
    if not base.exists():
      return []
    paths = sorted(base.rglob("*")) if prefix else sorted(base.iterdir())
    return [str(p.relative_to(base)) for p in paths if p.is_file()]

  def _rpc_upload_file_to_url(self, fn: str, url: str, headers: dict | None = None,
                              allow_cellular: bool = True, priority: int = 99) -> dict:
    """Queue a file for upload (NavPilot will drain)."""
    path = Path("/data/media/0/realdata") / fn
    if not path.exists():
      return {"success": 0, "error": "file not found"}

    item = UploadItem(
      priority=priority,
      path=str(path),
      url=url,
      headers=headers or {},
      allow_cellular=allow_cellular,
      id=hashlib.sha256(f"{path}:{time.time()}".encode()).hexdigest()[:16],  # noqa: TID251
    )

    try:
      self._upload_queue.put(item, block=False)
    except Exception:
      return {"success": 0, "error": "upload queue full"}

    cloudlog.info(f"subscribed: queued upload {item.id} {path} -> {url}")
    return {"success": 1, "id": item.id}

  def _rpc_list_upload_queue(self) -> list[dict]:
    """Return current upload queue items."""
    items: list[dict] = []
    # PriorityQueue doesn't support iteration — snapshot via drain/requeue
    temp: list[UploadItem] = []
    while not self._upload_queue.empty():
      try:
        item = self._upload_queue.get(block=False)
        temp.append(item)
        items.append({
          "id": item.id,
          "path": item.path,
          "url": item.url,
          "progress": item.progress,
          "priority": item.priority,
          "retryCount": item.retry_count,
        })
      except Empty:
        break
    for item in temp:
      self._upload_queue.put(item)
    return items

  def _rpc_cancel_upload(self, upload_id: str) -> dict:
    """Cancel an upload by ID."""
    temp: list[UploadItem] = []
    cancelled = False
    while not self._upload_queue.empty():
      try:
        item = self._upload_queue.get(block=False)
        if item.id == upload_id:
          cancelled = True
          continue
        temp.append(item)
      except Empty:
        break
    for item in temp:
      self._upload_queue.put(item)
    return {"success": 1 if cancelled else 0}

  def _rpc_get_device_info(self) -> dict[str, Any]:
    """Return device identity + subscription info for NavPilot."""
    return {
      "deviceId": self._get_device_id(),
      "hardwareSource": self._hw_fingerprint.get("primary_source", "none") if self._hw_fingerprint else "none",
      "platform": self._hw_fingerprint.get("platform", "unknown") if self._hw_fingerprint else "unknown",
      "tier": self._tier,
      "status": self._status,
      "expiry": self._expiry,
      "navPilotPaired": self._navpilot_paired,
      "version": self._rpc_get_version(),
    }

  def _rpc_get_networks(self) -> list[dict]:
    """Return available WiFi networks (placeholder — delegate to networkd)."""
    # TODO: read from networkd state or wpa_supplicant
    return []

  def _rpc_get_subscription_state(self) -> dict[str, Any]:
    return {
      "tier": int(self._tier),
      "status": int(self._status),
      "expiry": self._expiry,
      "deviceId": self._get_device_id(),
    }

  def _rpc_set_subscription(self, tier: int, expiry: str, token: str) -> bool:
    """NavPilot calls this to activate a subscription tier."""
    try:
      t = SubscriptionTier(tier)
    except ValueError:
      return False
    return self._handle_navpilot_handshake(t, expiry, token)

  # ------------------------------------------------------------------
  # Upload drain thread (sends blobs to NavPilot over BLE)
  # ------------------------------------------------------------------

  def _upload_worker(self) -> None:
    """Background thread: drain upload queue and stage for BLE transfer."""
    while not self._exit_event.is_set():
      try:
        item = self._upload_queue.get(timeout=1.0)
      except Empty:
        continue

      item.current = True
      self._upload_current = item
      cloudlog.info(f"subscribed: uploading {item.id} {item.path}")

      try:
        path = Path(item.path)
        if not path.exists():
          cloudlog.warning(f"subscribed: upload file missing {path}")
          continue

        # Stage upload metadata for bluetoothd to read
        self.params.put("EOPUploadCurrentId", item.id)
        self.params.put("EOPUploadCurrentPath", str(path))
        self.params.put("EOPUploadCurrentUrl", item.url)
        self.params.put("EOPUploadCurrentHeaders", json.dumps(item.headers))
        self.params.put("EOPUploadCurrentSize", str(path.stat().st_size))

        # Wait for bluetoothd to drain this file (or timeout)
        # bluetoothd sets EOPUploadCurrentProgress param as it sends chunks
        deadline = time.monotonic() + 300.0
        while time.monotonic() < deadline:
          progress_str = self.params.get("EOPUploadCurrentProgress") or "0"
          try:
            item.progress = float(progress_str)
          except ValueError:
            item.progress = 0.0

          if item.progress >= 1.0:
            cloudlog.info(f"subscribed: upload complete {item.id}")
            break

          if self._exit_event.is_set():
            break
          time.sleep(1.0)
        else:
          cloudlog.warning(f"subscribed: upload timeout {item.id}")
          item.retry_count += 1
          if item.retry_count < 3:
            self._upload_queue.put(item)

      except Exception as e:
        cloudlog.exception(f"subscribed: upload error {e}")
      finally:
        item.current = False
        self._upload_current = None
        for key in ("EOPUploadCurrentId", "EOPUploadCurrentPath", "EOPUploadCurrentUrl",
                    "EOPUploadCurrentHeaders", "EOPUploadCurrentSize", "EOPUploadCurrentProgress"):
          self.params.remove(key)

  # ------------------------------------------------------------------
  # RPC ingestion from BLE
  # ------------------------------------------------------------------

  def _process_rpc_frame(self, payload: str) -> str | None:
    """Parse JSON-RPC string, dispatch, return JSON response."""
    try:
      req = json.loads(payload)
    except json.JSONDecodeError as e:
      return json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {e}"}, "id": None})

    if isinstance(req, list):
      # Batch request
      results = []
      for r in req:
        res = self._rpc.handle(r)
        if res is not None:
          results.append(res)
      return json.dumps(results) if results else None

    res = self._rpc.handle(req)
    return json.dumps(res) if res is not None else None

  def _poll_ble_rpc(self) -> None:
    """Check for incoming RPC requests from bluetoothd."""
    # bluetoothd writes incoming JSON-RPC frames to params
    # We drain them here and write responses back
    frame = self.params.get("EOPSPPRpcRequest")
    if not frame:
      return

    self.params.remove("EOPSPPRpcRequest")
    response = self._process_rpc_frame(frame)
    if response:
      self.params.put("EOPSPPRpcResponse", response)

  # ------------------------------------------------------------------
  # Publish state
  # ------------------------------------------------------------------

  def _publish(self) -> None:
    if self.pm is None:
      return

    msg = messaging.new_message("subscriptionState")
    ss = msg.subscriptionState

    ss.tier = int(self._tier)
    ss.status = int(self._status)
    ss.deviceId = self._get_device_id()
    ss.expiry = self._expiry
    ss.navPilotPaired = self._navpilot_paired
    ss.navPilotAddress = self._navpilot_addr
    ss.hardwareSource = self._hw_fingerprint.get("primary_source", "none") if self._hw_fingerprint else "none"

    self.pm.send("subscriptionState", msg)

  # ------------------------------------------------------------------
  # Main loop
  # ------------------------------------------------------------------

  def run(self) -> None:
    cloudlog.info("subscribed: daemon starting")

    self._hw_fingerprint = self._load_hardware_fingerprint()
    self._load_params()

    # Start upload worker
    self._upload_thread = threading.Thread(target=self._upload_worker, daemon=True)
    self._upload_thread.start()

    check_interval = float(self.params.get("EOPSubscriptionCheckInterval") or self.DEFAULT_CHECK_INTERVAL)
    rk = Ratekeeper(1.0, print_delay_threshold=None)

    last_validation = 0.0

    while not self._exit_event.is_set():
      now = time.monotonic()

      self._update_navpilot_status()
      self._poll_ble_rpc()

      if now - last_validation >= check_interval:
        self._status = self._validate()
        last_validation = now
        cloudlog.info(f"subscribed: status={self._status.name} tier={self._tier.name}")

      self._publish()
      rk.keep_time()

    cloudlog.info("subscribed: daemon exiting")

  def stop(self) -> None:
    self._exit_event.set()
    if self._upload_thread:
      self._upload_thread.join(timeout=5.0)


def main(exit_event: threading.Event | None = None) -> None:
  daemon = SubscribedD()
  if exit_event:
    daemon._exit_event = exit_event
  daemon.run()


if __name__ == "__main__":
  main()
