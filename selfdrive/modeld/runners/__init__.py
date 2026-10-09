"""Public driving-runner API; optional inference backends load only on use."""
import importlib

_EXPORTS = {
    "DrivingRunner": "driving_runner",
    "DrivingModelSpec": "driving_runner",
    "DrivingRunnerResult": "driving_runner",
    "RKNNDrivingRunner": "rknn_driving_runner",
    "EgpuDrivingRunner": "egpu_driving_runner",
    "ChestnutDrivingRunner": "egpu_driving_runner",
    "create_driving_runner": "factory",
    "build_driving_specs": "factory",
    "build_egpu_spec": "factory",
    "build_chestnut_spec": "factory",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value
