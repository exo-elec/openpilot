"""openWakeWord ONNX CPU adapter; phrase is determined by model, not label."""
import time
import numpy as np


class WakeDetector:
  def __init__(self, model_path, threshold=.5, cooldown=3, model=None):
    if model is None:
      from openwakeword.model import Model
      model = Model(wakeword_models=[model_path], inference_framework='onnx')
      # Upstream forces CPU on wake/feature models. Verify, do not silently
      # accept another runtime provider supplied by a modified installation.
      sessions = list(model.models.values())
      sessions += [model.preprocessor.melspec_model, model.preprocessor.embedding_model]
      if any(s.get_providers() != ['CPUExecutionProvider'] for s in sessions):
        raise ValueError('waked requires CPUExecutionProvider only')
    self.model = model
    self.threshold, self.cooldown = threshold, cooldown
    self.pending = np.empty(0, dtype=np.int16)
    self.last = -float('inf')

  def reset(self):
    self.pending = np.empty(0, dtype=np.int16)
    self.model.reset()

  def process(self, pcm, now=None):
    now = time.monotonic() if now is None else now
    samples = np.frombuffer(pcm, dtype='<i2')
    if samples.size > 16000:
      self.reset()
      return False
    self.pending = np.concatenate((self.pending, samples))
    detected = False
    while self.pending.size >= 1280:
      frame, self.pending = self.pending[:1280], self.pending[1280:]
      scores = self.model.predict(frame)
      if max(scores.values(), default=0) >= self.threshold and now - self.last >= self.cooldown:
        self.last, detected = now, True
    return detected
