#!/usr/bin/env python3
"""Bluetooth device classification.

Identifies mobile devices for SPP (companion app) connectivity.
"""
from __future__ import annotations

from enum import IntEnum
from dataclasses import dataclass


class Type(IntEnum):
    """Device type."""
    UNKNOWN = 0
    MOBILE = 1


# UUIDs
SPP = "00001101-0000-1000-8000-00805F9B34FB"


@dataclass(frozen=True)
class Info:
    """Device information."""
    address: str
    name: str
    device_type: Type
    uuids: tuple[str, ...]
    
    def is_mobile(self) -> bool:
        return self.device_type == Type.MOBILE


def classify(address: str, name: str, uuids: list[str]) -> Info:
    """Classify device based on UUIDs and name."""
    uuid_set = set(uuids)
    
    # Check for SPP (Serial Port Profile) - used by companion apps
    if SPP in uuid_set:
        return Info(address, name, Type.MOBILE, tuple(uuids))
    
    # Check name patterns for mobile devices
    name_lower = name.lower()
    phone_keywords = ('iphone', 'samsung', 'pixel', 'phone', 'mobile', 'navpilot')
    
    if any(k in name_lower for k in phone_keywords):
        return Info(address, name, Type.MOBILE, tuple(uuids))
    
    return Info(address, name, Type.UNKNOWN, tuple(uuids))
