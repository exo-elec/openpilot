"""Sample-clock endpointing after wake: speech holds the recording open."""
class Endpoint:
  def __init__(self, rate=16000, silence_seconds=.8, start_timeout=3):
    self.rate = rate
    self.silence_limit = int(rate * silence_seconds)
    self.start_limit = int(rate * start_timeout)
    self.total = self.silence = 0
    self.heard_speech = False

  def feed(self, samples, speech):
    self.total += samples
    if speech:
      self.heard_speech = True
      self.silence = 0
    else:
      self.silence += samples
    if self.total >= self.rate * 15:
      return 'send' if self.heard_speech else 'cancel'
    if self.heard_speech and self.silence >= self.silence_limit:
      return 'send'
    if not self.heard_speech and self.total >= self.start_limit:
      return 'cancel'
    return ''
