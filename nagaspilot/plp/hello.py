"""The PLP hello: each side says what it speaks, and both settle on the intersection.

The hello is the first message on a link (service core, message hello, request then response). Before it, and on links whose peer does not answer it, the link is NCP v4.1.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from nagaspilot.plp import registry
from nagaspilot.plp.frame import ENC_JSON, VERSION

LINKS = ('usb', 'ble', 'spp')


@dataclass(frozen=True)
class Hello:
  min_version: int = VERSION
  max_version: int = VERSION
  encodings: tuple[int, ...] = (ENC_JSON,)
  max_payload: int = 4096                     # largest frame payload this side accepts
  max_message: int = 1 << 20                  # largest reassembled message
  services: tuple[tuple[str, int, int], ...] = ()   # (service name, lowest, highest message set version)
  link: str = 'ble'

  def to_json(self) -> bytes:
    return json.dumps({'version': [self.min_version, self.max_version], 'encodings': list(self.encodings), 'max_payload': self.max_payload,
                       'max_message': self.max_message, 'services': {n: [lo, hi] for n, lo, hi in self.services}, 'link': self.link},
                      separators=(',', ':'), sort_keys=True).encode()

  @classmethod
  def parse(cls, raw: bytes) -> Hello:
    try:
      d = json.loads(raw)
      lo, hi = (int(x) for x in d['version'])
      svc = tuple((str(n), int(v[0]), int(v[1])) for n, v in sorted(d.get('services', {}).items()))
      return cls(lo, hi, tuple(int(e) for e in d.get('encodings', [ENC_JSON])), int(d['max_payload']), int(d['max_message']), svc, str(d.get('link', 'ble')))
    except (ValueError, KeyError, TypeError, IndexError) as e:
      raise ValueError(f'unreadable hello: {e}') from e


@dataclass(frozen=True)
class Agreed:
  version: int
  encodings: tuple[int, ...]
  max_payload: int                       # frames sent to the peer stay within the peer's limit
  max_message: int
  services: dict[str, int]               # service name -> message set version both speak
  link: str


def negotiate(local: Hello, remote: Hello) -> Agreed | None:
  """None when there is no common version: the link stays NCP v4.1."""
  version = min(local.max_version, remote.max_version)
  if version < max(local.min_version, remote.min_version):
    return None
  encodings = tuple(e for e in local.encodings if e in remote.encodings) or (ENC_JSON,)
  mine = {n: (lo, hi) for n, lo, hi in local.services}
  common: dict[str, int] = {}
  for n, lo, hi in remote.services:
    if n in mine and n in registry.SERVICES:
      top = min(mine[n][1], hi)
      if top >= max(mine[n][0], lo):
        common[n] = top
  return Agreed(version, encodings, min(local.max_payload, remote.max_payload), min(local.max_message, remote.max_message), common, remote.link)
