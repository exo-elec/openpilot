#!/usr/bin/env python3
"""Spoken turn-by-turn on top of upstream's navd (selfdrive/navigation): reads navInstruction, sends ttsRequest.

The announcements of EOP10's former Valhalla navd, kept when it was replaced by upstream's: each maneuver is spoken once
per stage, approach (500-200 m), imminent (200-80 m) and execute (80-20 m), and arrival once under 50 m. The text is
the route's own banner (Valhalla's banner instructions, e.g. "Turn right onto Main Street") with the distance in
front. NavVoiceEnabled switches it (read every 2 s); ttsRequest exists on ExoPilot boards only.
"""
from dataclasses import dataclass

STAGES = (("approach", 500.0, 200.0), ("imminent", 200.0, 80.0), ("execute", 80.0, 20.0))
ARRIVAL_M = 50.0


def spoken_distance(meters: float, metric: bool) -> str:
  if metric:
    if meters >= 1000:
      return f"{meters / 1000:.1f} kilometers".replace(".0 ", " ")
    return f"{int(round(meters / 50.0) * 50)} meters"
  feet = meters * 3.28084
  if feet >= 1000:
    return f"{meters / 1609.344:.1f} miles".replace(".0 ", " ")
  return f"{int(round(feet / 100.0) * 100)} feet"


@dataclass(frozen=True)
class Instruction:
  valid: bool
  primary_text: str = ""
  maneuver_type: str = ""
  distance: float = 0.0          # navInstruction.maneuverDistance, m
  distance_remaining: float = 0.0


class NavVoice:
  def __init__(self):
    self._spoken: set[tuple[str, str]] = set()
    self._arrived = False

  def update(self, ins: Instruction, metric: bool = True) -> str | None:
    """The sentence to speak now, or None."""
    if not ins.valid:
      self._spoken.clear()
      self._arrived = False
      return None
    if ins.maneuver_type == "arrive" or 0.0 < ins.distance_remaining < ARRIVAL_M:
      if ins.distance < ARRIVAL_M and not self._arrived:
        self._arrived = True
        return "You have arrived at your destination"
      return None
    self._arrived = False
    text = ins.primary_text.strip()
    if not text:
      return None
    for stage, far, near in STAGES:
      if near < ins.distance <= far:
        key = (text, stage)
        if key in self._spoken:
          return None
        self._spoken = {k for k in self._spoken if k[0] == text} | {key}   # a new maneuver starts fresh
        return text if stage == "execute" else f"In {spoken_distance(ins.distance, metric)}, {text[0].lower()}{text[1:]}"
    return None


def main():
  import cereal.messaging as messaging
  from openpilot.common.params import Params
  from openpilot.common.realtime import Ratekeeper

  params = Params()
  sm = messaging.SubMaster(['navInstruction'])
  pm = messaging.PubMaster(['ttsRequest'])
  voice = NavVoice()
  rk = Ratekeeper(2.0)
  enabled, metric = True, True
  while True:
    sm.update(0)
    if sm.frame % 4 == 0:
      enabled, metric = params.get_bool("NavVoiceEnabled"), params.get_bool("IsMetric")
    n = sm['navInstruction']
    ins = Instruction(sm.valid['navInstruction'] and sm.alive['navInstruction'], n.maneuverPrimaryText, n.maneuverType,
                      float(n.maneuverDistance), float(n.distanceRemaining))
    text = voice.update(ins, metric)
    if text and enabled:
      msg = messaging.new_message('ttsRequest')
      msg.ttsRequest.text = text
      msg.ttsRequest.priority = 1
      msg.ttsRequest.interrupt = True
      pm.send('ttsRequest', msg)
    rk.keep_time()


if __name__ == "__main__":
  main()
