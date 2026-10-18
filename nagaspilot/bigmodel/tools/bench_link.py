"""Measure the link's own cost per frame against a phone server: the round trip of a real-sized request, with no model in the way.

    python3 -m nagaspilot.bigmodel.tools.bench_link HOST PORT [--frames 400] [--hz 20]

Sends zero-filled frames at the driving rate and reports the round trip (send to reply) in milliseconds. With the stub backend (no inference) this
is the protocol, framing and network cost; with a real model it is the whole phone-side time, to be held against the 50 ms frame budget
(FRAME_BUDGET_MS). Exit status is 1 when the p99 is over the budget.
"""
from __future__ import annotations

import argparse
import sys
import time

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel.client import Client

FRAME_BUDGET_MS = 50.0


def percentile(sorted_ms: list[float], q: float) -> float:
  return sorted_ms[min(len(sorted_ms) - 1, int(q * len(sorted_ms)))]


def run(client: Client, frames: int, hz: float) -> list[float]:
  info = client.connect()
  packed = [0.0] * (P.DESIRE_LEN + P.TRAFFIC_LEN)
  if info.contract != P.CONTRACT_SPLIT:
    raise SystemExit(f'bench_link drives the split contract; the phone chose {info.contract}')
  frame = bytes(P.FRAME_BYTES)
  out: list[float] = []
  period = 1.0 / hz
  nxt = time.monotonic()
  for i in range(frames):
    nxt += period
    t0 = time.monotonic()
    seq = client.send_frame_packed(i, 0, packed, frame)
    resp = None
    while resp is None:
      resp = client.recv_response(seq, 1.0)
    out.append((time.monotonic() - t0) * 1000)
    time.sleep(max(0.0, nxt - time.monotonic()))
  client.leave('bench done')
  client.close()
  return sorted(out)


def main(argv=None) -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument('host')
  ap.add_argument('port', type=int)
  ap.add_argument('--frames', type=int, default=400)
  ap.add_argument('--hz', type=float, default=20.0)
  args = ap.parse_args(argv)
  ms = run(Client(args.host, args.port), args.frames, args.hz)
  p50, p99, worst = percentile(ms, .5), percentile(ms, .99), ms[-1]
  print(f'{len(ms)} frames at {args.hz:g} Hz: p50 {p50:.2f} ms  p99 {p99:.2f} ms  max {worst:.2f} ms  (budget {FRAME_BUDGET_MS:g} ms)')
  return 0 if p99 < FRAME_BUDGET_MS else 1


if __name__ == '__main__':
  sys.exit(main())
