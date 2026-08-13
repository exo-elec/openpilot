"""Quectel EC25 modem configuration for RK3588 platforms.

This module brings up a data-centric cellular connection via ModemManager/mmcli
and direct wwan0 IP configuration. It is decoupled from the hardware HAL so it
can be unit-tested and reused.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast

from cereal import log
from openpilot.common.swaglog import cloudlog
from openpilot.common.util import sudo_write  # noqa: F401


NetworkType = log.DeviceState.NetworkType
NetworkStrength = log.DeviceState.NetworkStrength

_MODEM_CACHE_TTL = 1.0
_modem_lock = threading.RLock()
_modem_cache: dict = {}
_modem_cache_ts: float = 0.0


def _run_nmcli(args: list[str], timeout: float = 5) -> str:
  try:
    return subprocess.run(["nmcli"] + args, capture_output=True, text=True, timeout=timeout).stdout.strip()
  except Exception:
    return ""


def _run_mmcli_json(args: list[str], timeout: float = 5) -> dict:
  try:
    return cast(dict, json.loads(subprocess.run(["mmcli", "-J"] + args, capture_output=True, text=True, timeout=timeout).stdout))
  except Exception:
    return {}


def _modem_json() -> dict:
  """Cached mmcli -J -m 0 result (1s TTL)."""
  global _modem_cache, _modem_cache_ts
  with _modem_lock:
    now = time.monotonic()
    if now - _modem_cache_ts < _MODEM_CACHE_TTL and _modem_cache:
      return _modem_cache
    _modem_cache = _run_mmcli_json(["-m", "0"])
    _modem_cache_ts = now
    return _modem_cache


def _modem_generic() -> dict:
  return _modem_json().get("modem", {}).get("generic", {}) or {}


def _modem_3gpp() -> dict:
  return _modem_json().get("modem", {}).get("3gpp", {}) or {}


def _sim_not_inserted() -> bool:
  mg = _modem_generic()
  if not mg:
    return False
  sim = mg.get("sim")
  return (not sim or sim == "/") or (mg.get("state-failed-reason") or "").lower() == "sim-missing"


def _extract_kv(text: str, key: str) -> str:
  return next((line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith(key)), "")


def _wwan0_has_ipv4() -> bool:
  """True if wwan0 has a routable IPv4 address."""
  if not Path("/sys/class/net/wwan0").exists():
    return False
  try:
    out = subprocess.run(
      ["ip", "-4", "-o", "addr", "show", "dev", "wwan0"],
      capture_output=True, text=True, timeout=2,
    ).stdout
  except Exception:
    return False
  if " inet " not in out:
    return False
  addr = out.split(" inet ", 1)[1].split()[0].split("/")[0]
  return bool(addr) and not addr.startswith("127.")


def _modem_network_type() -> int | None:
  """Return cellular NetworkType from ModemManager state, or None."""
  mg = _modem_generic()
  if mg.get("state", "").lower() != "connected":
    return None
  at = mg.get("access-technologies", [])
  techs = " ".join(at).lower() if isinstance(at, list) else str(at).lower()
  if "lte" in techs:
    return cast(int, NetworkType.cell4G)
  if any(x in techs for x in ("umts", "hspa")):
    return cast(int, NetworkType.cell3G)
  return cast(int, NetworkType.cell2G)


def _get_bearer_info(mid: str) -> tuple[str, str, str, str, str] | None:
  """Parse newest IPv4 bearer from mmcli for QMI-style EC25 modems."""
  bearer_re = re.compile(r"/org/freedesktop/ModemManager1/Bearer/\d+")
  for _ in range(30):
    out = subprocess.run(["mmcli", "-m", mid], capture_output=True, text=True).stdout
    if not out:
      time.sleep(1)
      continue
    paths = sorted(set(bearer_re.findall(out)), key=lambda p: int(p.rsplit("/", 1)[-1]), reverse=True)
    if not paths:
      time.sleep(1)
      continue
    for bpath in paths:
      bkv = subprocess.run(["mmcli", "-b", bpath, "--output-keyvalue"], capture_output=True, text=True).stdout
      if not bkv:
        continue
      addr = _extract_kv(bkv, "bearer.ipv4-config.address")
      prefix = _extract_kv(bkv, "bearer.ipv4-config.prefix")
      gw = _extract_kv(bkv, "bearer.ipv4-config.gateway")
      if not addr or not prefix or not prefix.isdigit() or not gw:
        continue
      return (
        addr,
        prefix,
        gw,
        _extract_kv(bkv, "bearer.ipv4-config.dns.value[1]"),
        _extract_kv(bkv, "bearer.ipv4-config.dns.value[2]"),
      )
    time.sleep(1)
  return None


def find_modem_id() -> str:
  for _ in range(30):
    out = subprocess.run(["mmcli", "-L"], capture_output=True, text=True).stdout
    if out:
      if m := re.search(r"/Modem/(\d+)", out):
        return m.group(1)
    time.sleep(1)
  cloudlog.error("modem: no modem found after 30s")
  return ""


def lookup_apn(mcc_mnc: str) -> str:
  """Look up APN from mobile-broadband-provider-info by MCC/MNC."""
  if not mcc_mnc or len(mcc_mnc) < 5:
    return ""
  mcc, mnc = mcc_mnc[:3], mcc_mnc[3:]
  try:
    root = ET.parse("/usr/share/mobile-broadband-provider-info/serviceproviders.xml").getroot()
    for provider in root.iter("provider"):
      if any(n.get("mcc") == mcc and n.get("mnc") == mnc for n in provider.iter("network-id")):
        return cast(str, next((a.get("value") for a in provider.iter("apn")
                     if any(u.get("type") == "internet" for u in a.iter("usage"))), ""))
  except Exception:
    pass
  return ""


def _run_ip(args: list[str]) -> bool:
  cmd = ["ip", *args]
  return subprocess.run(["sudo"] + cmd, capture_output=True).returncode == 0


def configure_modem() -> bool:
  """Bring up EC25 data session.

  Steps:
    1. Find modem
    2. Verify SIM inserted
    3. Use manual GsmApn param or auto-lookup APN
    4. Set data-centric NV items (only if not already set)
    5. Connect bearer via mmcli
    6. Configure wwan0 IP/route/DNS

  Returns True if a bearer was configured and wwan0 has IPv4.
  """
  mid = find_modem_id()
  if not mid:
    return False

  if _sim_not_inserted():
    cloudlog.warning("modem: SIM not inserted")
    return False

  from openpilot.common.params import Params
  params = Params()
  apn_raw = params.get("GsmApn")
  apn = apn_raw.decode() if isinstance(apn_raw, bytes) else (apn_raw or "")
  apn_source = "manual"
  if not apn:
    sim_info = get_sim_info()
    mcc_mnc = str((sim_info or {}).get("mcc_mnc", "")).strip()
    apn = lookup_apn(mcc_mnc)
    apn_source = "auto"

  if not apn:
    cloudlog.warning("modem: no APN found")
    return False

  cloudlog.info(f"modem: {apn_source} APN {apn}")

  # Stop NM from managing wwan0 while mmcli configures it
  subprocess.run(["nmcli", "-w", "10", "device", "disconnect", "wwan0"], capture_output=True)

  # Data-centric NV configuration; write only if current value differs.
  nv_cmds = [
    {"target": "/nv/item_files/modem/mmode/ue_usage_setting", "val": "01", "is_file": True},
    {"target": "5280", "val": "0102000000000000", "is_file": False},
    {"target": "/nv/item_files/ims/IMS_enable", "val": "00", "is_file": True},
  ]
  for nv in nv_cmds:
    t, v = nv["target"], nv["val"]
    is_file = nv["is_file"]
    if is_file:
      r = f'AT+QNVFR="{t}"'
      w = f'AT+QNVFW="{t}",{v}'
      pattern = rf'\+QNVFR:\s*(?:"[^"]+",\s*)?{v}\b'
    else:
      r = f"AT+QNVR={t},0"
      w = f'AT+QNVW={t},0,"{v}"'
      pattern = rf'\+QNVR:\s*(?:\d+,\d+,\s*)?"?{v}"?\b'

    read_out = subprocess.run(["mmcli", "-m", mid, f"--command={r}"], capture_output=True, text=True).stdout
    if re.search(pattern, read_out):
      continue
    write_out = subprocess.run(["mmcli", "-m", mid, f"--command={w}"], capture_output=True, text=True).stdout
    if "OK" not in write_out:
      cloudlog.warning(f"modem: NV write failed: {w}")

  subprocess.run(["mmcli", "-m", mid, "--simple-disconnect"], capture_output=True, text=True)
  time.sleep(1)

  conn_cmd = f"--simple-connect=apn={apn},ip-type=ipv4"
  conn_out = subprocess.run(["mmcli", "-m", mid, conn_cmd], capture_output=True, text=True).stdout.lower()
  if "error" in conn_out:
    cloudlog.error(f"modem: connect failed: {conn_out}")
    return False

  b = _get_bearer_info(mid)
  if not b:
    cloudlog.error("modem: bearer parse failed")
    return False
  addr, prefix, gw, dns1, dns2 = b

  _run_ip(["addr", "flush", "dev", "wwan0"])
  _run_ip(["link", "set", "wwan0", "up"])
  _run_ip(["addr", "replace", f"{addr}/{prefix}", "dev", "wwan0"])
  _run_ip(["route", "replace", "default", "via", gw, "dev", "wwan0", "onlink", "metric", "2000"])

  resolvectl = shutil.which("resolvectl")
  if (dns1 or dns2) and resolvectl:
    dns_args = ["sudo", resolvectl, "dns", "wwan0"]
    if dns1:
      dns_args.append(dns1)
    if dns2:
      dns_args.append(dns2)
    subprocess.run(dns_args, capture_output=True)
    subprocess.run(["sudo", resolvectl, "domain", "wwan0", "~."], capture_output=True)

  return _wwan0_has_ipv4()


def get_modem_data_usage() -> tuple[int, int]:
  """Return (tx_bytes, rx_bytes) from wwan0 sysfs counters."""
  try:
    tx = int(Path("/sys/class/net/wwan0/statistics/tx_bytes").read_text().strip())
    rx = int(Path("/sys/class/net/wwan0/statistics/rx_bytes").read_text().strip())
    return tx, rx
  except Exception:
    return -1, -1


def get_modem_version() -> str | None:
  mg = _modem_generic()
  return str(mg.get("revision", "")) or None


def get_imei() -> str:
  """Return modem equipment identifier (IMEI)."""
  mg = _modem_generic()
  return str(mg.get("equipment-identifier", ""))


def get_sim_info() -> dict:
  mg = _modem_generic()
  if not mg:
    return {
      "sim_id": "",
      "mcc_mnc": None,
      "network_type": ["Unknown"],
      "sim_state": ["ABSENT"],
      "data_connected": False,
    }

  sim_path = mg.get("sim")
  if not sim_path or sim_path == "/":
    return {
      "sim_id": "",
      "mcc_mnc": None,
      "network_type": ["Unknown"],
      "sim_state": ["ABSENT"],
      "data_connected": False,
    }

  sim_props = _run_mmcli_json(["-i", sim_path.split('/')[-1]]).get("sim", {}).get("properties", {}) or {}
  state_ok = mg.get("state", "").lower() == "connected"
  packet_attached = str(_modem_3gpp().get("packet-service-state", "")).lower() == "attached"
  return {
    "sim_id": str(sim_props.get("iccid", "")),
    "mcc_mnc": str(sim_props.get("operator-code", "")),
    "network_type": ["Unknown"],
    "sim_state": ["READY"],
    "data_connected": state_ok or packet_attached or _wwan0_has_ipv4(),
  }


def get_network_type() -> int:
  """Return active network type, preferring Ethernet > Wi-Fi > Cellular."""
  out = _run_nmcli(["-t", "-f", "TYPE,STATE,NAME", "connection", "show", "--active"])
  if out:
    has_ethernet = False
    has_wifi = False
    has_gsm = False
    for line in out.splitlines():
      parts = line.split(":")
      conn_type = parts[0]
      conn_name = parts[2] if len(parts) > 2 else ""
      if "ethernet" in conn_type:
        has_ethernet = True
      elif "wireless" in conn_type and conn_name != "Hotspot":
        has_wifi = True
      elif conn_type == "gsm":
        has_gsm = True

    if has_ethernet:
      return cast(int, NetworkType.ethernet)
    if has_wifi:
      return cast(int, NetworkType.wifi)
    if has_gsm:
      nt = _modem_network_type()
      if nt is not None:
        return nt

  nt = _modem_network_type()
  return nt if nt is not None else cast(int, NetworkType.none)


def get_network_strength(network_type: int) -> int:
  if network_type == NetworkType.wifi:
    out = _run_nmcli(["-t", "-f", "IN-USE,SIGNAL", "dev", "wifi"])
    if out:
      for line in out.splitlines():
        if line.startswith('*') and len(parts := line.split(':')) > 1:
          return _parse_strength(int(parts[-1]))
  elif network_type != NetworkType.none:
    quality = _modem_generic().get("signal-quality", {}).get("value", 0)
    if quality:
      return _parse_strength(int(quality))
  return cast(int, NetworkStrength.unknown)


def _parse_strength(percentage: int) -> int:
  if percentage < 25:
    return cast(int, NetworkStrength.poor)
  if percentage < 50:
    return cast(int, NetworkStrength.moderate)
  if percentage < 75:
    return cast(int, NetworkStrength.good)
  return cast(int, NetworkStrength.great)


def get_network_info() -> dict | None:
  try:
    raw = subprocess.run(["mmcli", "-m", "0", "--command=AT+QNWINFO"],
                         capture_output=True, text=True, timeout=2).stdout
    if raw and "response: '" in raw:
      m = raw.split("response: '")[1].split("'")[0]
      if m.startswith("+QNWINFO: "):
        info_list = m.replace("+QNWINFO: ", "").replace('"', "").split(",")
        if len(info_list) == 4:
          ex_raw = subprocess.run(["mmcli", "-m", "0", '--command=AT+QENG="servingcell"'],
                                  capture_output=True, text=True, timeout=2).stdout
          ex = ex_raw.split("response: '")[1].split("'")[0] if "response: '" in ex_raw else ""
          return {
            "technology": info_list[0],
            "operator": info_list[1],
            "band": info_list[2],
            "channel": int(info_list[3]),
            "extra": ex.replace('+QENG: "servingcell",', "").replace('"', ""),
            "state": _modem_generic().get("state", "unknown").upper(),
          }
  except Exception:
    pass
  return None


def get_modem_temperatures() -> list[int]:
  try:
    res = subprocess.run(["mmcli", "-m", "0", "--command=AT+QTEMP"],
                         capture_output=True, text=True, timeout=5).stdout
    if "response: '" in res:
      return list(map(int, res.split("response: '")[1].split("'")[0].split(' ')[1].split(',')))
  except (IndexError, ValueError, Exception):
    pass
  return []


def get_network_metered(network_type: int) -> bool:
  return network_type in (NetworkType.cell2G, NetworkType.cell3G, NetworkType.cell4G, NetworkType.cell5G)
