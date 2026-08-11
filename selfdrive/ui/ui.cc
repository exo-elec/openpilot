#include "selfdrive/ui/ui.h"

#include <algorithm>
#include <cmath>

#include <QtConcurrent>

#include "common/transformations/orientation.hpp"
#include "common/swaglog.h"
#include "common/util.h"
#include "common/watchdog.h"
#include "system/hardware/hw.h"

#define BACKLIGHT_DT 0.05
#define BACKLIGHT_TS 10.00

static void update_sockets(UIState *s) {
  s->sm->update(0);
}

static void update_state(UIState *s) {
  SubMaster &sm = *(s->sm);
  UIScene &scene = s->scene;

  if (sm.updated("liveCalibration")) {
    auto list2rot = [](const capnp::List<float>::Reader &rpy_list) ->Eigen::Matrix3f {
      return euler2rot({rpy_list[0], rpy_list[1], rpy_list[2]}).cast<float>();
    };

    auto live_calib = sm["liveCalibration"].getLiveCalibration();
    if (live_calib.getCalStatus() == cereal::LiveCalibrationData::Status::CALIBRATED) {
      auto device_from_calib = list2rot(live_calib.getRpyCalib());
      auto wide_from_device = list2rot(live_calib.getWideFromDeviceEuler());
      s->scene.view_from_calib = VIEW_FROM_DEVICE * device_from_calib;
      s->scene.view_from_wide_calib = VIEW_FROM_DEVICE * wide_from_device * device_from_calib;
    } else {
      s->scene.view_from_calib = s->scene.view_from_wide_calib = VIEW_FROM_DEVICE;
    }
  }
  if (sm.updated("wideRoadCameraState")) {
    auto cam_state = sm["wideRoadCameraState"].getWideRoadCameraState();
    float scale = (cam_state.getSensor() == cereal::FrameData::ImageSensor::AR0231) ? 6.0f : 1.0f;
    scene.light_sensor = std::max(100.0f - scale * cam_state.getExposureValPercent(), 0.0f);
  } else if (!sm.allAliveAndValid({"wideRoadCameraState"})) {
    scene.light_sensor = -1;
  }

  // EOP: Ignition from EOPIgnitionOn param (no Panda — SocketD + BrownPanda)
  auto params = Params();
  scene.ignition = params.getBool("EOPIgnitionOn");
  scene.started = sm["deviceState"].getDeviceState().getStarted() && scene.ignition;
  scene.recording_audio = params.getBool("RecordAudio") && scene.started;

  // Speed limit: OSM mapData (km/h → m/s) has priority over navInstruction (already m/s)
  float speed_limit_ms = 0.0f;
  if (sm.valid("mapData")) {
    float osl = sm["mapData"].getMapData().getSpeedLimit();
    if (osl > 0.0f) {
      speed_limit_ms = osl / 3.6f;  // km/h → m/s
    }
  }
  if (speed_limit_ms == 0.0f && sm.valid("navInstruction")) {
    float nsl = sm["navInstruction"].getNavInstruction().getSpeedLimit();
    if (nsl > 0.0f) {
      speed_limit_ms = nsl;  // already m/s
    }
  }
  scene.nav_speed_limit_ms = speed_limit_ms;
  
  // OBD2 telemetry — copy primitives out before sm buffer is recycled
  if (sm.valid("obdState")) {
    auto obd = sm["obdState"].getObdState();
    scene.obd_state.vehicle_type   = obd.getVehicleType();
    scene.obd_state.battery_soc    = obd.getBatterySoc();
    scene.obd_state.battery_voltage = obd.getBatteryVoltage();
    scene.obd_state.battery_current = obd.getBatteryCurrent();
    scene.obd_state.motor_temp     = obd.getMotorTemp();
    scene.obd_state.engine_rpm     = obd.getEngineRpm();
    scene.obd_state.coolant_temp   = obd.getCoolantTemp();
    scene.obd_state.fuel_level     = obd.getFuelLevel();
    // scene.obd_state.error_codes    = obd.getErrorCodes();  // EOP: not in ObdState schema
  }

  // Voice assistant state — copy primitives out
  if (sm.valid("voiceState")) {
    auto vs = sm["voiceState"].getVoiceState();
    scene.voice_state.state      = vs.getState();
    scene.voice_state.transcript = vs.getTranscript();
    scene.voice_state.intent     = vs.getIntent();
    scene.voice_state.reply      = vs.getReply();
    scene.voice_state.confidence = vs.getConfidence();
  }
  if (sm.valid("ttsStatus")) {
    scene.tts_playing = sm["ttsStatus"].getTtsStatus().getPlaying();
  }
  if (sm.valid("micStatus")) {
    scene.mic_level_db = sm["micStatus"].getMicStatus().getMicLevelDb();
  }
  if (sm.valid("alccState")) {
    scene.alcc_active = sm["alccState"].getAlccState().getActive();
  }
}

void ui_update_params(UIState *s) {
  auto params = Params();
  s->scene.is_metric = params.getBool("IsMetric");
}

void UIState::updateStatus() {
  if (scene.started && sm->updated("selfdriveState")) {
    auto ss = (*sm)["selfdriveState"].getSelfdriveState();
    auto state = ss.getState();
    if (state == cereal::SelfdriveState::OpenpilotState::PRE_ENABLED || state == cereal::SelfdriveState::OpenpilotState::OVERRIDING) {
      status = STATUS_OVERRIDE;
    } else {
      status = ss.getEnabled() ? STATUS_ENGAGED : STATUS_DISENGAGED;
    }
  }

  if (engaged() != engaged_prev) {
    engaged_prev = engaged();
    emit engagedChanged(engaged());
  }

  // Handle onroad/offroad transition
  if (scene.started != started_prev || sm->frame == 1) {
    if (scene.started) {
      status = STATUS_DISENGAGED;
      scene.started_frame = sm->frame;
    }
    started_prev = scene.started;
    emit offroadTransition(!scene.started);
  }
}

UIState::UIState(QObject *parent) : QObject(parent) {
  sm = std::make_unique<SubMaster>(std::vector<const char*>{
    "modelV2", "controlsState", "liveCalibration", "radarState", "deviceState",
    "carParams", "driverPoseState", "carState",
    "wideRoadCameraState", "managerState", "selfdriveState", "longitudinalPlan",
    "navInstruction", "navRoute", "liveLocationKalman", "mapData",
    "obdState", "voiceState", "ttsStatus", "micStatus",
    "driverStatus", "gpsLocation", "gpsLocationExternal",
    "pandaState", "sensorEvents", "alccState",
  });
  prime_state = new PrimeState(this);
  language = QString::fromStdString(Params().get("LanguageSetting"));

  // update timer
  timer = new QTimer(this);
  QObject::connect(timer, &QTimer::timeout, this, &UIState::update);
  timer->start(1000 / UI_FREQ);
}

void UIState::update() {
  update_sockets(this);
  update_state(this);
  updateStatus();

  if (sm->frame % UI_FREQ == 0) {
    watchdog_kick(nanos_since_boot());
  }
  emit uiUpdate(*this);
}

Device::Device(QObject *parent) : brightness_filter(BACKLIGHT_OFFROAD, BACKLIGHT_TS, BACKLIGHT_DT), QObject(parent) {
  setAwake(true);
  resetInteractiveTimeout();

  QObject::connect(uiState(), &UIState::uiUpdate, this, &Device::update);
}

void Device::update(const UIState &s) {
  updateBrightness(s);
  updateWakefulness(s);
}

void Device::setAwake(bool on) {
  if (on != awake) {
    awake = on;
    // Hardware::set_display_power(awake);  // EOP: not in Hardware class
    LOGD("setting display power %d", awake);
    emit displayPowerChanged(awake);
  }
}

void Device::resetInteractiveTimeout(int timeout) {
  if (timeout == -1) {
    timeout = (ignition_on ? 10 : 30);
  }
  interactive_timeout = timeout * UI_FREQ;
}

void Device::updateBrightness(const UIState &s) {
  float clipped_brightness = offroad_brightness;
  if (s.scene.started && s.scene.light_sensor >= 0) {
    clipped_brightness = s.scene.light_sensor;

    // CIE 1931 - https://www.photonstophotos.net/GeneralTopics/Exposure/Psychometric_Lightness_and_Gamma.htm
    if (clipped_brightness <= 8) {
      clipped_brightness = (clipped_brightness / 903.3);
    } else {
      clipped_brightness = std::pow((clipped_brightness + 16.0) / 116.0, 3.0);
    }

    // Scale back to 10% to 100%
    clipped_brightness = std::clamp(100.0f * clipped_brightness, 10.0f, 100.0f);
  }

  int brightness = brightness_filter.update(clipped_brightness);
  if (!awake) {
    brightness = 0;
  }

  if (brightness != last_brightness) {
    if (!brightness_future.isRunning()) {
      // brightness_future = QtConcurrent::run(Hardware::set_brightness, brightness);  // EOP: not in Hardware class
      last_brightness = brightness;
      last_brightness = brightness;
    }
  }
}

void Device::updateWakefulness(const UIState &s) {
  bool ignition_just_turned_off = !s.scene.ignition && ignition_on;
  ignition_on = s.scene.ignition;

  if (ignition_just_turned_off) {
    resetInteractiveTimeout();
  } else if (interactive_timeout > 0 && --interactive_timeout == 0) {
    emit interactiveTimeout();
  }

  setAwake(s.scene.ignition || interactive_timeout > 0);
}

UIState *uiState() {
  static UIState ui_state;
  return &ui_state;
}

Device *device() {
  static Device _device;
  return &_device;
}
