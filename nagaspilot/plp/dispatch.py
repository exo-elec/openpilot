"""What to do with a message nobody here handles: a critical one gets an error back, an elective one is ignored."""
from __future__ import annotations

import json

from nagaspilot.plp import registry
from nagaspilot.plp.channel import Message
from nagaspilot.plp.frame import ENC_JSON

ERR_UNSUPPORTED = 'unsupported'
ERR_BAD_PAYLOAD = 'bad_payload'


def error_reply(request: Message, code: str, detail: str = '') -> Message:
  return Message(registry.KIND_ERROR, request.service, request.message, json.dumps({'code': code, 'detail': detail}, separators=(',', ':')).encode(), request.channel, ENC_JSON,
                 request.request_id)


def unknown_reply(m: Message) -> Message | None:
  """None for an elective notify or elective anything (silently dropped); an error for a critical message or any request, which is waiting for an answer."""
  if m.critical or m.kind == registry.KIND_REQUEST:
    return error_reply(m, ERR_UNSUPPORTED, f'{m.service}.{m.message}')
  return None
