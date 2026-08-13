#!/usr/bin/env python3
"""
EOP: Lane Change Lead Handoff (Pure Camera)

During laneChangeStarting, switch longitudinal MPC to track the lead vehicle
in the *target* lane (from modelV2.leadsV3) instead of the current-lane lead.
This creates human-like gap behavior — you naturally start following the car
you're merging behind.

Param: EOPLCAdjacentLeadHandoff (default 0 — opt-in)
"""
from dataclasses import dataclass
from typing import Any

from cereal import log
from openpilot.common.params import Params
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL
from nagaspilot.speed_zones import URBAN_SPEED_MPS

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

# EOP 3-zone speed boundary: ALC disabled in zone 1 (< 12 m/s)
LANE_CHANGE_SPEED_MIN = URBAN_SPEED_MPS

# Lateral threshold for adjacent lane
ADJACENT_LANE_Y_MIN = 1.5  # m, |y| > 1.5

MIN_LEAD_PROB = 0.5
HANDOFF_PERSISTENCE_S = 1.0
HANDOFF_SMOOTHING_TAU = 0.3

RADAR_TO_CAMERA = 1.52  # m, same offset used by radard.py vision path


@dataclass
class _LeadProxy:
  """Minimal lead struct compatible with longitudinal_mpc.process_lead()."""
  status: bool
  dRel: float
  yRel: float
  vRel: float
  vLead: float
  vLeadK: float
  aLeadK: float
  aLeadTau: float
  modelProb: float
  fcw: bool = False
  radar: bool = False
  radarTrackId: int = -1


class LaneChangeLeadHandoff:
  """
  EOP Lane Change Lead Handoff — pure camera.

  Uses modelV2.leadsV3 to detect adjacent-lane traffic and produces a
  radarState-compatible wrapper so longitudinal MPC can track the target-lane
  lead without any radar hardware.
  """

  def __init__(self):
    self.enabled = bool(Params().get_bool("EOPLCAdjacentLeadHandoff"))
    self._active = False
    self._proxy: _LeadProxy | None = None
    self._persist_until = 0.0
    self._last_lc_state = LaneChangeState.off
    self._d_filter = FirstOrderFilter(0.0, HANDOFF_SMOOTHING_TAU, DT_MDL)

  @staticmethod
  def _pick_adjacent_lead(model_v2, direction: int, v_ego: float) -> _LeadProxy | None:
    if model_v2 is None or len(model_v2.leadsV3) == 0:
      return None

    picks = []
    for lead in model_v2.leadsV3:
      if lead.prob < MIN_LEAD_PROB:
        continue
      y = lead.y[0]   # positive = left
      x = lead.x[0] - RADAR_TO_CAMERA
      if direction == LaneChangeDirection.left and y > ADJACENT_LANE_Y_MIN:
        picks.append((x, y, lead))
      elif direction == LaneChangeDirection.right and y < -ADJACENT_LANE_Y_MIN:
        picks.append((x, y, lead))

    if not picks:
      return None

    picks.sort(key=lambda c: c[0])
    x, y, lead = picks[0]
    v_rel = lead.v[0] - v_ego

    return _LeadProxy(
      status=True,
      dRel=float(x),
      yRel=float(-y),
      vRel=float(v_rel),
      vLead=float(lead.v[0]),
      vLeadK=float(lead.v[0]),
      aLeadK=float(lead.a[0]) if len(lead.a) > 0 else 0.0,
      aLeadTau=0.3,
      modelProb=float(lead.prob),
    )

  def _wrap(self, base_radar, proxy: _LeadProxy):
    """Wrap base radarState so MPC sees proxy as leadOne."""
    class _Wrap:
      def __init__(self, base, lead):
        self._base = base
        self._lead = lead

      @property
      def leadOne(self):
        return self._lead

      @property
      def leadTwo(self):
        if self._base is not None and self._base.leadOne.status:
          return self._base.leadOne
        if self._base is not None and self._base.leadTwo.status:
          return self._base.leadTwo
        return _LeadProxy(status=False, dRel=0.0, yRel=0.0, vRel=0.0,
                          vLead=0.0, vLeadK=0.0, aLeadK=0.0, aLeadTau=0.3, modelProb=0.0)

      def __getattr__(self, name):
        return getattr(self._base, name)

    return _Wrap(base_radar, proxy)

  def update(self, model_v2, radar_state, lc_state: int, lc_dir: int,
             v_ego: float, now: float) -> Any:
    """
    Returns either the original radar_state or a wrapped version with the
    adjacent lead injected as leadOne.
    """
    if not self.enabled or v_ego < LANE_CHANGE_SPEED_MIN:
      self._active = False
      self._proxy = None
      return radar_state

    # Detect state transitions
    just_started = (
      lc_state == LaneChangeState.laneChangeStarting and
      self._last_lc_state != LaneChangeState.laneChangeStarting
    )
    just_finished = (
      lc_state in (LaneChangeState.off, LaneChangeState.preLaneChange) and
      self._last_lc_state in (LaneChangeState.laneChangeStarting, LaneChangeState.laneChangeFinishing)
    )

    if just_finished:
      self._persist_until = now + HANDOFF_PERSISTENCE_S

    if lc_state == LaneChangeState.laneChangeStarting:
      adjacent = self._pick_adjacent_lead(model_v2, lc_dir, v_ego)
      if adjacent is not None:
        if not self._active or self._proxy is None or just_started:
          self._d_filter.x = adjacent.dRel
          self._proxy = adjacent
        else:
          self._d_filter.update(adjacent.dRel)
          self._proxy = _LeadProxy(
            status=True,
            dRel=float(self._d_filter.x),
            yRel=adjacent.yRel,
            vRel=adjacent.vRel,
            vLead=adjacent.vLead,
            vLeadK=adjacent.vLeadK,
            aLeadK=adjacent.aLeadK,
            aLeadTau=adjacent.aLeadTau,
            modelProb=adjacent.modelProb,
          )
        self._active = True
      else:
        self._active = False
        self._proxy = None

    elif lc_state == LaneChangeState.laneChangeFinishing:
      pass  # keep existing handoff
    else:
      self._active = False
      if now > self._persist_until:
        self._proxy = None

    self._last_lc_state = lc_state

    if self._active and self._proxy is not None:
      return self._wrap(radar_state, self._proxy)
    return radar_state
