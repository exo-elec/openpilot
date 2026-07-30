#!/usr/bin/env python3
"""
Auto-detect USB camera topology and generate udev rules.

Run this on the target RK3588 device after connecting the
RTS5411S hub and UVC cameras to their designated ports.

It will:
1. Show the USB bus topology (hub ports -> devices)
2. Identify which /dev/videoN belongs to which camera
3. Generate the correct udev ENV{ID_PATH} rules
4. Optionally write them to /etc/udev/rules.d/

Usage:
    sudo python3 tune_udev_usb_cameras.py [--write] [--dry-run]

The generated rules replace the placeholder comments in:
    system/hardware/rk3588/config/88-rockchip-camera.rules
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class UvcDevice:
    name: str
    video_nodes: List[str]
    id_path: Optional[str] = None
    usb_port_path: Optional[str] = None
    vendor_id: Optional[str] = None
    product_id: Optional[str] = None


def run(cmd: List[str]) -> str:
    """Run a command and return stdout."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Warning: {' '.join(cmd)} failed: {e.stderr.strip()}", file=sys.stderr)
        return ""


def get_usb_topology() -> str:
    """Get USB tree showing hub and device connections."""
    return run(["lsusb", "-t"])


def get_v4l2_devices() -> List[UvcDevice]:
    """Parse v4l2-ctl --list-devices output to find UVC cameras."""
    output = run(["v4l2-ctl", "--list-devices"])
    devices: List[UvcDevice] = []
    current_name = None
    current_nodes: List[str] = []

    for line in output.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("\t") or line.startswith(" "):
            # This is a video node line
            node_match = re.search(r'(/dev/video\d+)', line)
            if node_match:
                current_nodes.append(node_match.group(1))
        else:
            # New device block
            if current_name and current_nodes:
                devices.append(UvcDevice(name=current_name.strip(), video_nodes=current_nodes))
            current_name = line.strip(":")
            current_nodes = []

    # Don't forget the last one
    if current_name and current_nodes:
        devices.append(UvcDevice(name=current_name.strip(), video_nodes=current_nodes))

    return devices


def get_udev_id_path(video_node: str) -> Optional[str]:
    """Get ID_PATH for a video device from udevadm."""
    output = run(["udevadm", "info", "--query=all", f"--name={video_node}"])
    for line in output.splitlines():
        if line.startswith("E: ID_PATH="):
            return line.split("=", 1)[1].strip()
    return None


def get_usb_ids(video_node: str) -> tuple:
    """Get vendor/product IDs from udevadm."""
    output = run(["udevadm", "info", "--query=all", f"--name={video_node}"])
    vid = None
    pid = None
    for line in output.splitlines():
        if line.startswith("E: ID_VENDOR_ID="):
            vid = line.split("=", 1)[1].strip()
        elif line.startswith("E: ID_MODEL_ID="):
            pid = line.split("=", 1)[1].strip()
    return vid, pid


def generate_udev_rule(device: UvcDevice, symlink_name: str) -> Optional[str]:
    """Generate a single udev rule line for a UVC camera."""
    if not device.id_path:
        return None

    # Build a glob pattern from ID_PATH that matches the hub port
    # Example ID_PATH: platform-xhci-hcd.0.auto-usb-0:1.1:1.0
    # We want to match the port path after the host controller
    id_path_pattern = device.id_path

    # If the ID_PATH contains a specific configuration/interface suffix like :1.0,
    # we can wildcard it to be more robust
    if ":" in id_path_pattern:
        base = id_path_pattern.rsplit(":", 1)[0]
        id_path_pattern = base + "*"

    return (
        f'# {device.name}\n'
        f'ACTION=="add", SUBSYSTEM=="video4linux", KERNEL=="video*", '
        f'ATTR{{name}}=="*UVC*", ENV{{ID_PATH}}=="*{id_path_pattern}*", '
        f'SYMLINK+="{symlink_name}", TAG+="systemd"'
    )


def identify_camera(device: UvcDevice) -> Optional[str]:
    """Try to identify which logical camera this is based on heuristics."""
    name_lower = device.name.lower()
    nodes_str = " ".join(device.video_nodes).lower()

    # Check for known camera names
    if "side" in name_lower and "left" in name_lower:
        return "video-side-left"
    if "side" in name_lower and "right" in name_lower:
        return "video-side-right"
    if "rear" in name_lower:
        return "video-rear"
    if "face" in name_lower or "driver" in name_lower:
        return "video-driver"

    # Heuristic: if we have multiple UVC devices, guess by order
    # This is weak — user should verify
    return None


def generate_rules(devices: List[UvcDevice]) -> List[str]:
    """Generate udev rules for all detected UVC cameras."""
    rules = []
    rules.append("# Auto-generated USB UVC camera rules")
    rules.append("# Run 'tune_udev_usb_cameras.py' on target device to regenerate")
    rules.append("")

    # Identify and generate rules
    assigned_names = set()
    unassigned: List[UvcDevice] = []

    for dev in devices:
        name = identify_camera(dev)
        if name and name not in assigned_names:
            dev.identified_name = name
            assigned_names.add(name)
        else:
            unassigned.append(dev)

    # Generate rules for assigned cameras
    for dev in devices:
        if hasattr(dev, 'identified_name'):
            rule = generate_udev_rule(dev, dev.identified_name)
            if rule:
                rules.append(rule)
                rules.append("")

    # Report unassigned cameras
    if unassigned:
        rules.append("# --- Unassigned cameras (manual identification needed) ---")
        for dev in unassigned:
            rules.append(f"# Device: {dev.name}")
            rules.append(f"# Nodes:  {', '.join(dev.video_nodes)}")
            rules.append(f"# ID_PATH: {dev.id_path or 'unknown'}")
            rules.append("")

    return rules


def main():
    parser = argparse.ArgumentParser(description="Auto-detect USB camera topology and generate udev rules")
    parser.add_argument("--write", action="store_true", help="Write rules to /etc/udev/rules.d/88-rockchip-camera.rules")
    parser.add_argument("--output", "-o", type=str, help="Output file path (default: stdout)")
    parser.add_argument("--platform", choices=["rk3588"], help="Platform name for rule comments")
    args = parser.parse_args()

    print("=" * 60)
    print("USB Camera Topology Detection")
    print("=" * 60)

    # 1. USB topology
    print("\n[1] USB Bus Topology:")
    print("-" * 40)
    usb_tree = get_usb_topology()
    print(usb_tree if usb_tree else "(lsusb -t failed — are you root?)")

    # 2. V4L2 devices
    print("\n[2] V4L2 Video Devices:")
    print("-" * 40)
    devices = get_v4l2_devices()
    if not devices:
        print("No V4L2 devices found.")
        return

    for dev in devices:
        print(f"\n  Device: {dev.name}")
        print(f"  Nodes:  {', '.join(dev.video_nodes)}")
        for node in dev.video_nodes:
            dev.id_path = get_udev_id_path(node)
            dev.vendor_id, dev.product_id = get_usb_ids(node)
            if dev.id_path:
                print(f"  ID_PATH ({node}): {dev.id_path}")
            if dev.vendor_id and dev.product_id:
                print(f"  USB IDs ({node}): {dev.vendor_id}:{dev.product_id}")

    # 3. Generate rules
    print("\n[3] Generated udev Rules:")
    print("-" * 40)
    rules = generate_rules(devices)
    rules_text = "\n".join(rules)
    print(rules_text)

    # 4. Output / write
    if args.output:
        with open(args.output, "w") as f:
            f.write(rules_text + "\n")
        print(f"\nRules written to: {args.output}")

    if args.write:
        output_path = "/etc/udev/rules.d/88-rockchip-camera.rules"
        print(f"\nWriting to {output_path} ...")
        # Read existing MIPI rules and append USB rules
        existing = ""
        try:
            with open(output_path) as f:
                existing = f.read()
        except FileNotFoundError:
            pass

        # Keep only the MIPI (rkisp) rules from existing file
        mipi_lines = []
        for line in existing.splitlines():
            if "rkisp" in line or line.startswith("#"):
                mipi_lines.append(line)

        new_content = "\n".join(mipi_lines) + "\n\n" + rules_text + "\n"
        with open(output_path, "w") as f:
            f.write(new_content)
        print("Done. Run: udevadm control --reload-rules && udevadm trigger")

    print("\n" + "=" * 60)
    print("Detection complete.")
    print("=" * 60)
    if not args.write and not args.output:
        print("\nTip: Run with --write to install rules, or --output FILE to save.")


if __name__ == "__main__":
    main()
