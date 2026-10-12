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
from nagaspilot.bigmodel.queues import PolicyQueues, StateLoop
from nagaspilot.bigmodel.spec import ModelSpec


def frame(value: int) -> bytes:
  return bytes([value]) * P.FRAME_BYTES


# a small combined model with the real warped frame: 8-float features, 28 outputs of which 8 are the hidden state
QUEUED_SHAPES = {'img': (1, 12, 128, 256), 'big_img': (1, 12, 128, 256), 'desire_pulse': (1, 33, 8), 'traffic_convention': (1, 2),
                 'action_t': (1, 2), 'features_buffer': (1, 32, 2, 4)}


def queued_vectors() -> dict:
  spec = ModelSpec(sha256='cd' * 32, nbytes=1, frame_skip=4, input_shapes=QUEUED_SHAPES, output_shapes={'outputs': (1, 28)},
                   output_slices={'hidden_state': slice(2, 10)}, checkpoint='golden')
  backend, queues = S.StubSpecBackend(spec), PolicyQueues(spec)
  queues.new_client()
  steps = []
  for i in range(10):
    packed = [1.0 if i == 3 else 0.0] + [0.0] * 7 + [1.0, 0.0] + [0.275, 0.525]
    fill = (i * 37) % 251
    reply = S.step_spec(queues, backend, packed, np.full(spec.warped_shape, fill, np.uint8))
    steps.append({'flags': P.RESET if i == 0 else 0, 'packed': packed, 'frame_fill': fill, 'reply': reply.tolist()})
  req = P.pack_infer_req_packed(9, 77, P.RESET, steps[0]['packed'], bytes([steps[0]['frame_fill']]) * P.FRAME_BYTES)
  ack = {'ok': True, 'contract': P.CONTRACT_QUEUED, 'model': backend.name, 'sha256': spec.sha256, 'spec': spec.to_dict(), 'ready': True}
  return {
    'contracts': list(P.CONTRACTS),
    'hello_message_seq1': P.pack(P.HELLO, 1, P.hello_payload((P.CONTRACT_QUEUED, P.CONTRACT_SPLIT))).hex(),
    'hello_payload': P.hello_payload((P.CONTRACT_QUEUED, P.CONTRACT_SPLIT)).decode(),
    'ack': ack, 'ack_message_seq1': P.pack(P.HELLO_ACK, 1, P.encode_json(ack)).hex(),
    'packed_nelem': spec.packed_nelem, 'reply_nelem': spec.reply_nelem, 'warped_nbytes': spec.warped_nbytes,
    'infer_req_seq9': {'header_hex': req[:P.HEADER_SIZE].hex(), 'fixed_hex': req[P.HEADER_SIZE:P.HEADER_SIZE + 8 + spec.packed_nelem * 4].hex(),
                       'total_length': len(req)},
    'weights': S.StubSpecBackend.WEIGHTS, 'steps': steps,
  }


# a small stateful graph (openpilot #38916 layout) with the real warped frame: two looped states, 24 outputs, no hidden_state
STATEFUL_SHAPES = {'new_img': (2, 6, 128, 256), 'desire': (1, 8), 'traffic_convention': (1, 2), 'action_t': (1, 2),
                   'state_feat': (1, 16), 'state_hist': (1, 4, 3)}
STATEFUL_OUTPUTS = {'outputs': (1, 24), 'next_state_feat': (1, 16), 'next_state_hist': (1, 4, 3)}


def stateful_vectors() -> dict:
  spec = ModelSpec(sha256='ef' * 32, nbytes=1, frame_skip=4, input_shapes=STATEFUL_SHAPES, output_shapes=STATEFUL_OUTPUTS,
                   output_slices={'plan': slice(0, 6), 'meta': slice(6, 24)}, checkpoint='golden')
  backend, loop = S.StubStatefulBackend(spec), StateLoop(spec)
  steps = []
  for i in range(10):
    packed = [1.0 if i == 3 else 0.0] + [0.0] * 7 + [1.0, 0.0] + [0.275, 0.525]
    fill = (i * 53) % 251
    if i == 0:
      loop.reset()
    reply = S.step_spec(loop, backend, packed, np.full(spec.warped_shape, fill, np.uint8))
    steps.append({'flags': P.RESET if i == 0 else 0, 'packed': packed, 'frame_fill': fill, 'reply': reply.tolist()})
  ack = {'ok': True, 'contract': P.CONTRACT_STATEFUL, 'model': backend.name, 'sha256': spec.sha256, 'spec': spec.to_dict(), 'ready': True}
  return {'ack': ack, 'packed_nelem': spec.packed_nelem, 'reply_nelem': spec.reply_nelem, 'warped_nbytes': spec.warped_nbytes,
          'state_weight': S.StubStatefulBackend.STATE_WEIGHT, 'weights': S.StubStatefulBackend.WEIGHTS, 'steps': steps}


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
    'queued': queued_vectors(),
    'stateful': stateful_vectors(),
    'bulk_framing': {
      'note': 'Bulk transport only (setting 1). comma->phone: zero-pad every message to a multiple of burst. phone->comma: one zero byte on an exact multiple of packet.',  # noqa: E501
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
