import base64
import time
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.system.cloudd.client import CloudClient, NoRedirect
from openpilot.system.cloudd.cloudd import CloudWorker, Job
from openpilot.system.cloudd.codec import OpusCodec
from openpilot.system.spkd.playback import SpeechBuffer, volume_pcm


@pytest.fixture
def pcm():
  return (np.sin(np.arange(16000) * 2 * np.pi * 440 / 16000) * 10000).astype('<i2').tobytes()


def test_real_opus_roundtrip_and_size(pcm):
  codec = OpusCodec()
  audio = codec.encode(pcm)
  assert len(audio) < len(pcm) // 3
  decoded = np.frombuffer(codec.decode(audio), dtype='<i2')
  assert abs(len(decoded) - 48000) < 960
  assert np.sqrt(np.mean(decoded.astype(float) ** 2)) > 4000


def test_codec_bounds_and_wrong_formats(pcm):
  codec = OpusCodec()
  with pytest.raises(ValueError): codec.encode(pcm, 48000)
  with pytest.raises(ValueError): codec.encode(pcm * 16)
  with pytest.raises(ValueError): codec.decode(b'raw PCM')


def test_speaker_keeps_pcm16_amplitude_and_bounds():
  buffer = SpeechBuffer()
  pcm = np.full(480, 10000, dtype=np.int16)
  buffer.replace(pcm.tobytes())
  chunk = buffer.pop()
  assert volume_pcm(chunk, .5)[0] == 5000  # Prior float -> int cast yielded zero.
  assert not buffer
  buffer.replace(pcm.tobytes())
  buffer.clear()  # Alert preemption drops pending online speech.
  assert buffer.pop() is None
  with pytest.raises(ValueError): buffer.replace(pcm.tobytes(), 16000)


def test_queue_expiry_disable_and_error_sanitizing(pcm):
  class BadClient:
    def converse(self, *args): raise RuntimeError('SECRET')
  worker = CloudWorker(BadClient())
  result = worker.process(Job(1, time.monotonic(), 'voice', pcm))
  assert result[2] == 'Online voice unavailable' and not result[1]
  assert worker.process(Job(1, time.monotonic() - 9, 'voice', pcm)) is None
  worker.enabled = lambda: False
  assert worker.process(Job(1, time.monotonic(), 'voice', pcm)) is None
  assert all(worker.submit('voice', pcm) for _ in range(4))
  assert not worker.submit('voice', pcm)


def test_client_contract_redirect_and_response_limits(pcm):
  encoded = OpusCodec().encode(pcm)
  class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit):
      import json
      return json.dumps({'replyAudio': base64.b64encode(encoded).decode(), 'replyText': 'Hello',
                          'transcribedText': 'hello', 'detectedLanguage': 'en-US',
                          'proposedAction': {'action': 'accelerate'}}).encode()
  requests = []
  class Opener:
    def open(self, req, timeout):
      requests.append(req)
      return Response()
  client = CloudClient('https://gateway.example', 'token', Opener())
  worker = CloudWorker(client)
  result, decoded, error = worker.process(Job(1, time.monotonic(), 'voice', pcm))
  assert not error and decoded and result['reply'] == 'Hello'
  assert 'proposedAction' not in result
  assert requests[0].headers['Content-type'] == 'audio/ogg; codecs=opus'
  assert NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil') is None
  with pytest.raises(ValueError): CloudClient('http://gateway.example', 'token')
  with pytest.raises(ValueError): CloudClient('https://token@gateway.example', 'token')


def test_disabled_while_request_in_flight_discards_reply(pcm):
  active = [True]
  encoded = OpusCodec().encode(pcm)
  class Client:
    def converse(self, *args):
      active[0] = False
      return {'audio': encoded}
  worker = CloudWorker(Client(), enabled=lambda: active[0])
  assert worker.process(Job(1, time.monotonic(), 'voice', pcm)) is None


def test_bad_gateway_response_is_rejected():
  class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return b'{"replyAudio":"bm90IG9wdXM="}'
  client = CloudClient('https://gateway.example', 'token', SimpleNamespace(open=lambda *args, **kw: Response()))
  with pytest.raises(ValueError): client.converse(b'OggS', 'en-US')
