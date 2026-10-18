"""GPU selection and memory policy tests without a target driver."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('gpu_compute_under_test', Path(__file__).parents[1] / 'gpu_compute.py')
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)


class TestGPUCompute(unittest.TestCase):
  def runtime(self, devices):
    platform = SimpleNamespace(get_devices=Mock(return_value=devices))
    cl = SimpleNamespace(device_type=SimpleNamespace(GPU=4), Error=RuntimeError,
                         get_platforms=lambda: [platform], Context=Mock(return_value=object()),
                         CommandQueue=Mock(return_value=Mock()))
    return cl, platform

  def device(self, name='Mali-G610', kind=4):
    return SimpleNamespace(name=name, type=kind, global_mem_size=1024 ** 3, max_mem_alloc_size=64 * 1024 ** 2)

  def test_gpu_only_enumeration_and_queue(self):
    device = self.device()
    cl, platform = self.runtime([device])
    runtime = gpu.GPUCompute(cl_module=cl)
    platform.get_devices.assert_called_once_with(device_type=4)
    self.assertIs(runtime.device, device)
    cl.CommandQueue.assert_called_once_with(runtime.context, device=device, properties=0)

  def test_cpu_device_is_rejected_even_if_icd_returns_it(self):
    cl, _ = self.runtime([self.device('CPU', 2)])
    with self.assertRaises(gpu.GPUUnavailable):
      gpu.GPUCompute(cl_module=cl)
    cl.Context.assert_not_called()

  def test_device_failure_does_not_select_cpu(self):
    cl, _ = self.runtime([self.device()])
    cl.Context.side_effect = RuntimeError('driver error')
    with self.assertRaisesRegex(gpu.GPUUnavailable, 'driver error'):
      gpu.GPUCompute(cl_module=cl)

  def test_memory_budget_and_single_allocation(self):
    cl, _ = self.runtime([self.device()])
    runtime = gpu.GPUCompute(cl_module=cl)
    self.assertEqual(runtime.check_allocations([1024, 2048]), 3072)
    for sizes in ([129 * 1024 ** 2], [65 * 1024 ** 2], [50 * 1024 ** 2] * 3):
      with self.assertRaises(gpu.GPUUnavailable):
        runtime.check_allocations(sizes)
    for sizes in ([0], [-1], [1.5]):
      with self.assertRaises(ValueError):
        runtime.check_allocations(sizes)

  def test_close_is_idempotent_and_rejects_new_work(self):
    cl, _ = self.runtime([self.device()])
    runtime = gpu.GPUCompute(cl_module=cl)
    queue = runtime.queue
    runtime.close()
    runtime.close()
    queue.finish.assert_called_once()
    with self.assertRaises(gpu.GPUUnavailable):
      runtime.check_allocations([16])


if __name__ == '__main__':
  unittest.main()
