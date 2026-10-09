"""Board-agnostic NPU task-to-core packing.

Bin-packs NPU workloads onto cores by estimated TOPS, using longest-
processing-time-first (LPT) greedy load balancing: process groups
heaviest-first, always placing the next one on whichever core currently
carries the least estimated load. This needs only the board's core count --
not a hand-authored per-platform table -- so RK3588 and RK3576 (and any
future board) call the exact same function; only their core count and
per-core TOPS differ.

TOPS numbers are hal.tuning.npu's estimates, not hardware measurements.
selfdrive/modeld/runners/npu_bench.py replaces them with real per-model
latency x Hz measured on real hardware -- when hal provides a non-empty,
already-measured CORE_ALLOCATION for a platform, that takes priority over
this module's computed guess (see rknn_platform.py).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NPUTaskGroup:
    """One physical NPU workload.

    `names` covers every alias a caller uses to look up this same physical
    model (e.g. a split driving runner's vision half is looked up as both
    "modeld" and "driving_vision"), so pack_tasks_to_cores counts its TOPS
    once no matter which name is queried, and every alias always shares one
    core mask.
    """
    names: tuple[str, ...]
    tops: float


def pack_tasks_to_cores(groups: tuple[NPUTaskGroup, ...], core_count: int) -> dict[str, int]:
    """{task name: RKNN core mask} for every alias in every group.

    LPT: heaviest group first, each placed on the currently least-loaded
    core (ties broken by lowest core index). Deterministic for a fixed
    `groups` order and stable sort.
    """
    if core_count < 1:
        raise ValueError(f"core_count must be >= 1, got {core_count}")

    loads = [0.0] * core_count
    allocation: dict[str, int] = {}
    for group in sorted(groups, key=lambda g: g.tops, reverse=True):
        core_idx = min(range(core_count), key=lambda i: loads[i])
        loads[core_idx] += group.tops
        mask = 1 << core_idx
        for name in group.names:
            allocation[name] = mask
    return allocation


def core_loads(groups: tuple[NPUTaskGroup, ...], allocation: dict[str, int], core_count: int) -> list[float]:
    """Total estimated TOPS assigned to each core, for a feasibility check.

    Reads placement back off `allocation` rather than re-running the
    packer, so it reports the load of whatever allocation is actually in
    use (computed or a real measured override).
    """
    loads = [0.0] * core_count
    for group in groups:
        mask = allocation.get(group.names[0])
        if mask is None:
            continue
        core_idx = mask.bit_length() - 1
        if 0 <= core_idx < core_count:
            loads[core_idx] += group.tops
    return loads
