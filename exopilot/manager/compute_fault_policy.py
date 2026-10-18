"""EOP compute policy: CPU core tasks and the RKNN safety NPU are critical.

GPU, RGA, MPP and camera-tier cards provide optional perception capabilities.
Their failure removes that evidence; it is not a core-inference fault. Normal
vehicle, driver, model-output validity and control deadline guards still apply.
"""


def rknn_fault_reason(*, available: bool, healthy: bool) -> str:
  if not available:
    return 'rknn_unavailable'
  if not healthy:
    return 'rknn_failed'
  return ''


def critical_inference_fault(status) -> bool:
  """Do not turn generic GPU/card failures into an inference disengagement."""
  if not status.enabled:
    return False
  if not status.npuAvailable:
    return True
  return bool(status.fault and status.faultReason in (
    'rknn_unavailable', 'rknn_failed', 'npu_failed', 'cpu_failed', 'cpu_deadline_missed',
  ))


def allow_rknn_mock(machine: str) -> bool:
  """Keep offline x86 test doubles out of ARM/Rockchip runtime readiness."""
  return machine.lower() in ('x86_64', 'amd64', 'i386', 'i686')
