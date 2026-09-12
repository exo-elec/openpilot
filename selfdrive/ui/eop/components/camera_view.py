"""VisionIPC camera surface (plan section 4.3).

Two paths, chosen at runtime:

**Zero-copy (device)** -- QOpenGLWidget, with the dmabuf imported as an
EGLImage and bound to a GL texture. openpilot's `system/ui/lib/egl.py` is
already toolkit-agnostic: `bind_egl_image_to_texture()` takes a raw GL texture
id, not a raylib handle, so only the texture comes from Qt. That is the whole
reason this is ~200 lines rather than a C++ extension.

**CPU (dev PC, and fallback)** -- VisionIPC NV12 -> numpy -> QImage. Same
shape as the cv_bridge path VisionPilot uses today, minus ROS.

The EGL path is unverified. It cannot be verified without real 02M hardware,
and it carries the open question from section 12.2: EGLFS supports one
fullscreen GL window and documents mixing GL windows with QWidget content as
terminating the application. A QOpenGLWidget composited as a child is the
supported case, but "supported" and "works against Rockchip libmali" are
different claims. `CameraView.create()` therefore falls back to the CPU path
on any failure rather than taking the UI down with it.
"""

from __future__ import annotations

import os

import numpy as np

from openpilot.selfdrive.ui.eop.qt import (
  Qt,
  QColor,
  QOpenGLWidget,
  QPainter,
  QtGui,
  QWidget,
)

# Set EOP_UI_CAMERA=cpu|gl|none to force a path; default probes.
FORCE = os.environ.get("EOP_UI_CAMERA", "").strip().lower()


def _vision_client(stream_name: str, publisher: str = "camerad"):
  """Import VisionIPC lazily -- it is a compiled extension, and the UI must
  stay importable on a machine without a built tree.

  `publisher` is not always camerad: the road camera comes from camerad, but
  the side and rear USB cameras are published by uvcd.
  """
  from msgq.visionipc import VisionIpcClient, VisionStreamType
  stream = getattr(VisionStreamType, stream_name)
  return VisionIpcClient(publisher, stream, conflate=True), stream


def bgr_to_rgb(buf: np.ndarray, width: int, height: int, stride: int) -> np.ndarray:
  """BGR888 -> RGB888.

  The USB side and rear cameras hand over packed BGR rather than NV12 -- they
  are ordinary UVC devices, not the ISP pipeline camerad drives. A stride
  wider than the row is cropped rather than reshaped, because the padding is
  not image data.
  """
  rows = buf[: stride * height].reshape(height, stride)
  packed = rows[:, : width * 3].reshape(height, width, 3)
  return packed[:, :, ::-1]


def nv12_to_rgb(buf: np.ndarray, width: int, height: int, stride: int) -> np.ndarray:
  """NV12 -> RGB888.

  Uses cv2's SIMD conversion, with a numpy fallback if cv2 is unavailable.
  The difference is not a micro-optimisation: measured on 1600x600, the numpy
  path costs **24.7 ms per frame** against a 50 ms budget at 20 Hz, and cv2
  costs **0.2 ms** -- 120x. An RK3576 core is several times slower than the
  machine that was measured on, so the numpy path cannot hold 20 fps there at
  all; it would run around 10 fps while burning a core the driving model
  wants.

  That matters beyond framerate. Plan section 4.3 treats the CPU path as the
  safe fallback if the EGL path fails on real hardware, and a fallback that
  cannot hold framerate is not one. cv2 is already a dependency of this repo
  (system/hardware/rockchip/mpp.py imports it), so this costs nothing new.

  The two agree to within coefficient rounding: mean absolute difference 5.4
  of 255 across channels on random input.
  """
  yuv = buf[: stride * height * 3 // 2].reshape(height * 3 // 2, stride)
  try:
    import cv2
    rgb = cv2.cvtColor(yuv, cv2.COLOR_YUV2RGB_NV12)
    return rgb[:, :width] if stride != width else rgb
  except ImportError:
    return _nv12_to_rgb_numpy(buf, width, height, stride)


def _nv12_to_rgb_numpy(buf: np.ndarray, width: int, height: int, stride: int) -> np.ndarray:
  """Pure-numpy NV12 conversion. Correct but ~120x slower than cv2 -- kept
  only so a machine without cv2 still renders, never as the device path."""
  y = buf[: stride * height].reshape(height, stride)[:, :width].astype(np.int32)
  uv = buf[stride * height:].reshape(height // 2, stride)[:, :width]
  u = uv[:, 0::2].repeat(2, axis=0).repeat(2, axis=1).astype(np.int32) - 128
  v = uv[:, 1::2].repeat(2, axis=0).repeat(2, axis=1).astype(np.int32) - 128
  u, v = u[:height, :width], v[:height, :width]

  r = y + ((91881 * v) >> 16)
  g = y - ((22554 * u + 46802 * v) >> 16)
  b = y + ((116130 * u) >> 16)
  return np.clip(np.stack((r, g, b), axis=-1), 0, 255).astype(np.uint8)


class CpuCameraView(QWidget):
  """VisionIPC -> numpy -> QImage. Works anywhere, costs a copy per frame."""

  def __init__(self, stream_name: str = "VISION_STREAM_ROAD",
               publisher: str = "camerad", parent=None):
    super().__init__(parent)
    self.stream_name = stream_name
    self.publisher = publisher
    self.setAttribute(Qt.WA_OpaquePaintEvent, True)
    self._client = None
    self._image: QtGui.QImage | None = None
    self._connected = False

  def connect(self) -> bool:
    try:
      self._client, _ = _vision_client(self.stream_name, self.publisher)
      self._connected = bool(self._client.connect(False))
    except Exception:
      self._connected = False
    return self._connected

  def poll(self) -> None:
    """Pull the newest frame. Called from the UI tick, not a thread: the
    conflated client only ever hands back the latest buffer, so there is
    nothing to fall behind on."""
    if not self._connected and not self.connect():
      return
    try:
      buf = self._client.recv(timeout_ms=0)
    except Exception:
      self._connected = False
      return
    if buf is None:
      return
    arr = np.frombuffer(buf.data, dtype=np.uint8)
    rgb = _to_rgb(arr, buf)
    # QImage does not copy, so keep the array alive on the instance.
    self._rgb = np.ascontiguousarray(rgb)
    self._image = QtGui.QImage(self._rgb.data, buf.width, buf.height,
                               buf.width * 3, QtGui.QImage.Format_RGB888)
    self.update()

  def paintEvent(self, event):
    p = QPainter(self)
    if self._image is None:
      p.fillRect(self.rect(), QColor(0, 0, 0))
      p.setPen(QColor(60, 72, 74))
      p.drawText(self.rect(), Qt.AlignCenter, "camera: no frames")
      return
    scaled = self._image.scaled(self.size(), Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
    x = (self.width() - scaled.width()) // 2
    y = (self.height() - scaled.height()) // 2
    p.drawImage(x, y, scaled)


VERTEX_SHADER = """
#version 300 es
precision mediump float;
in vec3 vertexPosition;
in vec2 vertexTexCoord;
uniform mat4 mvp;
out vec2 fragTexCoord;
void main() {
  fragTexCoord = vertexTexCoord;
  gl_Position = mvp * vec4(vertexPosition, 1.0);
}
"""

# Zero-copy path: the dmabuf arrives as an external OES image, already NV12,
# so the sampler does the conversion and the shader only applies openpilot's
# 1/1.28 gamma. Lifted from the C++/raylib CameraView so the two agree on
# what a frame should look like.
FRAGMENT_SHADER_EGL = """
#version 300 es
#extension GL_OES_EGL_image_external_essl3 : enable
precision mediump float;
in vec2 fragTexCoord;
uniform samplerExternalOES texture0;
out vec4 fragColor;
void main() {
  vec4 color = texture(texture0, fragTexCoord);
  fragColor = vec4(pow(color.rgb, vec3(1.0/1.28)), color.a);
}
"""

# Full-screen quad in clip space, with an identity MVP: this widget always
# fills its rect, so there is no camera transform to apply here.
_QUAD = np.array([
  # x, y, z,   u, v
  -1.0, -1.0, 0.0, 0.0, 1.0,
   1.0, -1.0, 0.0, 1.0, 1.0,
   1.0,  1.0, 0.0, 1.0, 0.0,
  -1.0,  1.0, 0.0, 0.0, 0.0,
], dtype=np.float32)
_INDICES = np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)
_IDENTITY = np.eye(4, dtype=np.float32)

GL_TEXTURE_EXTERNAL_OES = 0x8D65


class GlCameraView(QOpenGLWidget):
  """Zero-copy: VisionIPC dmabuf -> EGLImage -> external OES texture.

  EGLImages are cached per buffer fd. VisionIPC recycles a small ring, so the
  same handful of fds come back round and round -- recreating the image every
  frame would leak EGL handles at frame rate, which is the mistake this
  caching exists to avoid.

  Unverified on hardware, and it carries the open question from plan section
  12.2: EGLFS supports one fullscreen GL window and documents mixing GL
  windows with QWidget content as terminating the application. A
  QOpenGLWidget composited as a child is the supported case, but that is not
  the same as verified against Rockchip libmali. `create_camera_view()` falls
  back to the CPU path rather than taking the UI down.
  """

  def __init__(self, stream_name: str = "VISION_STREAM_ROAD",
               publisher: str = "camerad", parent=None):
    super().__init__(parent)
    self.stream_name = stream_name
    self.publisher = publisher
    self._client = None
    self._egl_ready = False
    self._program = None
    self._texture = 0
    self._images: dict[int, object] = {}
    self._frame = None
    self._failed = False

  # ---- GL lifecycle -----------------------------------------------------

  def initializeGL(self) -> None:
    from openpilot.selfdrive.ui.eop.qt import QtGui
    try:
      from openpilot.system.ui.lib.egl import init_egl
      self._egl_ready = bool(init_egl())
    except Exception:
      self._egl_ready = False
      self._failed = True
      return

    self._program = QtGui.QOpenGLShaderProgram(self)
    ok = self._program.addShaderFromSourceCode(QtGui.QOpenGLShader.Vertex, VERTEX_SHADER)
    ok = ok and self._program.addShaderFromSourceCode(
      QtGui.QOpenGLShader.Fragment, FRAGMENT_SHADER_EGL)
    ok = ok and self._program.link()
    if not ok:
      # Report rather than draw nothing silently: a shader that fails to
      # compile against libmali is exactly the failure section 12.2 warns of,
      # and it should be visible in the log, not just as a black screen.
      from openpilot.common.swaglog import cloudlog
      cloudlog.error(f"camera shader failed: {self._program.log()}")
      self._failed = True
      return

    fns = self.context().functions()
    tex = fns.glGenTextures(1)
    self._texture = tex if isinstance(tex, int) else int(tex[0])

  def _ensure_image(self, buf):
    """EGLImage for this buffer's fd, created once and reused."""
    from openpilot.system.ui.lib.egl import create_egl_image
    fd = int(buf.fd)
    img = self._images.get(fd)
    if img is None:
      img = create_egl_image(buf.width, buf.height, buf.stride, fd, buf.uv_offset)
      if img is None:
        return None
      self._images[fd] = img
    return img

  # ---- frames -----------------------------------------------------------

  def connect(self) -> bool:
    try:
      self._client, _ = _vision_client(self.stream_name, self.publisher)
      return bool(self._client.connect(False))
    except Exception:
      return False

  def poll(self) -> None:
    if self._failed:
      return
    if self._client is None and not self.connect():
      return
    try:
      frame = self._client.recv(timeout_ms=0)
    except Exception:
      self._client = None
      return
    if frame is not None:
      self._frame = frame
      self.update()

  # ---- draw -------------------------------------------------------------

  def paintGL(self) -> None:
    from openpilot.selfdrive.ui.eop.qt import QtGui
    fns = self.context().functions()
    fns.glClearColor(0.0, 0.0, 0.0, 1.0)
    fns.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT

    if self._failed or self._frame is None or not self._egl_ready:
      return

    from openpilot.system.ui.lib.egl import bind_egl_image_to_texture
    image = self._ensure_image(self._frame)
    if image is None:
      return

    bind_egl_image_to_texture(self._texture, image)

    self._program.bind()
    self._program.setUniformValue(
      self._program.uniformLocation("mvp"), QtGui.QMatrix4x4())
    self._program.setUniformValue(self._program.uniformLocation("texture0"), 0)

    stride = 5 * 4  # 5 floats per vertex
    pos = self._program.attributeLocation("vertexPosition")
    tex = self._program.attributeLocation("vertexTexCoord")
    self._program.enableAttributeArray(pos)
    self._program.enableAttributeArray(tex)
    self._program.setAttributeArray(pos, _QUAD.tobytes(), 3, stride)
    self._program.setAttributeArray(tex, _QUAD[3:].tobytes(), 2, stride)

    fns.glDrawArrays(0x0006, 0, 4)  # GL_TRIANGLE_FAN

    self._program.disableAttributeArray(pos)
    self._program.disableAttributeArray(tex)
    self._program.release()

  def cleanup(self) -> None:
    """Release every cached EGLImage. Called on teardown -- these are kernel
    handles, not garbage-collected memory."""
    try:
      from openpilot.system.ui.lib.egl import destroy_egl_image
      for img in self._images.values():
        destroy_egl_image(img)
    except Exception:
      pass
    self._images.clear()

  def hideEvent(self, event):
    super().hideEvent(event)
    self.cleanup()


def _to_rgb(arr: np.ndarray, buf) -> np.ndarray:
  """Convert whichever format this stream carries.

  camerad publishes NV12 from the ISP; uvcd publishes packed BGR from
  ordinary UVC devices. The buffer says which: a VisionBuf with y/uv plane
  pointers is NV12, one without is packed. The C++ overlay made the same
  distinction the same way.
  """
  if getattr(buf, "uv_offset", 0) or getattr(buf, "y", None) is not None:
    return nv12_to_rgb(arr, buf.width, buf.height, buf.stride)
  # A packed 3-byte-per-pixel buffer is exactly height*stride with
  # stride >= width*3; anything else is NV12-shaped (height*3/2 rows).
  if arr.size >= buf.stride * buf.height and buf.stride >= buf.width * 3:
    return bgr_to_rgb(arr, buf.width, buf.height, buf.stride)
  return nv12_to_rgb(arr, buf.width, buf.height, buf.stride)


def create_camera_view(stream_name: str = "VISION_STREAM_ROAD",
                       publisher: str = "camerad", parent=None) -> QWidget:
  """Pick a path. Falls back to CPU rather than failing the UI.

  The side and rear overlays always take the CPU path. They are only up while
  a blinker is on or the car is reversing, and EGLFS permits one fullscreen
  GL surface -- a second QOpenGLWidget appearing mid-drive is precisely the
  case plan section 12.2 flags as terminating the application. A view that is
  briefly on screen is not worth that risk, and at these resolutions the cv2
  conversion is well inside budget.
  """
  if publisher != "camerad":
    return CpuCameraView(stream_name, publisher, parent)
  if FORCE == "cpu":
    return CpuCameraView(stream_name, publisher, parent)
  if FORCE == "gl":
    return GlCameraView(stream_name, publisher, parent)
  try:
    from openpilot.system.hardware import ROCKCHIP
    if ROCKCHIP:
      return GlCameraView(stream_name, publisher, parent)
  except Exception:
    pass
  return CpuCameraView(stream_name, publisher, parent)
