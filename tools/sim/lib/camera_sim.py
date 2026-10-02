import numpy as np
import os
import pyopencl as cl
import pyopencl.array as cl_array

from msgq.visionipc import VisionIpcServer, VisionStreamType
from cereal import messaging

from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.tools.sim.lib.common import W, H

# Stereo camera resolution (matches GC4653 typical config)
STEREO_W, STEREO_H = 1280, 720


class CameraSim:
  """Simulates v4l2d + stereod VisionIPC servers."""
  def __init__(self, dual_camera=False, tele_camera=None, stereo_camera=None):
    params = Params()
    # ExoPilot 01M (RK3588) has no tele camera — that's an ExoPilot 02M feature
    self.tele_camera = False
    self.stereo_camera = stereo_camera if stereo_camera is not None else params.get_bool("EOPStereoEnabled")

    pub_types = ['roadCameraState', 'wideRoadCameraState']
    if self.tele_camera:
      pub_types.append('teleRoadCameraState')
    if self.stereo_camera:
      pub_types.extend(['stereoCameraState', 'stereoCameraStateRight'])

    self.pm = messaging.PubMaster(pub_types)

    self.frame_road_id = 0
    self.frame_wide_id = 0
    self.frame_tele_id = 0
    self.frame_stereo_left_id = 0
    self.frame_stereo_right_id = 0

    # V4L2D server: road, wide, tele
    self.vipc_v4l2d = VisionIpcServer("v4l2d")
    self.vipc_v4l2d.create_buffers(VisionStreamType.VISION_STREAM_ROAD, 5, W, H)
    if dual_camera:
      self.vipc_v4l2d.create_buffers(VisionStreamType.VISION_STREAM_WIDE_ROAD, 5, W, H)
    if self.tele_camera:
      self.vipc_v4l2d.create_buffers(VisionStreamType.VISION_STREAM_TELE_ROAD, 5, W, H)
    self.vipc_v4l2d.start_listener()

    # Stereod server: stereo_left, stereo_right
    self.vipc_stereod = None
    if self.stereo_camera:
      self.vipc_stereod = VisionIpcServer("stereod")
      self.vipc_stereod.create_buffers(VisionStreamType.VISION_STREAM_STEREO_LEFT, 5, STEREO_W, STEREO_H)
      self.vipc_stereod.create_buffers(VisionStreamType.VISION_STREAM_STEREO_RIGHT, 5, STEREO_W, STEREO_H)
      self.vipc_stereod.start_listener()

    # OpenCL context for RGB→NV12 (road cameras)
    self.ctx = cl.create_some_context()
    self.queue = cl.CommandQueue(self.ctx)
    cl_arg = f" -DHEIGHT={H} -DWIDTH={W} -DRGB_STRIDE={W * 3} -DUV_WIDTH={W // 2} -DUV_HEIGHT={H // 2} -DRGB_SIZE={W * H} -DCL_DEBUG "

    kernel_fn = os.path.join(BASEDIR, "tools/sim/rgb_to_nv12.cl")
    with open(kernel_fn) as f:
      prg = cl.Program(self.ctx, f.read()).build(cl_arg)
      self.krnl = prg.rgb_to_nv12
    self.Wdiv4 = W // 4 if (W % 4 == 0) else (W + (4 - W % 4)) // 4
    self.Hdiv4 = H // 4 if (H % 4 == 0) else (H + (4 - H % 4)) // 4

    # Stereo OpenCL kernels (same context, separate queue + program)
    self.stereo_queue = None
    if self.stereo_camera:
      self.stereo_queue = cl.CommandQueue(self.ctx)
      stereo_cl_arg = (
        f" -DHEIGHT={STEREO_H} -DWIDTH={STEREO_W} -DRGB_STRIDE={STEREO_W * 3}" +
        f" -DUV_WIDTH={STEREO_W // 2} -DUV_HEIGHT={STEREO_H // 2}" +
        f" -DRGB_SIZE={STEREO_W * STEREO_H} -DCL_DEBUG "
      )
      with open(kernel_fn) as f:
        prg = cl.Program(self.ctx, f.read()).build(stereo_cl_arg)
        self.stereo_krnl = prg.rgb_to_nv12
      self.stereo_Wdiv4 = STEREO_W // 4 if (STEREO_W % 4 == 0) else (STEREO_W + (4 - STEREO_W % 4)) // 4
      self.stereo_Hdiv4 = STEREO_H // 4 if (STEREO_H % 4 == 0) else (STEREO_H + (4 - STEREO_H % 4)) // 4

  def cam_send_yuv_road(self, yuv):
    self._send_yuv(self.vipc_v4l2d, yuv, self.frame_road_id, 'roadCameraState', VisionStreamType.VISION_STREAM_ROAD)
    self.frame_road_id += 1

  def cam_send_yuv_wide_road(self, yuv):
    self._send_yuv(self.vipc_v4l2d, yuv, self.frame_wide_id, 'wideRoadCameraState', VisionStreamType.VISION_STREAM_WIDE_ROAD)
    self.frame_wide_id += 1

  def cam_send_yuv_tele_road(self, yuv):
    self._send_yuv(self.vipc_v4l2d, yuv, self.frame_tele_id, 'teleRoadCameraState', VisionStreamType.VISION_STREAM_TELE_ROAD)
    self.frame_tele_id += 1

  def cam_send_yuv_stereo_left(self, yuv):
    self._send_yuv(self.vipc_stereod, yuv, self.frame_stereo_left_id, 'stereoCameraState', VisionStreamType.VISION_STREAM_STEREO_LEFT)
    self.frame_stereo_left_id += 1

  def cam_send_yuv_stereo_right(self, yuv):
    self._send_yuv(self.vipc_stereod, yuv, self.frame_stereo_right_id, 'stereoCameraStateRight', VisionStreamType.VISION_STREAM_STEREO_RIGHT)
    self.frame_stereo_right_id += 1

  def rgb_to_yuv(self, rgb):
    assert rgb.shape == (H, W, 3), f"{rgb.shape}"
    assert rgb.dtype == np.uint8
    rgb_cl = cl_array.to_device(self.queue, rgb)
    yuv_cl = cl_array.empty_like(rgb_cl)
    self.krnl(self.queue, (self.Wdiv4, self.Hdiv4), None, rgb_cl.data, yuv_cl.data).wait()
    yuv = np.resize(yuv_cl.get(), rgb.size // 2)
    return yuv.data.tobytes()

  def rgb_to_yuv_stereo(self, rgb):
    assert rgb.shape == (STEREO_H, STEREO_W, 3), f"{rgb.shape}"
    assert rgb.dtype == np.uint8
    rgb_cl = cl_array.to_device(self.stereo_queue, rgb)
    yuv_cl = cl_array.empty_like(rgb_cl)
    self.stereo_krnl(self.stereo_queue, (self.stereo_Wdiv4, self.stereo_Hdiv4), None, rgb_cl.data, yuv_cl.data).wait()
    yuv = np.resize(yuv_cl.get(), rgb.size // 2)
    return yuv.data.tobytes()

  def close(self):
    """Release VisionIPC servers and OpenCL contexts."""
    self.vipc_v4l2d = None
    self.vipc_stereod = None
    self.ctx = None
    self.queue = None
    self.stereo_queue = None
    self.krnl = None
    self.stereo_krnl = None

  def _send_yuv(self, vipc_server, yuv, frame_id, pub_type, yuv_type):
    eof = int(frame_id * 0.05 * 1e9)
    vipc_server.send(yuv_type, yuv, frame_id, eof, eof)

    dat = messaging.new_message(pub_type, valid=True)
    msg = {
      "frameId": frame_id,
      "transform": [1.0, 0.0, 0.0,
                    0.0, 1.0, 0.0,
                    0.0, 0.0, 1.0]
    }
    setattr(dat, pub_type, msg)
    self.pm.send(pub_type, dat)
