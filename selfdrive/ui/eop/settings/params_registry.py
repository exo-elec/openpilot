"""Read the authoritative param list out of common/params_keys.h.

The settings UI must not keep its own copy of which params exist -- that is
how 263 hand-transcribed entries quietly drift from the C++ header. This
parses the header instead, so the descriptor (descriptor.py) can be checked
against reality by a test rather than by review.

Deliberately a regex over the header rather than a C++ parse: the entries are
a uniform `{"Key", {FLAGS, TYPE, "default"}},` table, and a parser that
understands more of C++ than that would be more code and no more correct.
The test catches it if the format ever moves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# openpilot root, from selfdrive/ui/eop/settings/params_registry.py
_HEADER = Path(__file__).resolve().parents[4] / "common" / "params_keys.h"

_ENTRY = re.compile(
  r'\{\s*"(?P<key>[A-Za-z0-9_]+)"\s*,\s*\{(?P<flags>[^,}]+),\s*(?P<type>[A-Z]+)\s*'
  + r'(?:,\s*"(?P<default>[^"]*)")?\s*\}\s*\}'
)


@dataclass(frozen=True)
class ParamKey:
  name: str
  type: str
  default: str | None
  flags: str

  @property
  def is_eop(self) -> bool:
    return self.name.startswith("EOP")


@lru_cache(maxsize=1)
def all_params() -> tuple[ParamKey, ...]:
  if not _HEADER.exists():
    raise FileNotFoundError(f"params_keys.h not found at {_HEADER}")
  text = _HEADER.read_text(encoding="utf-8", errors="replace")
  out = []
  for m in _ENTRY.finditer(text):
    out.append(ParamKey(
      name=m.group("key"),
      type=m.group("type"),
      default=m.group("default"),
      flags=m.group("flags").strip(),
    ))
  return tuple(out)


def eop_params() -> tuple[ParamKey, ...]:
  return tuple(p for p in all_params() if p.is_eop)


def eop_keys() -> frozenset[str]:
  return frozenset(p.name for p in eop_params())
