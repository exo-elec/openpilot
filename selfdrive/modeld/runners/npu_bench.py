#!/usr/bin/env python3
"""Measure each model's NPU time on its allocated core and the per-core load.

Run on the board (read-only; loads models with RKNNLite, feeds zeros):
  python3 -m openpilot.selfdrive.modeld.runners.npu_bench \\
      --model modeld=/data/models/driving_vision.rknn@20:1x12x128x256:uint8 \\
      --model monod=/data/models/mono.rknn@10:1x480x640x3:uint8

Each --model is TASK=PATH@HZ:SHAPE[:DTYPE]. TASK picks the core from
rknn_platform.NPU_ALLOCATION_MAP; HZ is how often the daemon runs it. Load =
median latency x HZ, summed per core, and a core above the 85% line fails
(exit 1). This replaces the unmeasured TOPS split in rknn_platform.py with
numbers from the real models on the real SoC.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass

import numpy as np

from openpilot.selfdrive.modeld.runners.rknn_platform import (
  NPU_ALLOCATION_MAP, detect_platform, get_core_mask,
)

SAFETY_LIMIT = 0.85


@dataclass
class ModelSpec:
  task: str
  path: str
  hz: float
  shape: tuple[int, ...]
  dtype: str = "uint8"


def parse_spec(text: str) -> ModelSpec:
  """'modeld=/m.rknn@20:1x3x64x64[:float16]' -> ModelSpec."""
  try:
    task, rest = text.split("=", 1)
    path, rest = rest.rsplit("@", 1)
    hz, shape, *dtype = rest.split(":")
    return ModelSpec(task, path, float(hz), tuple(int(d) for d in shape.lower().split("x")),
                     dtype[0] if dtype else "uint8")
  except ValueError as e:
    raise argparse.ArgumentTypeError(f"bad --model '{text}': want TASK=PATH@HZ:SHAPE[:DTYPE]") from e


def measure(spec: ModelSpec, core_mask: int, runs: int, warmup: int, rknn_factory,
            clock=time.perf_counter) -> float:
  """Median latency in seconds of one inference on `core_mask`."""
  rknn = rknn_factory()
  if rknn.load_rknn(spec.path) != 0:
    raise RuntimeError(f"load_rknn failed: {spec.path}")
  if rknn.init_runtime(core_mask=core_mask) != 0:
    raise RuntimeError(f"init_runtime failed: {spec.path} mask {core_mask:#x}")
  x = np.zeros(spec.shape, dtype=spec.dtype)
  try:
    for _ in range(warmup):
      rknn.inference(inputs=[x])
    times = []
    for _ in range(runs):
      t0 = clock()
      rknn.inference(inputs=[x])
      times.append(clock() - t0)
  finally:
    rknn.release()
  return statistics.median(times)


def core_loads(results: list[tuple[ModelSpec, int, float]]) -> dict[int, float]:
  """{core mask: sum of latency x hz} for (spec, mask, latency) results."""
  loads: dict[int, float] = {}
  for spec, mask, latency in results:
    loads[mask] = loads.get(mask, 0.0) + latency * spec.hz
  return loads


def report(results, loads, out=None) -> bool:
  out = out or sys.stdout
  print(f"{'task':14s} {'core mask':>9s} {'median ms':>10s} {'Hz':>6s} {'load':>7s}", file=out)
  for spec, mask, latency in results:
    print(f"{spec.task:14s} {mask:>#9x} {latency * 1000:>10.2f} {spec.hz:>6.1f} {latency * spec.hz:>7.1%}", file=out)
  ok = True
  for mask, load in sorted(loads.items()):
    flag = "OK" if load <= SAFETY_LIMIT else "OVER"
    ok &= load <= SAFETY_LIMIT
    print(f"core mask {mask:#x}: {load:.1%} of the core ({flag}, limit {SAFETY_LIMIT:.0%})", file=out)
  return ok


def main(argv=None, rknn_factory=None, clock=time.perf_counter) -> int:
  ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  ap.add_argument("--model", type=parse_spec, action="append", required=True)
  ap.add_argument("--runs", type=int, default=50)
  ap.add_argument("--warmup", type=int, default=5)
  args = ap.parse_args(argv)

  platform = detect_platform()
  if platform not in NPU_ALLOCATION_MAP:
    print(f"platform {platform.value}: no NPU allocation map; run on the board", file=sys.stderr)
    return 2
  if rknn_factory is None:
    from rknnlite.api import RKNNLite
    rknn_factory = lambda: RKNNLite(verbose=False)  # noqa: E731

  results = []
  for spec in args.model:
    mask = get_core_mask(platform, spec.task)
    results.append((spec, mask, measure(spec, mask, args.runs, args.warmup, rknn_factory, clock)))
  return 0 if report(results, core_loads(results)) else 1


if __name__ == "__main__":
  raise SystemExit(main())
