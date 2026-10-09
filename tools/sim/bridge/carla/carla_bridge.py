from openpilot.tools.sim.bridge.common import SimulatorBridge
from openpilot.tools.sim.bridge.carla.carla_world import CarlaWorld


class CarlaBridge(SimulatorBridge):
  TICKS_PER_FRAME = 5

  def __init__(self, dual_camera, high_quality, tele_camera=None, stereo_camera=None,
               host='127.0.0.1', port=2000,
               town='Town04_Opt', num_selected_spawn_point=16, enable_traffic=False,
               ackermann=True):
    super().__init__(dual_camera, high_quality, tele_camera=tele_camera, stereo_camera=stereo_camera)
    self.host = host
    self.port = port
    self.town = town
    self.num_selected_spawn_point = num_selected_spawn_point
    self.enable_traffic = enable_traffic
    self.ackermann = ackermann

  def spawn_world(self, queue):
    import carla

    client = carla.Client(self.host, self.port)
    client.set_timeout(120)  # map loading takes 30-90s on first run

    return CarlaWorld(client, high_quality=self.high_quality, dual_camera=self.dual_camera,
                      tele_camera=self.tele_camera, stereo_camera=self.stereo_camera,
                      num_selected_spawn_point=self.num_selected_spawn_point, town=self.town,
                      enable_traffic=self.enable_traffic, ackermann=self.ackermann)
