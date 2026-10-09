import numpy as np
from openpilot.system.waked.detector import WakeDetector
from openpilot.system.voiced.utterance import Endpoint


class Model:
  def __init__(self): self.frames = []
  def predict(self, frame):
    self.frames.append(frame.copy())
    return {'hi_exo': .9}
  def reset(self): pass


def test_chunking_threshold_and_cooldown():
  model = Model()
  detector = WakeDetector('', model=model)
  data = np.ones(640, dtype=np.int16).tobytes()
  assert not detector.process(data, now=1)
  assert detector.process(data, now=1)
  assert not detector.process(data * 2, now=2)
  assert detector.process(data * 2, now=5)
  assert all(len(frame) == 1280 for frame in model.frames)


def test_speech_continues_until_pause():
  endpoint = Endpoint()
  for _ in range(100):
    assert endpoint.feed(800, True) == ''
  for _ in range(15):
    assert endpoint.feed(800, False) == ''
  assert endpoint.feed(800, False) == 'send'


def test_pause_reset_timeout_and_limit():
  endpoint = Endpoint()
  assert endpoint.feed(16000, False) == ''
  assert endpoint.feed(16000, False) == ''
  assert endpoint.feed(16000, False) == 'cancel'
  endpoint = Endpoint()
  assert endpoint.feed(8000, True) == ''
  assert endpoint.feed(8000, False) == ''
  assert endpoint.feed(8000, True) == ''
  assert endpoint.feed(16000 * 14, True) == 'send'
