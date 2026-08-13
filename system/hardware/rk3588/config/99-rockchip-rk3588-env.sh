#!/usr/bin/env bash
# RK3588 Platform Environment Setup
# Source this in /etc/profile.d/ or from openpilot launch script.
#
# Derived from vendor SDK debian/overlay/etc/profile.d/ settings.
# These variables are REQUIRED for correct GStreamer, Qt, and GPU behavior.

# ---- GPU / Qt -----------------------------------------------------------
export QT_XCB_GL_INTEGRATION=xcb_egl

# ---- GStreamer (camera + codec pipeline) --------------------------------
export GST_GL_API=gles2
export GST_GL_PLATFORM=egl
export GST_DEBUG_NO_COLOR=1
export GST_INSPECT_NO_COLORS=1

# Disable videodecoder QoS to prevent frame drops in openpilot
export GST_VIDEO_DECODER_QOS=0

# Preferred formats for V4L2 camera sources (NV12 first for zero-copy)
export GST_V4L2_PREFERRED_FOURCC=NV12:YU12:NV16:YUY2
export GST_VIDEO_CONVERT_PREFERRED_FORMAT=NV12:NV16:I420:YUY2

# Use libv4l2 for V4L2 (required for Rockchip ISP formats)
export GST_V4L2_USE_LIBV4L2=1

# Default camera device symlink (created by udev rule)
export GST_V4L2SRC_DEFAULT_DEVICE=/dev/video-camera0

# Rockchip V4L2 sub-device suffixes
export GST_V4L2SRC_RK_DEVICES=_mainpath:_selfpath:_bypass:_scale

# ---- RGA integration (optional acceleration) ----------------------------
# Uncomment if using GStreamer elements that support RGA fallback:
# export GST_MPP_DEC_DISABLE_NV12_10=1
# export GST_MPP_VIDEODEC_DEFAULT_FORMAT=NV12
# export GST_VIDEO_CONVERT_USE_RGA=1

# ---- NPU ----------------------------------------------------------------
# RK3588 NPU runtime library search path (rknpu2 submodule prebuilts)
for rknpu_path in \
  /data/openpilot/third_party/rknpu2/runtime/RK3588/Linux/librknn_api/aarch64 \
  /data/openpilot/third_party/rknpu2/runtime/RK356X/Linux/librknn_api/aarch64; do
  if [ -d "$rknpu_path" ]; then
    export LD_LIBRARY_PATH="${rknpu_path}:${LD_LIBRARY_PATH}"
    break
  fi
done
