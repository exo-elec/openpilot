"""Write the NPBM conformance vectors the phone-side servers (Swift, Kotlin) test against.

    python3 -m nagaspilot.bigmodel.tools.make_golden > npbm_golden.json

Everything is derived from protocol.py and server_ref.py, so the file cannot drift from them; nagaspilot/tests/test_bigmodel_golden.py
fails when the committed copy (docs/npbm_golden.json) is not what this prints. The frame is 393,216 bytes, so vectors give a
pattern and the sha256 of the bytes instead of the bytes.
"""
import hashlib
import json

import numpy as np

from nagaspilot.bigmodel import framing as F
from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel import server_ref as S


def frame(value: int) -> bytes:
  return bytes([value]) * P.FRAME_BYTES


def build() -> dict:
  req = P.pack_infer_req(7, 42, P.RESET, [0, 1, 0, 0, 0, 0, 0, 0], [1, 0], frame(5))
  resp_out = np.array([1.0, -2.5, 0.0, 3.25], np.float32).tobytes()
  backend, state = S.StubBackend(), S.State()
  steps = []
  for i, value in enumerate((3, 9, 27)):
    desire = [0, 1.0 if i == 1 else 0.0, 0, 0, 0, 0, 0, 0]
    out = S.step(state, backend, desire, [1, 0], np.full(P.FRAME_SHAPE, value, np.uint8))
    steps.append({'frame_fill': value, 'desire': desire, 'traffic': [1, 0], 'vision_first4': out[:4].tolist(),
                  'vision_511': float(out[511]), 'policy_first': float(out[backend.vision_floats]),
                  'out_sha256': hashlib.sha256(out.tobytes()).hexdigest()})
  return {
    'version': P.VERSION, 'magic': P.MAGIC.decode(), 'header_size': P.HEADER_SIZE, 'contract': P.CONTRACT,
    'frame_shape': list(P.FRAME_SHAPE), 'frame_bytes': P.FRAME_BYTES,
    'ping_seq7': P.pack(P.PING, 7).hex(),
    'hello_payload': P.hello_payload().decode(),
    'hello_message_seq1': P.pack(P.HELLO, 1, P.hello_payload()).hex(),
    'infer_req_seq7': {'frame_id': 42, 'flags': P.RESET, 'desire': [0, 1, 0, 0, 0, 0, 0, 0], 'traffic': [1, 0],
                       'frame_fill': 5, 'header_hex': req[:P.HEADER_SIZE].hex(),
                       'fixed_hex': req[P.HEADER_SIZE:P.HEADER_SIZE + P.REQ_FIXED.size].hex(),
                       'total_length': len(req), 'sha256': hashlib.sha256(req).hexdigest()},
    'infer_resp_seq7': P.pack_infer_resp(7, 42, P.OK, 1234, resp_out).hex(),
    'bad_headers': {'bad_magic': (b'XXXX' + P.pack(P.PING, 1)[4:]).hex(),
                    'bad_version': (P.pack(P.PING, 1)[:4] + bytes([2]) + P.pack(P.PING, 1)[5:]).hex(),
                    'bad_crc': (P.pack(P.PING, 1)[:-1] + bytes([P.pack(P.PING, 1)[-1] ^ 1])).hex()},
    'bulk_framing': {
      'note': 'Android only. comma->phone: zero-pad every message to a multiple of burst. phone->comma: one zero byte when the length is an exact multiple of packet.',
      'burst': F.BURST, 'packet': F.PACKET,
      'ping_wire_length': len(F.pad_to_burst(P.pack(P.PING, 7))),
      'infer_req_wire_length': len(F.pad_to_burst(req)),
      'resp_exactly_512': {'wire_length_before_pad': 512, 'wire_length_sent': len(F.pad_for_packet(P.pack_infer_resp(3, 1, P.OK, 5, bytes(480))))},
      'resp_not_multiple': {'wire_length_sent': len(F.pad_for_packet(P.pack_infer_resp(3, 1, P.OK, 5, bytes(8))))},
    },
    'stub_server': {'vision_floats': backend.vision_floats, 'policy_floats': backend.policy_floats,
                    'note': 'one connection, HELLO then three INFER_REQs (first with RESET); StubBackend is defined in server_ref.py',
                    'steps': steps},
  }


if __name__ == '__main__':
  print(json.dumps(build(), indent=1, sort_keys=True))
