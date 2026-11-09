"""Lead-departure notice: the car ahead pulls away while we are stopped.

Pure policy, like ngp_tja / ngp_brsc: input is `CarState.standstill` and a `radarState` lead, output is
a bool for the caller to turn into an event. Works from comma 3 inputs only. The baseline range is the
smallest range seen since the stop (a lead creeping closer never counts as departing) and is dropped
whenever the lead is lost or its radar track changes, so a lead picked up again at a larger range is
not reported as having driven off.
"""
DEPART_DISTANCE_M = 1.0  # range gained since the stop
DEPART_SPEED_MPS = 1.0  # lead's own speed


class NGPLeadDeparture:
  def __init__(self):
    self._baseline: float | None = None
    self._track_id: int | None = None

  def update(self, standstill: bool, lead) -> bool:
    if not (standstill and lead.status):
      self._baseline = None
      self._track_id = None
      return False

    track_id = getattr(lead, 'radarTrackId', -1)
    if self._baseline is None or track_id != self._track_id:
      self._baseline = lead.dRel
      self._track_id = track_id
      return False

    self._baseline = min(self._baseline, lead.dRel)
    return lead.dRel - self._baseline > DEPART_DISTANCE_M and lead.vLead > DEPART_SPEED_MPS
