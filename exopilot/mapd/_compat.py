"""Imports that need a built openpilot (cereal, swaglog, hardware paths) behind fallbacks, so the pure map logic runs and tests anywhere."""
import logging
import os

try:
  from openpilot.common.swaglog import cloudlog
except Exception:           # no built cereal (dev PC test, offline tools)
  cloudlog = logging.getLogger('mapd')


def data_root() -> str:
  """EOP10's board data root where defined, else the comma home, else /data."""
  try:
    from openpilot.system.hardware.hw import Paths
    try:
      return str(Paths.eop_data_root())
    except AttributeError:
      return str(Paths.comma_home())
  except Exception:
    return os.environ.get('NAGASPILOT_DATA', '/data')
