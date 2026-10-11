"""NGP10's side of Jetlink's contract (jetlink.openpilot.interface.Openpilot, API 3).

Jetlink never imports openpilot; this module is the one place that does. Its top level imports only the
standard library, because manager runs it as the resident gadget owner `jetlinkd` (about 10 MB) through
main(); every openpilot import is inside the method that needs it. adapter() and owner_config() are the entry
points Jetlink's provisioning run and owner use (`--adapter nagaspilot.runtime.jetlink_adapter`).

The Jetlink checkout is the `jetlink_repo` gitlink at the repo root once pinned (see JETLINK_BIGMODEL.md);
until then it is found on sys.path or in JETLINK_REPO.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from nagaspilot.runtime import bigmodel_platform as platform

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = 'nagaspilot.runtime.jetlink_adapter'

KEYS = dict(link=platform.LINK, offroad='IsOffroad', progress=platform.PROGRESS, spec=platform.SPEC,
            pointers=platform.POINTERS, charge_phone=platform.CHARGE_PHONE)
# comma's chestnut and our own eGPU firmware, which the gadget owner must never take as a host
CHESTNUT_IDS = frozenset({(0xADD1, 0x0001), (0x3801, 0x0001), (0x174C, 0x2464), (0x174C, 0x2463)})


def _on_device() -> bool:
  return platform.on_device()


def comma_home() -> Path:
  return Path.home() / ('.comma' + os.environ.get('OPENPILOT_PREFIX', ''))


def params_dir() -> Path:
  """The params store's directory by params.cc's rule: PARAMS_ROOT, else ~/.comma/params on a PC and /data/params on
  a device, then /d (or OPENPILOT_PREFIX)."""
  root = os.environ.get('PARAMS_ROOT') or (str(comma_home() / 'params') if not _on_device() else '/data/params')
  return Path(root) / os.environ.get('OPENPILOT_PREFIX', 'd')


def log_file() -> Path:
  return (Path('/data/log') if _on_device() else comma_home() / 'log') / 'jetlink-owner.log'


def _checkout() -> Path:
  return Path(os.environ.get('JETLINK_REPO') or ROOT / 'jetlink_repo')


def jetlink_present() -> bool:
  """Is a Jetlink checkout there to run? Without one nothing on the link side can start, and the manager must not
  try (a dead jetlinkd is a processNotRunning event, which blocks engagement)."""
  return (_checkout() / 'jetlink').is_dir()


def _jetlink_path() -> None:
  checkout = _checkout()
  if checkout.is_dir() and str(checkout) not in sys.path:
    sys.path.insert(0, str(checkout))


def keys():
  _jetlink_path()
  from jetlink.openpilot.interface import Keys
  return Keys(**KEYS)


def owner_config():
  _jetlink_path()
  from jetlink.openpilot.interface import OwnerConfig
  paths = [str(ROOT)] + ([str(ROOT / 'jetlink_repo')] if (ROOT / 'jetlink_repo').is_dir() else [])
  env = {'PYTHONPATH': os.pathsep.join(paths + [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p])}
  return OwnerConfig(params_dir=params_dir(), keys=keys(), chestnut_ids=CHESTNUT_IDS, adapter=ADAPTER, cwd=ROOT,
                     env=env, log_file=log_file())


def main() -> None:
  """jetlinkd: hold the USB gadget until manager stops this process."""
  from jetlink.openpilot import owner
  owner.main(owner_config())


class NgpOpenpilot:
  """Every side of the interface in one object, as Jetlink expects of a fork."""

  catalog_selector = 0  # NGP10 has no model manager: Jetlink runs its own default big model

  def __init__(self):
    self.keys = keys()
    self.basedir = ROOT
    self._log = None
    self._sm = None
    from nagaspilot.runtime.bigmodel_warp import NgpWarpSource
    self.warp_source = NgpWarpSource()  # Jetlink's optional seam: NGP10 warps with OpenCL, not a tinygrad pickle
    self.fallback_action = None  # modeld's own get_action_from_model, set by the hook

  @property
  def log(self):
    if self._log is None:
      from openpilot.common.swaglog import cloudlog
      self._log = cloudlog
    return self._log

  def params_dir(self) -> Path:
    return params_dir()

  def _params(self):
    from openpilot.common.params import Params
    return Params()

  def get(self, key: str):
    try:
      return self._params().get(key)
    except Exception:
      return None

  def put(self, key: str, value, *, block: bool = False) -> None:
    params = self._params()
    (params.put if block else params.put_nonblocking)(key, value)

  def remove(self, key: str) -> None:
    self._params().remove(key)

  def chestnut_present(self) -> bool:
    return platform.egpu_in_use()

  def camera(self) -> tuple[int, int, int, int]:
    return platform.DEVICE_CAMERA

  def warp_path(self, cam_w: int, cam_h: int, model_w: int, model_h: int) -> Path:
    # never read: the warp is OpenCL (warp_source), there is no pickle
    return ROOT / 'selfdrive' / 'modeld' / 'models' / f'driving_warp_{cam_w}x{cam_h}_tinygrad.pkl'

  def model_root(self) -> Path:
    return Path('/data/media/0/models') if _on_device() else comma_home() / 'models'

  def model_face(self):
    from nagaspilot.runtime.bigmodel_face import model_face
    return model_face(self.fallback_action)

  def in_control(self) -> bool:
    """Is openpilot in control of the car? Asked on modeld's frame thread before every frame; True when unknown."""
    try:
      if self._sm is None:
        import cereal.messaging as messaging
        self._sm = messaging.SubMaster(['carControl', 'selfdriveState'])
      sm = self._sm
      sm.update(0)
      # a SubMaster hands back default (disengaged) messages until the first arrives, and stale ones when a
      # service dies: neither says "not in control", so a swap waits for both services to be seen and alive
      if not all(sm.seen[s] and sm.alive[s] for s in ('carControl', 'selfdriveState')):
        return True
      return bool(sm['selfdriveState'].enabled or sm['carControl'].latActive or sm['carControl'].longActive)
    except Exception:
      return True

  def event(self, name: str, **fields) -> None:
    self.log.event(name, **fields)


def adapter() -> NgpOpenpilot:
  return NgpOpenpilot()


if __name__ == '__main__':
  main()
