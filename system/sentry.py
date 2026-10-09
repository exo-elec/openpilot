"""Install exception handler for process crash."""
import sentry_sdk
from enum import Enum

from openpilot.common.swaglog import cloudlog


class SentryProject(Enum):
  # EOP: Sentry disabled — no crash reporting to external services
  SELFDRIVE = ""
  SELFDRIVE_NATIVE = ""  # noqa: PIE796


def report_tombstone(fn: str, message: str, contents: str) -> None:
  cloudlog.error({'tombstone': message})

  with sentry_sdk.configure_scope() as scope:
    scope.set_extra("tombstone_fn", fn)
    scope.set_extra("tombstone", contents)
    sentry_sdk.capture_message(message=message)
    sentry_sdk.flush()


def capture_exception(*args, **kwargs) -> None:
  cloudlog.error("crash", exc_info=kwargs.get('exc_info', 1))

  try:
    sentry_sdk.capture_exception(*args, **kwargs)
    sentry_sdk.flush()  # https://github.com/getsentry/sentry-python/issues/291
  except Exception:
    cloudlog.exception("sentry exception")


def set_tag(key: str, value: str) -> None:
  sentry_sdk.set_tag(key, value)


def init(project: SentryProject) -> bool:
  # EOP: No crash reporting to external services. All telemetry disabled.
  return False
