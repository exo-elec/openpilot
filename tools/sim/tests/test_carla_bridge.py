#!/usr/bin/env python3
import subprocess
import time
from subprocess import Popen

from openpilot.tools.sim.tests.test_sim_bridge import TestSimBridgeBase, SIM_DIR
from openpilot.tools.sim.bridge.carla.carla_bridge import CarlaBridge


class TestCarlaBridge(TestSimBridgeBase):
  """
  Tests need CARLA simulator to run.
  """
  carla_process: Popen | None = None

  @classmethod
  def setup_class(cls):
    pass  # Allow running this class

  def setup_method(self):
    super().setup_method()

    # Ensure no stale CARLA container
    subprocess.run("docker rm -f carla_sim", shell=True, stderr=subprocess.PIPE, check=False)
    self.carla_process = subprocess.Popen("./start_carla.sh", cwd=SIM_DIR)

    # Wait for CARLA to start up
    time.sleep(15)

  def create_bridge(self):
    return CarlaBridge(
      dual_camera=True,
      high_quality=False,
      host="127.0.0.1",
      port=2000,
      town="Town04_Opt",
      num_selected_spawn_point=16,
    )

  def teardown_method(self):
    super().teardown_method()

    # Stop CARLA container
    subprocess.run("docker rm -f carla_sim", shell=True, stderr=subprocess.PIPE, check=False)
    if self.carla_process is not None:
      self.carla_process.wait()
