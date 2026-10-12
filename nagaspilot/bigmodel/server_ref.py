"""Reference phone server for the NPBM link: the executable spec of what NavPilot's Swift and Kotlin servers do.

Python, numpy and the standard library. It holds the state `ModelState.run` keeps on the comma (the frame ring, the feature
and desire history), runs a pluggable backend, and answers the protocol. `StubBackend` is deterministic and needs no model
(tests, protocol bring-up); `OnnxBackend` runs the split v0.10 vision and policy ONNX with ONNX Runtime on a PC.

    python -m nagaspilot.bigmodel.server_ref --stub --dial 192.168.60.1   # the phone's mode: dial the comma on the cable
    python -m nagaspilot.bigmodel.server_ref --stub            # listen on 0.0.0.0:5600 (tests, a LAN)
    python -m nagaspilot.bigmodel.server_ref --vision v.onnx --policy p.onnx --hidden-slice 0:512 --vision-floats 1024 --policy-floats 6000
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import socket
import threading
import time

import numpy as np

from nagaspilot.bigmodel import protocol as P

log = logging.getLogger('nagaspilot.bigmodel.server')

# the v0.10 model contract (selfdrive/modeld/constants.py); duplicated, a server must not import openpilot
MODEL_FREQ, HISTORY_FREQ, HISTORY_SECONDS = 20, 5, 5
TEMPORAL_SKIP = MODEL_FREQ // HISTORY_FREQ
FULL_HISTORY = MODEL_FREQ * HISTORY_SECONDS
INPUT_HISTORY = HISTORY_FREQ * HISTORY_SECONDS
FEATURE_LEN = 512
TEMPORAL_IDXS = slice(-1 - (TEMPORAL_SKIP * (INPUT_HISTORY - 1)), None, TEMPORAL_SKIP)
CAMERAS = ('img', 'big_img')


class Backend:
  name = ''
  sha256 = ''
  vision_floats = 0
  policy_floats = 0

  def run_vision(self, imgs: dict[str, np.ndarray]) -> np.ndarray:
    """imgs: name -> uint8 (1, 12, 128, 256), older frame then newest. Returns the flat float32 vision output."""
    raise NotImplementedError

  def hidden_state(self, vision_out: np.ndarray) -> np.ndarray:
    raise NotImplementedError

  def run_policy(self, inputs: dict[str, np.ndarray]) -> np.ndarray:
    """inputs: desire (1,25,8), traffic_convention (1,2), features_buffer (1,25,512). Returns the flat float32 policy output."""
    raise NotImplementedError


class StubBackend(Backend):
  """Deterministic: the outputs are a function of the inputs only, so a test can recompute them and tell a wrong history
  from a right one."""
  name = 'stub'

  def __init__(self, vision_floats: int = 1024, policy_floats: int = 600):
    assert vision_floats >= FEATURE_LEN
    self.vision_floats, self.policy_floats = vision_floats, policy_floats
    self.sha256 = hashlib.sha256(b'stub').hexdigest()

  def run_vision(self, imgs):
    out = np.zeros(self.vision_floats, np.float32)
    mean = sum(float(imgs[c].mean()) for c in CAMERAS)
    out[:FEATURE_LEN] = mean + np.arange(FEATURE_LEN, dtype=np.float32) * 1e-3
    out[FEATURE_LEN:] = mean
    return out

  def hidden_state(self, vision_out):
    return vision_out[:FEATURE_LEN]

  def run_policy(self, inputs):
    # float64 sums, so another implementation (the phone's Dart one) reproduces it without matching numpy's float32 pairwise order
    s = sum(float(inputs[k].sum(dtype=np.float64)) for k in ('features_buffer', 'desire', 'traffic_convention'))
    return np.full(self.policy_floats, s, np.float32)


class OnnxBackend(Backend):
  """The split v0.10 models on ONNX Runtime. `hidden` is the slice of the vision output that is the feature vector."""

  def __init__(self, vision_path: str, policy_path: str, hidden: slice, vision_floats: int, policy_floats: int):
    import onnxruntime as ort
    self.vision = ort.InferenceSession(vision_path, providers=['CPUExecutionProvider'])
    self.policy = ort.InferenceSession(policy_path, providers=['CPUExecutionProvider'])
    self.hidden = hidden
    self.vision_floats, self.policy_floats = vision_floats, policy_floats
    h = hashlib.sha256()
    for p in (vision_path, policy_path):
      with open(p, 'rb') as f:
        h.update(f.read())
    self.sha256 = h.hexdigest()
    self.name = 'onnx'

  def run_vision(self, imgs):
    return np.asarray(self.vision.run(None, dict(imgs))[0], np.float32).reshape(-1)

  def hidden_state(self, vision_out):
    return vision_out[self.hidden]

  def run_policy(self, inputs):
    return np.asarray(self.policy.run(None, inputs)[0], np.float32).reshape(-1)


class State:
  """The history a comma's ModelState keeps, advanced once per INFER_REQ exactly as `ModelState.run` advances it."""

  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.frames: dict[str, list[np.ndarray]] = {c: [] for c in CAMERAS}
    self.features = np.zeros((1, FULL_HISTORY, FEATURE_LEN), np.float32)
    self.full_desire = np.zeros((1, FULL_HISTORY, P.DESIRE_LEN), np.float32)
    self.prev_desire = np.zeros(P.DESIRE_LEN, np.float32)


def step(state: State, backend: Backend, desire, traffic, frame: np.ndarray) -> np.ndarray | None:
  """One frame. Returns the flat float32 output (vision then policy), or None if it is not finite, in which case `state` is
  exactly what it was before the call."""
  desire = np.asarray(desire, np.float32).copy()
  desire[0] = 0
  new_desire = np.where(desire - state.prev_desire > .99, desire, 0).astype(np.float32)
  full_desire = state.full_desire.copy()
  full_desire[0, :-1] = full_desire[0, 1:]
  full_desire[0, -1] = new_desire
  policy_desire = full_desire.reshape((1, INPUT_HISTORY, TEMPORAL_SKIP, -1)).max(axis=2)

  frames = {}
  imgs = {}
  for i, cam in enumerate(CAMERAS):
    ring = (state.frames[cam] + [np.array(frame[i], np.uint8)])[-(TEMPORAL_SKIP + 1):]
    frames[cam] = ring
    older = ring[0]    # the oldest held until the ring is full: a reset history repeats its first frame
    imgs[cam] = np.concatenate([older, ring[-1]], axis=0)[np.newaxis]

  vision_out = backend.run_vision(imgs)
  features = state.features.copy()
  features[0, :-1] = features[0, 1:]
  features[0, -1] = backend.hidden_state(vision_out)
  policy_out = backend.run_policy({'desire': policy_desire.astype(np.float32),
                                   'traffic_convention': np.asarray(traffic, np.float32).reshape(1, -1),
                                   'features_buffer': features[0, TEMPORAL_IDXS][np.newaxis]})
  out = np.concatenate([vision_out, policy_out]).astype(np.float32)
  if not np.isfinite(out).all():
    return None
  state.frames, state.features, state.full_desire = frames, features, full_desire
  state.prev_desire = np.asarray(desire, np.float32).copy()
  return out


def dial(backend: Backend, host: str = P.COMMA_ADDR, port: int = P.DEFAULT_PORT, *, retry: float = 1.0,
         stop: threading.Event | None = None) -> None:
  """The phone's real mode: dial the comma on the cable and serve it, redialling when it drops. Blocks until `stop`."""
  stop = stop or threading.Event()
  server = Server(backend, listen=False)
  while not stop.is_set():
    try:
      conn = socket.create_connection((host, port), timeout=2.0)
    except OSError:
      stop.wait(retry)
      continue
    server.serve(conn)
    stop.wait(retry)


class Server:
  """Serves a comma over a connection. In `listen` mode (tests, a PC on a LAN) it accepts one at a time and a new HELLO
  replaces the old connection; `dial()` serves connections the phone opened itself."""

  def __init__(self, backend: Backend, host: str = '0.0.0.0', port: int = P.DEFAULT_PORT, *, listen: bool = True):
    self.backend = backend
    self.state = State()
    self.frames_served = 0
    self.leaves: list[dict] = []
    self._conn: socket.socket | None = None
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self.listener = None
    self.port = 0
    if listen:
      self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      self.listener.bind((host, port))
      self.listener.listen(2)
      self.port = self.listener.getsockname()[1]
      self.thread = threading.Thread(target=self._accept_loop, daemon=True)
      self.thread.start()

  def close(self) -> None:
    self._stop.set()
    for s in (self.listener, self._conn):
      try:
        if s is not None:
          s.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      try:
        if s is not None:
          s.close()
      except OSError:
        pass

  def _accept_loop(self) -> None:
    while not self._stop.is_set():
      try:
        conn, _ = self.listener.accept()
      except OSError:
        return
      with self._lock:
        old, self._conn = self._conn, conn
      if old is not None:
        try:
          old.close()
        except OSError:
          pass
      threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

  @staticmethod
  def _read(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
      chunk = conn.recv(n - len(buf))
      if not chunk:
        raise ConnectionError('closed')
      buf += chunk
    return bytes(buf)

  def serve(self, conn: socket.socket) -> None:
    """Answer one connection until it closes or breaks."""
    self._conn = conn
    self._serve(conn)

  def _serve(self, conn: socket.socket) -> None:
    if isinstance(conn, socket.socket):
      conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    hello_done = False
    try:
      while not self._stop.is_set():
        mtype, flags, seq, length = P.parse_header(self._read(conn, P.HEADER_SIZE))
        payload = self._read(conn, length) if length else b''
        if mtype == P.HELLO:
          try:
            P.check_hello(P.decode_json(payload))
          except P.ProtocolError as e:
            conn.sendall(P.pack(P.ERROR, seq, P.encode_json({'error': str(e)})))
            return
          self.state.reset()
          hello_done = True
          conn.sendall(P.pack(P.HELLO_ACK, seq, P.encode_json({
            'ok': True, 'model': self.backend.name, 'sha256': self.backend.sha256,
            'vision_floats': self.backend.vision_floats, 'policy_floats': self.backend.policy_floats, 'ready': True})))
        elif not hello_done:
          conn.sendall(P.pack(P.ERROR, seq, P.encode_json({'error': 'hello first'})))
          return
        elif mtype == P.PING:
          conn.sendall(P.pack(P.PONG, seq))
        elif mtype == P.INFER_REQ:
          req = P.unpack_infer_req(payload)
          t0 = time.perf_counter()
          if req.flags & P.RESET:
            self.state.reset()
          frame = np.frombuffer(req.frame, np.uint8).reshape(P.FRAME_SHAPE)
          try:
            out = step(self.state, self.backend, req.desire, req.traffic, frame)
            status = P.OK if out is not None else P.NOT_FINITE
          except Exception:
            log.exception('backend failed')
            out, status = None, P.INTERNAL
          us = int((time.perf_counter() - t0) * 1e6)
          self.frames_served += 1
          conn.sendall(P.pack_infer_resp(seq, req.frame_id, status, us, out.tobytes() if out is not None else b''))
        elif mtype == P.LEAVE:
          try:
            self.leaves.append(P.decode_json(payload))
          except P.ProtocolError:
            pass
        # PONG, INFER_RESP, HELLO_ACK, ERROR from a comma are ignored
    except (ConnectionError, OSError):
      pass
    except P.ProtocolError as e:
      log.warning('broken stream: %s', e)
    finally:
      try:
        conn.close()
      except OSError:
        pass


def main() -> None:
  ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  ap.add_argument('--stub', action='store_true')
  ap.add_argument('--vision'), ap.add_argument('--policy')
  ap.add_argument('--hidden-slice', default='0:512')
  ap.add_argument('--vision-floats', type=int), ap.add_argument('--policy-floats', type=int)
  ap.add_argument('--host', default='0.0.0.0'), ap.add_argument('--port', type=int, default=P.DEFAULT_PORT)
  ap.add_argument('--dial', metavar='HOST', help=f'dial the comma at HOST:PORT (the phone\'s mode; the cable address is {P.COMMA_ADDR}) instead of listening')
  a = ap.parse_args()
  logging.basicConfig(level=logging.INFO)
  if a.stub:
    backend: Backend = StubBackend()
  else:
    if not (a.vision and a.policy and a.vision_floats and a.policy_floats):
      ap.error('give --stub, or --vision --policy --vision-floats --policy-floats')
    lo, hi = (int(x) for x in a.hidden_slice.split(':'))
    backend = OnnxBackend(a.vision, a.policy, slice(lo, hi), a.vision_floats, a.policy_floats)
  if a.dial:
    log.info('dialling %s:%d with %s', a.dial, a.port, backend.name)
    dial(backend, a.dial, a.port)
    return
  server = Server(backend, a.host, a.port)
  log.info('serving %s on %s:%d', backend.name, a.host, server.port)
  server.thread.join()


if __name__ == '__main__':
  main()
