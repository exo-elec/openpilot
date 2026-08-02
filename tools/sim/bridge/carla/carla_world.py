import math
import numpy as np

from openpilot.common.params import Params
from openpilot.system.socketd.vehicle.tesla.values import VEHICLE
from openpilot.tools.sim.lib.common import SimulatorState, vec3
from openpilot.tools.sim.lib.camera_sim import W, H, STEREO_W, STEREO_H
from openpilot.tools.sim.bridge.common import World


class CarlaWorld(World):
  """High-fidelity CARLA world for EOP/openpilot simulation.

  Features:
  - Ackermann steering control (realistic vehicle dynamics)
  - Realistic sensor noise (IMU, GNSS)
  - Multiple camera setup (road, wide_road, tele_road)
  - Optional Traffic Manager NPC traffic
  - Weather presets
  - Vehicle-type-aware physics tuning from EOPVehicleType param
  """

  # CARLA blueprint mapping for generic size categories
  # Fallback chain: preferred → alternative → tesla.model3 (safe default)
  BLUEPRINT_MAP = {
    "HATCH_A":  ["vehicle.nissan.micra", "vehicle.citroen.c3", "vehicle.tesla.model3"],
    "HATCH_B":  ["vehicle.mini.cooperst", "vehicle.seat.leon", "vehicle.toyota.prius", "vehicle.tesla.model3"],
    "SEDAN_B":  ["vehicle.toyota.prius", "vehicle.citroen.c3", "vehicle.tesla.model3"],
    "SUV_B":    ["vehicle.audi.a2", "vehicle.mini.cooperst", "vehicle.tesla.model3"],
    "SEDAN_C":  ["vehicle.chevrolet.impala", "vehicle.lincoln.mkz2017", "vehicle.tesla.model3"],
    "SUV_C":    ["vehicle.jeep.wrangler_rubicon", "vehicle.audi.a2", "vehicle.tesla.model3"],
    "SEDAN_D":  ["vehicle.tesla.model3", "vehicle.lincoln.mkz2017", "vehicle.chevrolet.impala"],
    "SUV_D":    ["vehicle.nissan.patrol", "vehicle.jeep.wrangler_rubicon", "vehicle.tesla.model3"],
    "SUV_E":    ["vehicle.bmw.grandtourer", "vehicle.nissan.patrol", "vehicle.tesla.model3"],
    "MPV":      ["vehicle.volkswagen.t2", "vehicle.bmw.grandtourer", "vehicle.tesla.model3"],
  }

  def __init__(self, client, high_quality, dual_camera, tele_camera,
               num_selected_spawn_point, town, enable_traffic=False,
               weather=None, ackermann=True, stereo_camera=False):
    super().__init__(dual_camera, stereo_camera=stereo_camera)
    import carla

    self.ackermann = ackermann
    self.high_quality = high_quality

    low_quality_layers = carla.MapLayer(carla.MapLayer.Ground | carla.MapLayer.Walls | carla.MapLayer.Decals)
    layers = carla.MapLayer.All if high_quality else low_quality_layers

    world = client.load_world(town, map_layers=layers)

    settings = world.get_settings()
    settings.fixed_delta_seconds = 0.01
    settings.synchronous_mode = True
    world.apply_settings(settings)

    # Weather
    if weather is not None:
      world.set_weather(weather)
    else:
      world.set_weather(carla.WeatherParameters.ClearSunset)

    self.world = world
    world_map = world.get_map()

    blueprint_library = world.get_blueprint_library()

    # Read params
    self.params = Params()
    sim_platform = self.params.get("EOPSimPlatform") or "rk3588"

    # Hardware geometry (ExoPilot 01M / RK3588)
    self.stereo_baseline = 0.08  # 80mm
    self.wide_road_fov = 120
    self.road_fov = 40
    # Front camera Y offsets from vehicle centerline (matching EOP camera bar):
    # road @ -40mm, wide_road @ +40mm (stereo pair shares these positions)
    self.road_y = -0.04
    self.wide_road_y = 0.04
    self.has_side = True  # ExoPilot 01M has side cameras via USB hub

    # Read vehicle type from params and load matching platform config
    vehicle_type_name = self.params.get("EOPVehicleType") or "SUV_C"
    vehicle_type_name = vehicle_type_name.upper()
    if not hasattr(VEHICLE, vehicle_type_name):
      print(f"WARNING: Unknown EOPVehicleType '{vehicle_type_name}', falling back to SUV_C")
      vehicle_type_name = "SUV_C"

    self.platform = getattr(VEHICLE, vehicle_type_name)
    _info = ("CarlaWorld: sim_platform=" + sim_platform + ", vehicle=" + self.platform.name
             + ", mass=" + str(self.platform.mass) + "kg, stereo_baseline="
             + str(int(self.stereo_baseline * 1000)) + "mm")
    print(_info)

    # Spawn appropriate CARLA blueprint
    vehicle_bp = self._select_blueprint(blueprint_library, vehicle_type_name)
    vehicle_bp.set_attribute('role_name', 'hero')

    spawn_points = world_map.get_spawn_points()
    assert len(spawn_points) > num_selected_spawn_point, \
      f'''No spawn point {num_selected_spawn_point}, try a value between 0 and {len(spawn_points)} for this town.'''
    self.spawn_point = spawn_points[num_selected_spawn_point]
    self.vehicle = world.spawn_actor(vehicle_bp, self.spawn_point)

    self.max_steer_angle = 35.0
    self.steer_ratio = self.platform.steer_ratio

    # Apply platform-specific physics
    self._setup_physics()

    self.carla_objects = []

    # Camera mount position (relative to vehicle center, matching EOP camera bar)
    # EOP cameras are mounted on the windshield ~0.8m forward, 1.13m height
    cam_transform = carla.Transform(carla.Location(x=0.8, z=1.13))

    def create_camera(fov, callback, name="camera", extra_attrs=None, width=W, height=H, transform=None):
      blueprint = blueprint_library.find('sensor.camera.rgb')
      blueprint.set_attribute('image_size_x', str(width))
      blueprint.set_attribute('image_size_y', str(height))
      blueprint.set_attribute('fov', str(fov))
      blueprint.set_attribute('sensor_tick', str(1/20))

      # Realistic camera settings for automotive vision
      if not high_quality:
        blueprint.set_attribute('enable_postprocess_effects', 'False')
      else:
        blueprint.set_attribute('enable_postprocess_effects', 'True')
        blueprint.set_attribute('motion_blur_intensity', '0.2')
        blueprint.set_attribute('motion_blur_max_distortion', '0.1')
        blueprint.set_attribute('shutter_speed', '200.0')
        blueprint.set_attribute('iso', '100.0')
        blueprint.set_attribute('gamma', '2.2')

      if extra_attrs:
        for k, v in extra_attrs.items():
          blueprint.set_attribute(str(k), str(v))

      t = transform if transform is not None else cam_transform
      camera = world.spawn_actor(blueprint, t, attach_to=self.vehicle)
      camera.listen(callback)
      return camera

    # Road camera: narrow, primary perception
    road_transform = carla.Transform(carla.Location(x=0.8, y=self.road_y, z=1.13))
    self.road_camera = create_camera(fov=self.road_fov, callback=self.cam_callback_road, name="road", transform=road_transform)

    # Wide road camera: wide-angle cross-traffic (FOV varies by platform)
    self.road_wide_camera = None
    if dual_camera:
      wide_transform = carla.Transform(carla.Location(x=0.8, y=self.wide_road_y, z=1.13))
      self.road_wide_camera = create_camera(fov=self.wide_road_fov, callback=self.cam_callback_wide_road, name="wide_road", transform=wide_transform)

    # ExoPilot 01M (RK3588) has no tele camera — that's an ExoPilot 02M feature
    self.tele_camera = None

    # Stereo cameras: SDR mode, platform-specific baseline
    self.stereo_left_camera = None
    self.stereo_right_camera = None
    if stereo_camera:
      stereo_left_transform = carla.Transform(carla.Location(x=0.8, y=-self.stereo_baseline/2, z=1.13))
      stereo_right_transform = carla.Transform(carla.Location(x=0.8, y=+self.stereo_baseline/2, z=1.13))
      self.stereo_left_camera = create_camera(
        fov=80, callback=self.cam_callback_stereo_left, name="stereo_left",
        width=STEREO_W, height=STEREO_H, transform=stereo_left_transform)
      self.stereo_right_camera = create_camera(
        fov=80, callback=self.cam_callback_stereo_right, name="stereo_right",
        width=STEREO_W, height=STEREO_H, transform=stereo_right_transform)

    # Side cameras (UVC, hood fender under side mirrors, pointing backward) — ExoPilot 01M & 02
    self.side_left_camera = None
    self.side_right_camera = None
    if self.has_side and self.params.get_bool("EOPSideCamerasEnabled"):
      side_w, side_h = 1280, 720
      # Side cameras: rear-pointing, yawed 30° outward from straight back
      # (parallel to ground — no pitch tilt). Left cam points back+left,
      # right cam points back+right. This captures adjacent lanes, not just
      # the road surface directly behind the vehicle.
      side_left_transform = carla.Transform(
        carla.Location(x=0.7, y=-0.85, z=0.75),
        carla.Rotation(yaw=150.0))
      self.side_left_camera = create_camera(
        fov=120, callback=self.cam_callback_side_left, name="side_left",
        width=side_w, height=side_h, transform=side_left_transform)

      side_right_transform = carla.Transform(
        carla.Location(x=0.7, y=0.85, z=0.75),
        carla.Rotation(yaw=-150.0))
      self.side_right_camera = create_camera(
        fov=120, callback=self.cam_callback_side_right, name="side_right",
        width=side_w, height=side_h, transform=side_right_transform)

    # IMU with realistic noise (simulates MEMS IMU like LSM6DS3)
    imu_bp = blueprint_library.find('sensor.other.imu')
    imu_bp.set_attribute('sensor_tick', '0.01')
    # Add noise to simulate real IMU
    imu_bp.set_attribute('noise_accel_stddev_x', '0.05')
    imu_bp.set_attribute('noise_accel_stddev_y', '0.05')
    imu_bp.set_attribute('noise_accel_stddev_z', '0.05')
    imu_bp.set_attribute('noise_gyro_stddev_x', '0.005')
    imu_bp.set_attribute('noise_gyro_stddev_y', '0.005')
    imu_bp.set_attribute('noise_gyro_stddev_z', '0.005')
    self.imu = world.spawn_actor(imu_bp, cam_transform, attach_to=self.vehicle)

    # GNSS with realistic noise (simulates ublox M8N)
    gps_bp = blueprint_library.find('sensor.other.gnss')
    gps_bp.set_attribute('sensor_tick', '0.05')  # 20Hz like ublox
    gps_bp.set_attribute('noise_alt_stddev', '0.5')
    gps_bp.set_attribute('noise_lat_stddev', '2.0')
    gps_bp.set_attribute('noise_lon_stddev', '2.0')
    self.gps = world.spawn_actor(gps_bp, cam_transform, attach_to=self.vehicle)
    self.params.put_bool("UbloxAvailable", True)

    self.carla_objects = [self.imu, self.gps, self.road_camera, self.road_wide_camera, self.tele_camera,
                          self.stereo_left_camera, self.stereo_right_camera,
                          self.side_left_camera, self.side_right_camera, self.vehicle]

    # Simulated side-camera perception (publishes sideDetections for bsd.py)
    self.simulated_sided = None
    self._sided_thread = None
    if self.has_side and self.params.get_bool("EOPSideCamerasEnabled"):
      from openpilot.tools.sim.lib.simulated_sided import SimulatedSideD
      import functools
      import threading
      self.simulated_sided = SimulatedSideD()
      if self.simulated_sided.enabled:
        self._sided_exit = threading.Event()
        self._sided_thread = threading.Thread(
          target=self._sided_rk_loop,
          args=(functools.partial(self.simulated_sided.update, self.world, self.vehicle), 20, self._sided_exit))
        self._sided_thread.start()

    # Traffic light publisher (for TLSC testing)
    self.traffic_light_publisher = None
    self._tl_thread = None
    if self.params.get_bool("EOPTLSCEnabled"):
      from openpilot.tools.sim.lib.traffic_light_publisher import TrafficLightPublisher
      import functools
      import threading
      self.traffic_light_publisher = TrafficLightPublisher()
      if self.traffic_light_publisher.enabled:
        self._tl_exit = threading.Event()
        self._tl_thread = threading.Thread(
          target=self._sided_rk_loop,
          args=(functools.partial(self.traffic_light_publisher.update, self.world, self.vehicle), 10, self._tl_exit))
        self._tl_thread.start()

    # Scenario spawner (Autoware-style feature testing)
    self.scenario_spawner = None
    from openpilot.tools.sim.lib.scenario_spawner import ScenarioSpawner
    self.scenario_spawner = ScenarioSpawner()
    if self.scenario_spawner.active:
      self.scenario_spawner.spawn(self.world, blueprint_library, self.vehicle)

    # Weather controller (dynamic environment testing)
    from openpilot.tools.sim.lib.weather_controller import WeatherController
    self.weather_controller = WeatherController()
    self.weather_controller.update(self.world)

    # Traffic Manager for NPC vehicles
    self.tm = None
    if enable_traffic:
      self.tm = client.get_trafficmanager(8000)
      self.tm.set_synchronous_mode(True)
      self.tm.set_random_device_seed(0)
      # Spawn some traffic
      self._spawn_traffic(blueprint_library, spawn_points, num_vehicles=30)

    # Control state
    self.vc = carla.VehicleControl(throttle=0, steer=0, brake=0, reverse=False)
    self.ackermann_control = carla.VehicleAckermannControl(steer=0.0, speed=0.0, acceleration=0.0)

  def _select_blueprint(self, blueprint_library, vehicle_type_name):
    """Select best-matching CARLA blueprint for vehicle type."""
    candidates = self.BLUEPRINT_MAP.get(vehicle_type_name, ["vehicle.tesla.model3"])
    for bp_name in candidates:
      bps = blueprint_library.filter(bp_name)
      if len(bps) > 0:
        return bps[0]
    # Ultimate fallback: any 4-wheeled vehicle
    for bp in blueprint_library.filter('vehicle.*'):
      if int(bp.get_attribute('number_of_wheels')) == 4:
        return bp
    raise RuntimeError("No suitable vehicle blueprint found in CARLA")

  def _setup_physics(self):
    """Tune physics to match selected platform config."""
    import carla
    physics = self.vehicle.get_physics_control()
    physics.mass = self.platform.mass
    physics.drag_coefficient = self.platform.drag_coefficient
    physics.center_of_mass = carla.Vector3D(0.0, 0.0, self.platform.center_of_mass_z)

    # Tune wheels for better steering response
    for wheel in physics.wheels:
      wheel.max_steer_angle = self.max_steer_angle

    # Engine/torque curve (simplified EV: flat torque, high RPM)
    physics.torque_curve = [
      carla.Vector2D(0.0, 500.0),
      carla.Vector2D(5000.0, 500.0),
      carla.Vector2D(6000.0, 400.0),
    ]
    physics.max_rpm = 18000.0
    physics.moi = 1.0
    physics.clutch_strength = 10.0
    physics.gear_switch_time = 0.0

    self.vehicle.apply_physics_control(physics)

  def _spawn_traffic(self, blueprint_library, spawn_points, num_vehicles=30):
    """Spawn NPC vehicles using Traffic Manager."""
    # Filter to safe vehicle blueprints
    safe_bps = [bp for bp in blueprint_library.filter('vehicle.*')
                if int(bp.get_attribute('number_of_wheels')) == 4]
    if not safe_bps:
      return

    # Exclude hero spawn point and nearby points
    hero_loc = self.spawn_point.location
    available_spawns = [sp for sp in spawn_points
                        if sp.location.distance(hero_loc) > 20.0]

    max_vehicles = min(num_vehicles, len(available_spawns))
    vehicles = []
    for i, spawn_point in enumerate(available_spawns[:max_vehicles]):
      bp = safe_bps[i % len(safe_bps)]
      vehicle = self.world.try_spawn_actor(bp, spawn_point)
      if vehicle is not None:
        vehicles.append(vehicle)
        vehicle.set_autopilot(True, self.tm.get_port())

    self.carla_objects.extend(vehicles)

  @staticmethod
  def _sided_rk_loop(function, hz, exit_event):
    from openpilot.common.realtime import Ratekeeper
    rk = Ratekeeper(hz, None)
    while not exit_event.is_set():
      function()
      rk.keep_time()

  def close(self, reason: str):
    print(f"CarlaWorld closing: {reason}")
    if self._sided_thread is not None:
      self._sided_exit.set()
      self._sided_thread.join(timeout=1.0)
    if self._tl_thread is not None:
      self._tl_exit.set()
      self._tl_thread.join(timeout=1.0)
    if self.scenario_spawner is not None:
      self.scenario_spawner.cleanup()
    if self.tm is not None:
      self.tm.set_synchronous_mode(False)
    if self.world is not None:
      settings = self.world.get_settings()
      settings.synchronous_mode = False
      self.world.apply_settings(settings)
    for s in self.carla_objects:
      if s is not None:
        try:
          s.destroy()
        except Exception as e:
          print("Failed to destroy carla object", e)

  @staticmethod
  def carla_image_to_rgb(image, width=W, height=H):
    rgb = np.frombuffer(image.raw_data, dtype=np.dtype("uint8"))
    rgb = np.reshape(rgb, (height, width, 4))
    return np.ascontiguousarray(rgb[:, :, [0, 1, 2]])

  def cam_callback_road(self, image):
    self.road_image = self.carla_image_to_rgb(image)
    self.image_lock.release()

  def cam_callback_wide_road(self, image):
    self.wide_road_image = self.carla_image_to_rgb(image)

  def cam_callback_stereo_left(self, image):
    self.stereo_left_image = self.carla_image_to_rgb(image, width=STEREO_W, height=STEREO_H)

  def cam_callback_stereo_right(self, image):
    self.stereo_right_image = self.carla_image_to_rgb(image, width=STEREO_W, height=STEREO_H)

  def cam_callback_side_left(self, image):
    self.side_left_image = self.carla_image_to_rgb(image, width=1280, height=720)

  def cam_callback_side_right(self, image):
    self.side_right_image = self.carla_image_to_rgb(image, width=1280, height=720)

  def apply_controls(self, steer_angle, throttle_out, brake_out):
    """Apply controls from openpilot to CARLA vehicle.

    steer_angle: desired steering angle in degrees (at wheels)
    throttle_out: 0.0-1.0
    brake_out: 0.0-1.0
    """
    if self.ackermann:
      # Use Ackermann control for realistic steering response
      # steer_angle is already in degrees at the wheels
      self.ackermann_control.steer = math.radians(steer_angle)

      # Convert throttle/brake to speed target + acceleration
      current_speed = self._get_forward_speed()
      if brake_out > 0:
        self.ackermann_control.speed = max(0.0, current_speed - 5.0)
        self.ackermann_control.acceleration = -4.0 * brake_out
      else:
        self.ackermann_control.speed = current_speed + 5.0 * throttle_out
        self.ackermann_control.acceleration = 2.0 * throttle_out

      self.vehicle.apply_ackermann_control(self.ackermann_control)
    else:
      # Legacy VehicleControl mode
      self.vc.throttle = throttle_out
      steer_carla = steer_angle * -1 / (self.max_steer_angle * self.steer_ratio)
      steer_carla = np.clip(steer_carla, -1, 1)
      self.vc.steer = steer_carla
      self.vc.brake = brake_out
      self.vehicle.apply_control(self.vc)

  def _get_forward_speed(self):
    """Get vehicle forward speed in m/s."""
    vel = self.vehicle.get_velocity()
    transform = self.vehicle.get_transform()
    yaw = math.radians(transform.rotation.yaw)
    # Project velocity onto forward vector
    forward = np.array([math.cos(yaw), math.sin(yaw)])
    velocity_2d = np.array([vel.x, vel.y])
    return float(np.dot(velocity_2d, forward))

  def read_state(self):
    pass

  def read_sensors(self, simulator_state: SimulatorState):
    simulator_state.imu.bearing = self.imu.get_transform().rotation.yaw

    # Read IMU with noise (CARLA applies noise automatically if configured)
    acc = self.imu.get_acceleration()
    simulator_state.imu.accelerometer = vec3(acc.x, acc.y, acc.z)

    gyro = self.imu.get_angular_velocity()
    simulator_state.imu.gyroscope = vec3(gyro.x, gyro.y, gyro.z)

    # GPS with noise
    loc = self.vehicle.get_location()
    simulator_state.gps.from_xy([loc.x, loc.y])

    # Velocity
    simulator_state.velocity = self.vehicle.get_velocity()
    simulator_state.valid = True

    # Steering angle: read actual wheel steer from physics
    physics = self.vehicle.get_physics_control()
    if physics.wheels:
      # Use transform-based approximation or control feedback
      simulator_state.steering_angle = self.ackermann_control.steer * 180 / math.pi if self.ackermann else self.vc.steer * self.max_steer_angle
    else:
      simulator_state.steering_angle = 0.0

  def read_cameras(self):
    pass  # cameras are read within callbacks for carla

  def tick(self):
    self.world.tick()
    if self.scenario_spawner is not None:
      self.scenario_spawner.tick()
    if hasattr(self, 'weather_controller'):
      self.weather_controller.update(self.world)

  def reset(self):
    import carla
    self.vehicle.set_transform(self.spawn_point)
    self.vehicle.set_target_velocity(carla.Vector3D())
    self.vehicle.set_target_angular_velocity(carla.Vector3D())
    if self.ackermann:
      self.ackermann_control = carla.VehicleAckermannControl(steer=0.0, speed=0.0, acceleration=0.0)
      self.vehicle.apply_ackermann_control(self.ackermann_control)
    else:
      self.vc = carla.VehicleControl(throttle=0, steer=0, brake=0, reverse=False)
      self.vehicle.apply_control(self.vc)
