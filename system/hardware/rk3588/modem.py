"""EOP10 cellular state adapter for RK3588 platforms.

Low-level EC25 control lives in `exopilot/hal/hal/drivers/cellular`. This module
reads application params, calls the HAL, and converts HAL dataclasses into
cereal messages.
"""

from __future__ import annotations

from cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

# Low-level EC25 driver is board-support code in ExoPilot HAL.
try:
    from hal.drivers.cellular import (
        EC25Modem,
        SIMInfo,
        NetworkInfo,
        ModemTemperatures,
        NetworkType as _HalNetworkType,
        NetworkStrength as _HalNetworkStrength,
        find_modem_id as _hal_find_modem_id,
        lookup_apn as _hal_lookup_apn,
        get_modem_data_usage as _hal_get_modem_data_usage,
        get_modem_version as _hal_get_modem_version,
        get_imei as _hal_get_imei,
        get_sim_info as _hal_get_sim_info,
        get_network_type as _hal_get_network_type,
        get_network_strength as _hal_get_network_strength,
        get_network_info as _hal_get_network_info,
        get_modem_temperatures as _hal_get_modem_temperatures,
        get_network_metered as _hal_get_network_metered,
    )
except Exception:
    # Dev-PC fallback: none of the cellular queries will work, but the module
    # remains importable so tests and managers don't crash.
    cloudlog.exception("modem: failed to import hal.drivers.cellular")
    EC25Modem = None  # type: ignore[misc,assignment]
    _HalNetworkType = None  # type: ignore[misc,assignment]
    _HalNetworkStrength = None  # type: ignore[misc,assignment]
    _hal_find_modem_id = None  # type: ignore[misc,assignment]
    _hal_lookup_apn = None  # type: ignore[misc,assignment]
    _hal_get_modem_data_usage = None  # type: ignore[misc,assignment]
    _hal_get_modem_version = None  # type: ignore[misc,assignment]
    _hal_get_imei = None  # type: ignore[misc,assignment]
    _hal_get_sim_info = None  # type: ignore[misc,assignment]
    _hal_get_network_type = None  # type: ignore[misc,assignment]
    _hal_get_network_strength = None  # type: ignore[misc,assignment]
    _hal_get_network_info = None  # type: ignore[misc,assignment]
    _hal_get_modem_temperatures = None  # type: ignore[misc,assignment]
    _hal_get_network_metered = None  # type: ignore[misc,assignment]


NetworkType = log.DeviceState.NetworkType
NetworkStrength = log.DeviceState.NetworkStrength


def _map_network_type(hal_type: _HalNetworkType | None) -> int:
    if hal_type is None:
        return int(NetworkType.none)
    mapping = {
        _HalNetworkType.none: NetworkType.none,
        _HalNetworkType.wifi: NetworkType.wifi,
        _HalNetworkType.cell2G: NetworkType.cell2G,
        _HalNetworkType.cell3G: NetworkType.cell3G,
        _HalNetworkType.cell4G: NetworkType.cell4G,
        _HalNetworkType.cell5G: NetworkType.cell5G,
        _HalNetworkType.ethernet: NetworkType.ethernet,
    }
    return int(mapping.get(hal_type, NetworkType.none))


def _map_network_strength(hal_strength: _HalNetworkStrength | None) -> int:
    if hal_strength is None:
        return int(NetworkStrength.unknown)
    mapping = {
        _HalNetworkStrength.unknown: NetworkStrength.unknown,
        _HalNetworkStrength.poor: NetworkStrength.poor,
        _HalNetworkStrength.moderate: NetworkStrength.moderate,
        _HalNetworkStrength.good: NetworkStrength.good,
        _HalNetworkStrength.great: NetworkStrength.great,
    }
    return int(mapping.get(hal_strength, NetworkStrength.unknown))


def _hal_available() -> bool:
    return EC25Modem is not None


def configure_modem() -> bool:
    """Bring up EC25 data session using APN from params or auto-lookup."""
    if not _hal_available():
        cloudlog.warning("modem: HAL not available, cannot configure modem")
        return False

    mid = _hal_find_modem_id()
    if not mid:
        return False

    params = Params()
    apn_raw = params.get("GsmApn")
    apn = apn_raw.decode() if isinstance(apn_raw, bytes) else (apn_raw or "")
    if not apn:
        sim_info = _hal_get_sim_info()
        mcc_mnc = str(sim_info.mcc_mnc or "").strip()
        apn = _hal_lookup_apn(mcc_mnc)

    if not apn:
        cloudlog.warning("modem: no APN found")
        return False

    return EC25Modem(mid=mid).configure(apn)


def get_modem_data_usage() -> tuple[int, int]:
    if not _hal_available():
        return -1, -1
    return _hal_get_modem_data_usage()


def get_modem_version() -> str | None:
    if not _hal_available():
        return None
    return _hal_get_modem_version()


def get_imei() -> str:
    if not _hal_available():
        return ""
    return _hal_get_imei()


def get_sim_info() -> dict:
    if not _hal_available():
        return {
            "sim_id": "",
            "mcc_mnc": None,
            "network_type": ["Unknown"],
            "sim_state": ["ABSENT"],
            "data_connected": False,
        }
    info: SIMInfo = _hal_get_sim_info()
    return {
        "sim_id": info.sim_id,
        "mcc_mnc": info.mcc_mnc,
        "network_type": info.network_type,
        "sim_state": info.sim_state,
        "data_connected": info.data_connected,
    }


def get_network_type() -> int:
    if not _hal_available():
        return int(NetworkType.none)
    return _map_network_type(_hal_get_network_type())


def get_network_strength(network_type: int) -> int:
    if not _hal_available():
        return int(NetworkStrength.unknown)
    # Convert cereal NetworkType to HAL enum for the query.
    try:
        hal_type = _HalNetworkType(network_type)  # type: ignore[index]
    except ValueError:
        return int(NetworkStrength.unknown)
    return _map_network_strength(_hal_get_network_strength(hal_type))


def get_network_info() -> dict | None:
    if not _hal_available():
        return None
    info: NetworkInfo | None = _hal_get_network_info()
    if info is None:
        return None
    return {
        "technology": info.technology,
        "operator": info.operator,
        "band": info.band,
        "channel": info.channel,
        "extra": info.extra,
        "state": info.state,
    }


def get_modem_temperatures() -> list[int]:
    if not _hal_available():
        return []
    temps: ModemTemperatures = _hal_get_modem_temperatures()
    return temps.values


def get_network_metered(network_type: int) -> bool:
    if not _hal_available():
        return network_type in (
            NetworkType.cell2G,
            NetworkType.cell3G,
            NetworkType.cell4G,
            NetworkType.cell5G,
        )
    try:
        hal_type = _HalNetworkType(network_type)  # type: ignore[index]
    except ValueError:
        return False
    return _hal_get_network_metered(hal_type)
