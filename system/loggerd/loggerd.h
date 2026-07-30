#pragma once

#include <vector>

#include "cereal/messaging/messaging.h"
#include "cereal/services.h"
#include "msgq/visionipc/visionipc_client.h"
#include "system/hardware/hw.h"
#include "common/params.h"
#include "common/swaglog.h"
#include "common/util.h"

#include "system/loggerd/logger.h"

constexpr int MAIN_FPS = 20;
const auto MAIN_ENCODE_TYPE = Hardware::PC() ? cereal::EncodeIndex::Type::BIG_BOX_LOSSLESS : cereal::EncodeIndex::Type::FULL_H_E_V_C;
#define NO_CAMERA_PATIENCE 500  // fall back to time-based rotation if all cameras are dead

#define INIT_ENCODE_FUNCTIONS(encode_type)                                \
  .get_encode_data_func = &cereal::Event::Reader::get##encode_type##Data, \
  .set_encode_idx_func = &cereal::Event::Builder::set##encode_type##Idx,  \
  .init_encode_data_func = &cereal::Event::Builder::init##encode_type##Data

const bool LOGGERD_TEST = getenv("LOGGERD_TEST");
const int SEGMENT_LENGTH = LOGGERD_TEST ? atoi(getenv("LOGGERD_SEGMENT_LENGTH")) : 60;

constexpr char PRESERVE_ATTR_NAME[] = "user.preserve";
constexpr char PRESERVE_ATTR_VALUE = '1';

struct EncoderSettings {
  cereal::EncodeIndex::Type encode_type;
  int bitrate;
  int gop_size;
  int b_frames = 0; // we don't use b frames

  static EncoderSettings MainEncoderSettings(int in_width) {
    if (in_width <= 1344) {
      return EncoderSettings{.encode_type = MAIN_ENCODE_TYPE, .bitrate = 5'000'000, .gop_size = 20};
    } else {
      return EncoderSettings{.encode_type = MAIN_ENCODE_TYPE, .bitrate = 10'000'000, .gop_size = 30};
    }
  }

  static EncoderSettings QcamEncoderSettings() {
    return EncoderSettings{.encode_type = cereal::EncodeIndex::Type::QCAMERA_H264, .bitrate = 256'000, .gop_size = 15};
  }

  static EncoderSettings StreamEncoderSettings() {
    return EncoderSettings{.encode_type = cereal::EncodeIndex::Type::QCAMERA_H264, .bitrate = 1'000'000, .gop_size = 15};
  }
};

class EncoderInfo {
public:
  const char *publish_name;
  const char *thumbnail_name = NULL;
  const char *filename = NULL;
  bool record = true;
  bool include_audio = false;
  int frame_width = -1;
  int frame_height = -1;
  int fps = MAIN_FPS;
  std::function<EncoderSettings(int)> get_settings;

  ::cereal::EncodeData::Reader (cereal::Event::Reader::*get_encode_data_func)() const;
  void (cereal::Event::Builder::*set_encode_idx_func)(::cereal::EncodeIndex::Reader);
  cereal::EncodeData::Builder (cereal::Event::Builder::*init_encode_data_func)();
};

class LogCameraInfo {
public:
  const char *thread_name;
  int fps = MAIN_FPS;
  VisionStreamType stream_type;
  const char *vipc_server_name = "v4l2d";
  std::vector<EncoderInfo> encoder_infos;
};

const EncoderInfo main_road_encoder_info = {
  .publish_name = "roadEncodeData",
  .thumbnail_name = "thumbnail",
  .filename = "fcamera.hevc",
  .get_settings = [](int in_width){return EncoderSettings::MainEncoderSettings(in_width);},
  INIT_ENCODE_FUNCTIONS(RoadEncode),
};

const EncoderInfo main_wide_road_encoder_info = {
  .publish_name = "wideRoadEncodeData",
  .filename = "ecamera.hevc",
  .get_settings = [](int in_width){return EncoderSettings::MainEncoderSettings(in_width);},
  INIT_ENCODE_FUNCTIONS(WideRoadEncode),
};

const EncoderInfo main_rear_encoder_info = {
  .publish_name = "rearEncodeData",
  .filename = "rcamera.hevc",
  .record = Params().getBool("EOPRearCameraEnabled"),
  .get_settings = [](int in_width){return EncoderSettings::MainEncoderSettings(in_width);},
  INIT_ENCODE_FUNCTIONS(RearEncode),
};

const EncoderInfo stream_road_encoder_info = {
  .publish_name = "livestreamRoadEncodeData",
  //.thumbnail_name = "thumbnail",
  .record = false,
  .get_settings = [](int){return EncoderSettings::StreamEncoderSettings();},
  INIT_ENCODE_FUNCTIONS(LivestreamRoadEncode),
};

const EncoderInfo stream_wide_road_encoder_info = {
  .publish_name = "livestreamWideRoadEncodeData",
  .record = false,
  .get_settings = [](int){return EncoderSettings::StreamEncoderSettings();},
  INIT_ENCODE_FUNCTIONS(LivestreamWideRoadEncode),
};

const EncoderInfo stream_rear_encoder_info = {
  .publish_name = "livestreamRearEncodeData",
  .record = false,
  .get_settings = [](int){return EncoderSettings::StreamEncoderSettings();},
  INIT_ENCODE_FUNCTIONS(LivestreamRearEncode),
};

const EncoderInfo main_tele_road_encoder_info = {
  .publish_name = "teleRoadEncodeData",
  .filename = "tcamera.hevc",
  .get_settings = [](int in_width){return EncoderSettings::MainEncoderSettings(in_width);},
  INIT_ENCODE_FUNCTIONS(TeleRoadEncode),
};

const EncoderInfo main_stereo_left_encoder_info = {
  .publish_name = "stereoLeftCameraEncodeData",
  .filename = "lcamera.hevc",
  .get_settings = [](int in_width){return EncoderSettings::MainEncoderSettings(in_width);},
  INIT_ENCODE_FUNCTIONS(StereoLeftCameraEncode),
};

const EncoderInfo main_stereo_right_encoder_info = {
  .publish_name = "stereoRightCameraEncodeData",
  .filename = "rcamera.hevc",
  .get_settings = [](int in_width){return EncoderSettings::MainEncoderSettings(in_width);},
  INIT_ENCODE_FUNCTIONS(StereoRightCameraEncode),
};

const EncoderInfo stream_stereo_left_encoder_info = {
  .publish_name = "livestreamStereoLeftEncodeData",
  .record = false,
  .get_settings = [](int){return EncoderSettings::StreamEncoderSettings();},
  INIT_ENCODE_FUNCTIONS(LivestreamStereoLeftEncode),
};

const EncoderInfo stream_stereo_right_encoder_info = {
  .publish_name = "livestreamStereoRightEncodeData",
  .record = false,
  .get_settings = [](int){return EncoderSettings::StreamEncoderSettings();},
  INIT_ENCODE_FUNCTIONS(LivestreamStereoRightEncode),
};

const EncoderInfo qcam_encoder_info = {
  .publish_name = "qRoadEncodeData",
  .filename = "qcamera.ts",
  .get_settings = [](int){return EncoderSettings::QcamEncoderSettings();},
  .frame_width = 526,
  .frame_height = 330,
  .include_audio = Params().getBool("RecordAudio"),
  INIT_ENCODE_FUNCTIONS(QRoadEncode),
};

const LogCameraInfo road_camera_info{
  .thread_name = "road_cam_encoder",
  .stream_type = VISION_STREAM_ROAD,
  .encoder_infos = {main_road_encoder_info, qcam_encoder_info}
};

const LogCameraInfo wide_road_camera_info{
  .thread_name = "wide_road_cam_encoder",
  .stream_type = VISION_STREAM_WIDE_ROAD,
  .encoder_infos = {main_wide_road_encoder_info}
};

const LogCameraInfo rear_camera_info{
  .thread_name = "rear_cam_encoder",
  .stream_type = VISION_STREAM_REAR,
  .vipc_server_name = "uvcd",
  .encoder_infos = {main_rear_encoder_info}
};

const LogCameraInfo stream_road_camera_info{
  .thread_name = "road_cam_encoder",
  .stream_type = VISION_STREAM_ROAD,
  .encoder_infos = {stream_road_encoder_info}
};

const LogCameraInfo stream_wide_road_camera_info{
  .thread_name = "wide_road_cam_encoder",
  .stream_type = VISION_STREAM_WIDE_ROAD,
  .encoder_infos = {stream_wide_road_encoder_info}
};

const LogCameraInfo stream_rear_camera_info{
  .thread_name = "rear_cam_encoder",
  .stream_type = VISION_STREAM_REAR,
  .vipc_server_name = "uvcd",
  .encoder_infos = {stream_rear_encoder_info}
};

const LogCameraInfo tele_road_camera_info{
  .thread_name = "tele_road_cam_encoder",
  .stream_type = VISION_STREAM_TELE_ROAD,
  .encoder_infos = {main_tele_road_encoder_info}
};

const LogCameraInfo stereo_left_camera_info{
  .thread_name = "stereo_left_cam_encoder",
  .stream_type = VISION_STREAM_STEREO_LEFT,
  .encoder_infos = {main_stereo_left_encoder_info}
};

const LogCameraInfo stereo_right_camera_info{
  .thread_name = "stereo_right_cam_encoder",
  .stream_type = VISION_STREAM_STEREO_RIGHT,
  .encoder_infos = {main_stereo_right_encoder_info}
};

const LogCameraInfo stream_stereo_left_camera_info{
  .thread_name = "stereo_left_cam_encoder",
  .stream_type = VISION_STREAM_STEREO_LEFT,
  .encoder_infos = {stream_stereo_left_encoder_info}
};

const LogCameraInfo stream_stereo_right_camera_info{
  .thread_name = "stereo_right_cam_encoder",
  .stream_type = VISION_STREAM_STEREO_RIGHT,
  .encoder_infos = {stream_stereo_right_encoder_info}
};

const LogCameraInfo cameras_logged[] = {
  road_camera_info, wide_road_camera_info, rear_camera_info,
  tele_road_camera_info, stereo_left_camera_info, stereo_right_camera_info
};
const LogCameraInfo stream_cameras_logged[] = {
  stream_road_camera_info, stream_wide_road_camera_info, stream_rear_camera_info,
  stream_stereo_left_camera_info, stream_stereo_right_camera_info
};
