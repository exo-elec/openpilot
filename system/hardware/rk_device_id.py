#!/usr/bin/env python3
"""
RK3588 Device ID Extraction Utility
=====================================
Extracts unique hardware identifiers from Rockchip boards for device
authentication and NavPilot official-hardware verification.

Sources (in order of reliability):
1. eMMC CID          - Unique per eMMC chip, persistent across reflashes
2. RK OTP (chip ID)  - SoC-level unique, burned at factory
3. Device-tree serial - Set by U-Boot / DTB (may be generic on clones)
4. CPU serial        - From /proc/cpuinfo (if exposed by kernel)

Usage:
    python3 rk_device_id.py
    python3 rk_device_id.py --format json
    python3 rk_device_id.py --source emmc_cid

Platforms tested:
    - LubanCat-5 (RK3588, ExoPilot 01M)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except OSError:
        return None


def _read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read().strip()
    except OSError:
        return None


def get_dt_serial() -> str | None:
    """Read /proc/device-tree/serial-number (currently used by openpilot).

    WARNING: On many LubanCat/RongPin clones this is set to a generic
    string like 'unknown' or copied from the reference DTB. Do not rely
    on this alone for security.
    """
    data = _read_text("/proc/device-tree/serial-number")
    if data is None:
        return None
    # Device-tree strings are often NUL-terminated
    data = data.strip("\x00")
    return data if data else None


def get_cpu_serial() -> str | None:
    """Read CPU serial from /proc/cpuinfo (ARM-specific field)."""
    data = _read_text("/proc/cpuinfo")
    if data is None:
        return None
    for line in data.splitlines():
        if line.startswith("Serial"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return None


def get_emmc_cid() -> str | None:
    """Read raw eMMC CID (Card Identification Register).

    The CID is 128 bits (32 hex chars) and is UNIQUE per eMMC chip.
    It contains:
      - Manufacturer ID (MID, 8 bits)
      - OEM/Application ID (OID, 16 bits)
      - Product name (PNM, 56 bits)
      - Product revision (PRV, 8 bits)
      - Product serial number (PSN, 32 bits)
      - Manufacturing date (MDT, 12 bits)
      - CRC7 (7 bits)
      - Not used / always 1 (1 bit)

    Returns the full 32-character hex string.
    """
    # eMMC is usually mmcblk0 or mmcblk1 depending on board layout
    for blk in ("mmcblk0", "mmcblk1", "mmcblk2"):
        path = f"/sys/block/{blk}/device/cid"
        data = _read_text(path)
        if data:
            return data
    return None


def parse_emmc_psn(cid_hex: str) -> str | None:
    """Extract Product Serial Number (PSN) from eMMC CID.

    Per JEDEC eMMC spec v5.1:
      CID bits:
        [127:120] MID
        [119:104] OID
        [103: 56] PNM
        [ 55: 48] PRV
        [ 47: 16] PSN   <-- 32-bit serial
        [ 15:  8] MDT
        [  7:  1] CRC7
        [  0:  0] 1

    Layout (bytes, MSB first as stored by Linux kernel):
        byte 0: MID
        byte 1-2: OID
        byte 3-9: PNM
        byte 10: PRV
        byte 11-14: PSN
        byte 15: [MDT[11:4]]
        byte 16-...: usually not present for 128-bit CID

    Wait - Linux exposes CID as a 32-char hex string = 16 bytes.
    Let's map the 16 bytes:
        idx 0:  MID
        idx 1-2: OID
        idx 3-9: PNM (7 bytes)
        idx 10: PRV
        idx 11-14: PSN (4 bytes)
        idx 15: MDT upper + CRC + fixed bit

    PSN is bytes 11-14 (0-indexed). We return it as 8-char hex.
    """
    if len(cid_hex) != 32:
        return None
    try:
        cid_bytes = bytes.fromhex(cid_hex)
        if len(cid_bytes) < 16:
            return None
        psn = cid_bytes[11:15]  # 4 bytes
        return psn.hex()
    except ValueError:
        return None


def get_rk_otp() -> bytes | None:
    """Read Rockchip OTP (One-Time Programmable) memory.

    Modern RK BSP kernels expose OTP via nvmem subsystem:
        /sys/bus/nvmem/devices/rockchip-otp0/nvmem

    The OTP contains a unique chip ID burned at the factory.
    Size varies by SoC (typically 64-256 bytes accessible).
    Requires root on most distros.
    """
    paths = [
        "/sys/bus/nvmem/devices/rockchip-otp0/nvmem",
        "/sys/bus/nvmem/devices/rockchip-otp1/nvmem",
        "/sys/bus/nvmem/devices/rockchip-otp2/nvmem",
        "/sys/bus/nvmem/devices/rockchip-otp/nvmem",
    ]
    for path in paths:
        data = _read_bytes(path)
        if data:
            # Filter out all-0x00 or all-0xFF (unprogrammed)
            if data and not (set(data) <= {0x00}) and not (set(data) <= {0xFF}):
                return data
    return None


def get_rk_otp_chip_id() -> str | None:
    """Return a stable hex string derived from RK OTP.

    On RK3588 the first 16-32 bytes of OTP usually contain
    the unique chip ID. We hash the entire readable OTP region to
    get a stable fingerprint regardless of which bytes the vendor
    actually programmed.
    """
    otp = get_rk_otp()
    if otp is None:
        return None
    return hashlib.sha256(otp).hexdigest()[:32]


def get_mac_address(iface: str = "eth0") -> str | None:
    """Fallback: use Ethernet MAC (burned into some boards)."""
    path = f"/sys/class/net/{iface}/address"
    data = _read_text(path)
    return data if data else None


def compute_device_fingerprint(
    sources: list[str] | None = None,
    prefer_combined: bool = True,
) -> dict:
    """Gather all available unique IDs and compute a composite fingerprint.

    Anti-clone strategy:
      - Single eMMC CID is NOT enough (attacker can transplant eMMC)
      - Single RK OTP is NOT enough (attacker can read OTP but not transplant)
      - We fuse BOTH eMMC CID + RK OTP into a single primary_id.
      - To clone, attacker must spoof BOTH identifiers simultaneously.
      - Falls back to whichever is available, but marks as "weak".

    Args:
        sources: Explicit list of sources to include. If None, use all.
        prefer_combined: If True, fuse eMMC CID + RK OTP when both available.

    Returns:
        dict with keys:
            - primary_id: fused eMMC+SoC ID (or best available fallback)
            - primary_source: which source was chosen
            - dt_serial: device-tree serial
            - cpu_serial: /proc/cpuinfo serial
            - emmc_cid: full eMMC CID
            - emmc_psn: eMMC product serial number
            - rk_otp_raw: RK OTP SHA256 (first 32 hex chars)
            - composite_hash: SHA256 of concatenated sources
            - platform: rk3588 / unknown
    """
    if sources is None:
        sources = ["dt_serial", "cpu_serial", "emmc_cid", "rk_otp"]

    dt_serial = get_dt_serial() if "dt_serial" in sources else None
    cpu_serial = get_cpu_serial() if "cpu_serial" in sources else None
    emmc_cid = get_emmc_cid() if "emmc_cid" in sources else None
    emmc_psn = parse_emmc_psn(emmc_cid) if emmc_cid else None
    rk_otp = get_rk_otp_chip_id() if "rk_otp" in sources else None

    # Determine platform
    platform = "unknown"
    compat = _read_text("/proc/device-tree/compatible") or ""
    if "rk3588" in compat.lower():
        platform = "rk3588"

    # Pick primary ID with anti-clone hierarchy
    primary_id: str | None = None
    primary_source: str | None = None

    # STRONG: fuse eMMC CID + RK OTP together
    if prefer_combined and emmc_cid and rk_otp:
        fused = hashlib.sha256(
            f"{emmc_cid}:{rk_otp}:exo_fuse_v1".encode()
        ).hexdigest()
        primary_id = fused
        primary_source = "emmc_otp_fused"
    # MEDIUM: eMMC CID alone (transplantable)
    elif emmc_cid:
        primary_id = emmc_cid
        primary_source = "emmc_cid"
    # MEDIUM: RK OTP alone (SoC-bound)
    elif rk_otp:
        primary_id = rk_otp
        primary_source = "rk_otp"
    # WEAK: device-tree serial (easily spoofed on clones)
    elif dt_serial and dt_serial.lower() not in ("unknown", "", "0123456789"):
        primary_id = dt_serial
        primary_source = "dt_serial"
    # WEAK: CPU serial (may not be exposed)
    elif cpu_serial:
        primary_id = cpu_serial
        primary_source = "cpu_serial"

    # Composite hash: even if one source is spoofed, an attacker must
    # spoof ALL of them to preserve the composite hash.
    hash_input = "|".join(
        str(v) for v in [platform, dt_serial, cpu_serial, emmc_cid, emmc_psn, rk_otp]
        if v
    ).encode("utf-8")
    composite_hash = hashlib.sha256(hash_input).hexdigest()

    return {
        "primary_id": primary_id or "unknown",
        "primary_source": primary_source or "none",
        "platform": platform,
        "dt_serial": dt_serial,
        "cpu_serial": cpu_serial,
        "emmc_cid": emmc_cid,
        "emmc_psn": emmc_psn,
        "rk_otp_raw": rk_otp,
        "composite_hash": composite_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RK3588 Device ID Utility")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--source", choices=["emmc_cid", "dt_serial", "cpu_serial", "rk_otp", "all"], default="all")
    parser.add_argument("--no-prefer-combined", action="store_true", help="Prefer single source over fused eMMC+OTP")
    args = parser.parse_args()

    sources = [args.source] if args.source != "all" else None
    info = compute_device_fingerprint(sources=sources, prefer_combined=not args.no_prefer_combined)

    if args.format == "json":
        print(json.dumps(info, indent=2))
    else:
        print("=" * 60)
        print("RK3588 Device ID Report")
        print("=" * 60)
        print(f"Platform         : {info['platform']}")
        print(f"Primary ID       : {info['primary_id']}")
        print(f"Primary Source   : {info['primary_source']}")
        print("-" * 60)
        print(f"Device-tree serial : {info['dt_serial'] or 'N/A'}")
        print(f"CPU serial         : {info['cpu_serial'] or 'N/A'}")
        print(f"eMMC CID           : {info['emmc_cid'] or 'N/A'}")
        print(f"eMMC PSN           : {info['emmc_psn'] or 'N/A'}")
        print(f"RK OTP hash        : {info['rk_otp_raw'] or 'N/A'}")
        print("-" * 60)
        print(f"Composite Hash   : {info['composite_hash']}")
        print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
