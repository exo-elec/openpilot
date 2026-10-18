"""The message registry: every PLP message is a (service, message) pair, listed once in registry.json (the Dart side generates its constants from the same file)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

KIND_NOTIFY, KIND_REQUEST, KIND_RESPONSE, KIND_ERROR = 0, 1, 2, 3
KINDS = {'notify': KIND_NOTIFY, 'request': KIND_REQUEST, 'response': KIND_RESPONSE, 'error': KIND_ERROR}


@dataclass(frozen=True)
class MessageDef:
  service: int
  message: int
  service_name: str
  name: str
  direction: str          # 'p2d' phone to device, 'd2p' device to phone, 'both'
  status: str             # 'active', 'retired' (v4 compatibility only) or 'planned' (reserved, not yet sent)
  note: str
  v4: tuple[tuple[int, int], ...]   # (v4 type number, kind)

  @property
  def key(self) -> tuple[int, int]:
    return (self.service, self.message)

  @property
  def full_name(self) -> str:
    return f'{self.service_name}.{self.name}'


def _load() -> dict:
  with open(Path(__file__).with_name('registry.json')) as f:
    return json.load(f)


_RAW = _load()
SERVICES: dict[str, int] = dict(_RAW['services'])
V4_SPECIAL: dict[str, int] = dict(_RAW['v4_special'])
MESSAGES: dict[tuple[int, int], MessageDef] = {}
BY_NAME: dict[str, MessageDef] = {}
_V4: dict[int, tuple[MessageDef, int]] = {}
for _m in _RAW['messages']:
  _d = MessageDef(SERVICES[_m['service']], _m['id'], _m['service'], _m['name'], _m['dir'], _m['status'], _m['note'],
                  tuple((c['code'], KINDS[c['kind']]) for c in _m['v4']))
  MESSAGES[_d.key] = _d
  BY_NAME[_d.full_name] = _d
  for _code, _kind in _d.v4:
    _V4[_code] = (_d, _kind)


def v4_to_v5(v4_type: int) -> tuple[MessageDef, int] | None:
  """The PLP message and kind an NCP v4 type number stands for, or None (ack, error and batch have no message of their own; see V4_SPECIAL)."""
  return _V4.get(v4_type)


def v5_to_v4(service: int, message: int, kind: int) -> int | None:
  """The NCP v4 type number to use for this message and kind, or None when v4 has none (a planned message)."""
  d = MESSAGES.get((service, message))
  if d is None:
    return None
  for code, k in d.v4:
    if k == kind:
      return code
  return d.v4[0][0] if d.v4 else None
