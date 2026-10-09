#!/usr/bin/env python3
"""cloudd: queued Google gateway requests and compressed voice replies.

Only codec work runs locally. Network/codec failures never enter the actuator
loop. Audio requests expire instead of being replayed after reconnect.
"""
import os
import queue
import threading
import time
from dataclasses import dataclass, field

from openpilot.system.cloudd.client import CloudClient
from openpilot.system.cloudd.codec import OpusCodec


@dataclass(order=True)
class Job:
  priority: int
  created: float
  kind: str = field(compare=False)
  data: object = field(compare=False)
  language: str = field(default='en-US', compare=False)


class CloudWorker:
  def __init__(self, client, codec=None, enabled=lambda: True, activity=lambda active: None):
    self.client, self.codec, self.enabled = client, codec or OpusCodec(), enabled
    self.activity = activity
    self.jobs = queue.PriorityQueue(maxsize=4)
    self.results = queue.Queue(maxsize=4)
    self.stopped = threading.Event()
    self.thread = threading.Thread(target=self.run, daemon=True)

  def submit(self, kind, data, language='en-US', priority=2):
    if kind == 'voice' and len(data) > 16000 * 2 * 15:
      return False
    if kind != 'voice' and len(str(data).encode()) > 4000:
      return False
    try:
      self.jobs.put_nowait(Job(priority, time.monotonic(), kind, data, language))
      return True
    except queue.Full:
      return False

  def process(self, job):
    if not self.enabled() or time.monotonic() - job.created > 8:
      return None
    self.activity(True)
    try:
      if job.kind == 'voice':
        result = self.client.converse(self.codec.encode(job.data), job.language)
      else:
        result = self.client.speak(job.data, job.language)
      pcm = self.codec.decode(result['audio'])
      if not self.enabled():
        return None  # Disabling during an in-flight request discards the result.
      return (result, pcm, '')
    except Exception:
      # Never publish raw provider/HTTP exceptions containing credentials/body.
      return ({}, b'', 'Online voice unavailable')
    finally:
      self.activity(False)

  def run(self):
    while not self.stopped.is_set():
      try:
        job = self.jobs.get(timeout=0.2)
      except queue.Empty:
        continue
      result = self.process(job)
      if result is not None:
        try:
          self.results.put_nowait(result)
        except queue.Full:
          pass


def main():
  from cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.realtime import Ratekeeper
  from openpilot.common.core_config import set_daemon_affinity
  from openpilot.common.swaglog import cloudlog
  set_daemon_affinity('cloudd')
  params = Params()
  try:
    client = CloudClient.configured()
  except (OSError, ValueError):
    client = None
  if client is None:
    cloudlog.error('cloudd: HTTPS gateway/private credential file not configured')
    # Stay healthy but inert until restart after configuration changes.
  enabled = lambda: params.get_bool('EOPCloudVoiceEnabled') and params.get_bool('EOPVoiceEnabled')
  params.put_bool("EOPVoiceProcessing", False)
  worker = CloudWorker(client, enabled=enabled, activity=lambda active: params.put_bool("EOPVoiceProcessing", active))
  worker.thread.start()
  audio = messaging.sub_sock('voiceAudioChunk', conflate=False)
  speech = messaging.sub_sock('ttsRequest', conflate=False)
  pm = messaging.PubMaster(['cloudAudioData', 'voiceCommand'])
  rk = Ratekeeper(20)
  language = os.environ.get('EOP_CLOUD_LANGUAGE', 'en-US')
  try:
    while True:
      for evt in messaging.drain_sock(audio):
        a = evt.voiceAudioChunk
        if enabled() and a.sampleRate == 16000 and a.channels == 1 and a.sampleWidth == 2:
          worker.submit('voice', bytes(a.chunk), language, priority=1)
      for evt in messaging.drain_sock(speech):
        r = evt.ttsRequest
        if enabled() and params.get_bool('EOPNavVoiceEnabled'):
          worker.submit('tts', str(r.text), str(r.language) or language, int(r.priority))
      while not worker.results.empty():
        result, pcm, error = worker.results.get_nowait()
        command = messaging.new_message('voiceCommand', valid=True)
        command.voiceCommand.timestamp = time.monotonic_ns()
        command.voiceCommand.transcript = result.get('transcript', '')
        command.voiceCommand.language = result.get('language', language)
        command.voiceCommand.error = error
        # UI receives the reply through this field; never a vehicle action.
        command.voiceCommand.intent = result.get('reply', '')
        pm.send('voiceCommand', command)
        if pcm and enabled():
          msg = messaging.new_message('cloudAudioData', valid=True)
          msg.cloudAudioData.data = pcm
          msg.cloudAudioData.sampleRate = 48000
          msg.cloudAudioData.channels = 1
          pm.send('cloudAudioData', msg)
      rk.keep_time()
  finally:
    worker.stopped.set()
    worker.thread.join(timeout=1)


if __name__ == '__main__':
  main()
