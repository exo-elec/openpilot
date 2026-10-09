"""
API client for openpilot.

EOP: Cloud services disabled. This module is kept for compatibility
but all network requests to comma.ai are disabled.
"""


class Api:
  """Stub API client — EOP does not use cloud services."""

  def __init__(self, dongle_id):
    self.dongle_id = dongle_id

  def get(self, *args, **kwargs):
    return None

  def post(self, *args, **kwargs):
    return None

  def request(self, *args, **kwargs):
    return None

  def get_token(self, expiry_hours=1):
    return None


def api_get(endpoint, method='GET', timeout=None, access_token=None, **params):
  """Stub — EOP does not make cloud API requests."""
  return None
