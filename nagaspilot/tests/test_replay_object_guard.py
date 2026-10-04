from types import SimpleNamespace as NS

from nagaspilot.tools.replay_object_guard import analyze


class Msg:
  def __init__(self, which, t, **fields):
    self._w, self.logMonoTime = which, int(t * 1e9)
    setattr(self, which, NS(**fields))

  def which(self):
    return self._w


def lead(d=0.0, y=0.0, status=True):
  return NS(dRel=d, yRel=y, status=status)


def det(tid, x, y, vx, vy, conf=0.9):
  return NS(trackId=tid, x=x, y=y, vx=vx, vy=vy, sigmaX=1.0, confidence=conf, className='car')


def log(n=40):
  msgs = []
  for i in range(n):
    t = i * 0.2
    msgs.append(Msg('carState', t, vEgo=25.0))
    msgs.append(Msg('radarState', t, leadOne=lead(30.0, 0.0), leadTwo=lead(status=False)))
    dets = [det(1, 31.5, 0.0, 0.0, 0.0)]                                   # a followed lead, range 5 % long
    if i >= 20:
      dets.append(det(2, 22.0, 3.0 - 0.3 * (i - 20), -6.0, -1.5))          # a car cutting in from the left
    msgs.append(Msg('monoDetections', t, detections=dets, modelExecutionTime=0.02))
  return msgs


def test_metrics_on_synthetic_log():
  r = analyze(log())
  assert r['monoDetections_frames'] == 40 and abs(r['rate_hz'] - 5.0) < 0.3
  assert r['exec_time_s']['max'] == 0.02
  assert r['cutin_would_trigger'] >= 1 and r['cutin_triggers'][0]['track_id'] == 2
  rv = r['ranging_vs_leads']
  assert rv['matched'] > 0 and abs(rv['median_rel_err'] - 0.05) < 1e-6
  assert r['detector_lead_time_s']['n'] >= 1


def test_empty_log():
  r = analyze([])
  assert r['monoDetections_frames'] == 0 and r['cutin_would_trigger'] == 0 and r['rate_hz'] is None


def test_model_path_is_used_when_logged():
  # same cut-in, but the logged model path bends toward the car (it is already "in" our lane): no trigger
  msgs = []
  for m in log():
    msgs.append(m)
    if m.which() == 'carState':
      msgs.append(Msg('modelV2', m.logMonoTime * 1e-9, position=NS(x=[0.0, 20.0, 40.0, 80.0], y=[0.0, -1.0, -4.0, -12.0])))  # y-right: path bends LEFT
  assert analyze(msgs)['cutin_would_trigger'] == 0
