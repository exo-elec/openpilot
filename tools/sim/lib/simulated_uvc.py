"""Simulated UVC daemon for side cameras in CARLA.

Publishes side_left and side_right frames to VisionIPC "uvcd"
for BSD / lane change assist testing.
"""
from msgq.visionipc import VisionIpcServer, VisionStreamType
from cereal import messaging

from openpilot.common.params import Params

# Side camera resolution (matches uvcd defaults)
SIDE_W, SIDE_H = 1280, 720

STREAM_SIDE_LEFT = VisionStreamType.VISION_STREAM_SIDE_LEFT
STREAM_SIDE_RIGHT = VisionStreamType.VISION_STREAM_SIDE_RIGHT


class SimulatedUvc:
  """Simulates uvcd for side cameras (blind spot detection)."""

  def __init__(self):
    params = Params()
    # Side cameras are available on ExoPilot 01M (RK3588) via USB hub
    self.enabled = params.get_bool("EOPSideCamerasEnabled")

    if not self.enabled:
      return

    self.pm = messaging.PubMaster(['leftCameraState', 'rightCameraState'])
    self.vipc = VisionIpcServer("uvcd")
    self.vipc.create_buffers_with_sizes(
      STREAM_SIDE_LEFT, 4, SIDE_W, SIDE_H,
      size=SIDE_W * SIDE_H * 3,
      stride=SIDE_W * 3,
      uv_offset=0,
    )
    self.vipc.create_buffers_with_sizes(
      STREAM_SIDE_RIGHT, 4, SIDE_W, SIDE_H,
      size=SIDE_W * SIDE_H * 3,
      stride=SIDE_W * 3,
      uv_offset=0,
    )
    self.vipc.start_listener()

    self.frame_side_left_id = 0
    self.frame_side_right_id = 0

  def _send_frame(self, frame_bgr, frame_id, pub_type, stream_type):
    """Send BGR frame to VisionIPC and cereal."""
    eof = int(frame_id * 0.05 * 1e9)
    # VisionIPC expects raw BGR bytes (3 bytes/pixel)
    self.vipc.send(stream_type, frame_bgr.tobytes(), frame_id, eof, eof)

    dat = messaging.new_message(pub_type, valid=True)
    msg = {
      "frameId": frame_id,
      "transform": [1.0, 0.0, 0.0,
                    0.0, 1.0, 0.0,
                    0.0, 0.0, 1.0]
    }
    setattr(dat, pub_type, msg)
    self.pm.send(pub_type, dat)

  def send_side_images(self, world):
    if not self.enabled:
      return

    self._send_frame(world.side_left_image, self.frame_side_left_id, 'leftCameraState', STREAM_SIDE_LEFT)
    self.frame_side_left_id += 1

    self._send_frame(world.side_right_image, self.frame_side_right_id, 'rightCameraState', STREAM_SIDE_RIGHT)
    self.frame_side_right_id += 1

  def close(self):
    """Release VisionIPC and messaging resources."""
    self.vipc_uvcd = None
    self.pm = None
