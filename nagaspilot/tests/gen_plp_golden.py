"""Regenerate fixtures/plp_golden.json (the frames, hello and exchange both languages must agree on), then copy it to navpilot's packages/plp/test/fixtures/.

    python3 nagaspilot/tests/gen_plp_golden.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from nagaspilot.plp import frame as F, registry as R  # noqa: E402
from nagaspilot.plp.hello import Hello  # noqa: E402
from nagaspilot.plp.session import PlpSession  # noqa: E402


def case(name, f):
  return {'name': name, 'hex': f.encode().hex(), 'kind': f.kind, 'service': f.service, 'message': f.message, 'channel': f.channel, 'encoding': f.encoding,
          'request_id': f.request_id, 'flags': f.flags, 'payload_hex': f.payload.hex()}


frames = [
  case('request_navigate', F.Frame(1, 3, 3, b'{"lat":13.75,"lon":100.5}', 2, F.ENC_JSON, 7)),
  case('notify_gnss_fix', F.Frame(0, 8, 1, b'{"t_ms":1}', 1, F.ENC_JSON, 0)),
  case('fragment_first', F.Frame(0, 8, 2, b'abcd', 3, F.ENC_JSON, 0, F.FLAG_MORE)),
  case('critical_empty', F.Frame(0, 9, 9, b'', 0, F.ENC_RAW, 0, F.FLAG_CRITICAL)),
  case('usb_no_payload_crc', F.Frame(0, 8, 2, b'abcd', 3, F.ENC_JSON, 0, F.FLAG_NO_PAYLOAD_CRC)),
]
svc = (('core', 1, 1), ('locate', 1, 1))
phone_hello = Hello(max_payload=4096, max_message=1 << 20, services=svc, link='usb', payload_crc=False)
dev_hello = Hello(max_payload=16384, max_message=1 << 20, services=svc, link='usb', payload_crc=False)
req = F.Frame(R.KIND_REQUEST, 0, 1, phone_hello.to_json(), F.CH_CONTROL, F.ENC_JSON, 1).encode()
unk = F.Frame(R.KIND_REQUEST, 99, 1, b'', F.CH_INTERACTIVE, F.ENC_JSON, 5).encode()
out = []
dev = PlpSession(dev_hello, out.append, role='device')
dev.feed(req)
dev.feed(unk)
sample = Hello(max_payload=512, max_message=65536, services=(('locate', 1, 2), ('nav', 1, 1)), link='ble')
g = {'crc32c_123456789': F.crc32c(b'123456789'), 'frames': frames, 'hello': {'json': sample.to_json().decode()},
     'exchange': {'phone_hello_json': phone_hello.to_json().decode(), 'device_hello_json': dev_hello.to_json().decode(), 'request_hex': req.hex(),
                  'response_hex': out[0].hex(), 'unknown_request_hex': unk.hex(), 'error_hex': out[1].hex()}}
path = pathlib.Path(__file__).parent / 'fixtures' / 'plp_golden.json'
path.write_text(json.dumps(g, indent=1))
print('wrote', path)
