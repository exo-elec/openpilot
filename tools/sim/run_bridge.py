#!/usr/bin/env python3
import argparse
import os

from typing import Any
from multiprocessing import Queue

from openpilot.tools.sim.bridge.carla.carla_bridge import CarlaBridge


def parse_args(add_args=None):
  parser = argparse.ArgumentParser(description='Bridge between the simulator and openpilot.')
  parser.add_argument('--simulator', type=str, default='carla', choices=['carla', 'metadrive'],
                      help='Simulator backend (default: carla)')
  parser.add_argument('--joystick', action='store_true')
  parser.add_argument('--high_quality', action='store_true')
  parser.add_argument('--dual_camera', action='store_true')
  parser.add_argument('--tele_camera', action='store_true', help='Enable tele road camera (unsupported on ExoPilot 01M, no-op)')
  parser.add_argument('--stereo_camera', action='store_true', help='Enable stereo cameras')
  parser.add_argument('--side_camera', action='store_true', help='Enable side cameras (blind spot)')

  # CARLA specific arguments
  parser.add_argument('--town', type=str, default='Town04_Opt')
  parser.add_argument('--spawn_point', dest='num_selected_spawn_point', type=int, default=16)
  parser.add_argument('--host', dest='host', type=str, default=os.environ.get("CARLA_HOST", '127.0.0.1'))
  parser.add_argument('--port', dest='port', type=int, default=int(os.environ.get("CARLA_PORT", "2000")))
  parser.add_argument('--traffic', dest='enable_traffic', action='store_true',
                      help='Enable NPC traffic via Traffic Manager')
  parser.add_argument('--legacy_control', dest='ackermann', action='store_false',
                      help='Use legacy VehicleControl instead of Ackermann control')

  return parser.parse_args(add_args)


def create_bridge(args):
  queue: Any = Queue()

  if args.simulator == 'carla':
    simulator_bridge = CarlaBridge(
      dual_camera=args.dual_camera,
      high_quality=args.high_quality,
      tele_camera=args.tele_camera,
      stereo_camera=args.stereo_camera,
      host=args.host,
      port=args.port,
      town=args.town,
      num_selected_spawn_point=args.num_selected_spawn_point,
      enable_traffic=args.enable_traffic,
      ackermann=args.ackermann,
    )
  elif args.simulator == 'metadrive':
    from openpilot.tools.sim.bridge.metadrive.metadrive_bridge import MetaDriveBridge
    simulator_bridge = MetaDriveBridge(
      dual_camera=args.dual_camera,
      high_quality=args.high_quality,
    )
  else:
    raise ValueError(f"Unknown simulator: {args.simulator}")

  simulator_process = simulator_bridge.run(queue)

  return queue, simulator_process, simulator_bridge


def main():
  args = parse_args()
  queue, simulator_process, simulator_bridge = create_bridge(args)

  if args.joystick:
    # start input poll for joystick
    from openpilot.tools.sim.lib.manual_ctrl import wheel_poll_thread

    wheel_poll_thread(queue)
  else:
    # start input poll for keyboard
    from openpilot.tools.sim.lib.keyboard_ctrl import keyboard_poll_thread

    keyboard_poll_thread(queue)

  simulator_bridge.shutdown()

  simulator_process.join()


if __name__ == "__main__":
  main()
