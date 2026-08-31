#pragma once

// Hardware platform detection for C++ code
// Mirrors the Python hardware abstraction in system/hardware/

#include <cstdlib>
#include <string>
#include <map>

#include "common/util.h"
#include "cereal/gen/cpp/log.capnp.h"

class Hardware {
public:
  // Check if running on PC (development/testing)
  static bool PC() {
    const char* pc_env = std::getenv("OPENPILOT_PC");
    if (pc_env && std::string(pc_env) == "1") {
      return true;
    }
    // Mirrors python: PC = not ROCKCHIP (device-tree based)
    return !ROCKCHIP();
  }

  // Check if running on TICI (comma 3/3X - legacy Qualcomm platform)
  // For EOP, this returns false as we use Rockchip instead
  static bool TICI() {
    // EOP uses Rockchip, not Qualcomm TICI
    return false;
  }

  // Check if running on RK3588 (ExoPilot 01M)
  static bool RK3588() { return matchesPlatform("rk3588"); }

  // Check if running on RK3576 (ExoPilot 02M). Mirrors
  // system/hardware/registry.py's PlatformRegistry.detect(), which already
  // aliases 'rk3576' to 'exopilot02m' on the Python side -- this brings the
  // C++ side (previously RK3588-only) in line with it.
  static bool RK3576() { return matchesPlatform("rk3576"); }

  // Generic Rockchip detection
  static bool ROCKCHIP() {
    return RK3588() || RK3576();
  }

  // Device name for logging
  static std::string get_name() {
    if (RK3588()) return "rk3588";
    if (RK3576()) return "rk3576";
    return "pc";
  }

  static cereal::InitData::DeviceType get_device_type() {
    if (RK3588() || RK3576()) return cereal::InitData::DeviceType::TICI;
    return cereal::InitData::DeviceType::PC;
  }

  static int get_voltage() { return 0; }
  static int get_current() { return 0; }

  static std::string get_serial() {
    // Rockchip: try OTP first, then device-tree serial
    if (ROCKCHIP()) {
      std::string otp = util::read_file("/sys/bus/nvmem/devices/rockchip-otp0/nvmem");
      if (!otp.empty()) {
        // Return first 16 bytes as hex (SHA256-like stable ID)
        std::string hex;
        for (size_t i = 0; i < otp.size() && i < 16; ++i) {
          char buf[3];
          snprintf(buf, sizeof(buf), "%02x", static_cast<unsigned char>(otp[i]));
          hex += buf;
        }
        if (!hex.empty()) return hex;
      }
      std::string dt_serial = util::read_file("/proc/device-tree/serial-number");
      if (!dt_serial.empty()) return dt_serial;
    }
    return "cccccc";
  }

  static std::map<std::string, std::string> get_init_logs() {
    return {};
  }

  static void set_ir_power(int percentage) { (void)percentage; }

  // Get SSH enabled state (always true for development)
  static bool get_ssh_enabled() {
    return true;
  }

  static void set_ssh_enabled(bool enabled) {
    // No-op for now
    (void)enabled;
  }

 private:
  // Shared by RK3588()/RK3576(): an EOP_PLATFORM env var override (for
  // testing) checked first, then a substring match against the device
  // tree's compatible string, read from disk once and cached -- the
  // physical SoC can't change at runtime, so re-reading it on every call
  // (both of these are checked from get_name()/get_device_type()/
  // ROCKCHIP(), none of them hot-path today, but no reason to redo I/O
  // that can only ever have one answer per process) is pure waste.
  static bool matchesPlatform(const char *name) {
    const char *platform = std::getenv("EOP_PLATFORM");
    if (platform && std::string(platform) == name) {
      return true;
    }
    return deviceTreeCompatible().find(name) != std::string::npos;
  }

  static const std::string &deviceTreeCompatible() {
    static const std::string compat = util::read_file("/proc/device-tree/compatible");
    return compat;
  }
};

namespace Path {
  inline std::string openpilot_prefix() {
    return util::getenv("OPENPILOT_PREFIX", "");
  }

  inline std::string comma_home() {
    return util::getenv("HOME") + "/.comma" + Path::openpilot_prefix();
  }

  inline std::string log_root() {
    if (const char *env = getenv("LOG_ROOT")) {
      return env;
    }
    return Hardware::PC() ? Path::comma_home() + "/media/0/realdata" : "/data/media/0/realdata";
  }

  inline std::string params() {
    return util::getenv("PARAMS_ROOT", Hardware::PC() ? (Path::comma_home() + "/params") : "/data/params");
  }

  inline std::string swaglog_ipc() {
    return "ipc:///tmp/logmessage" + Path::openpilot_prefix();
  }

  inline std::string download_cache_root() {
    if (const char *env = getenv("COMMA_CACHE")) {
      return env;
    }
    return "/tmp/comma_download_cache" + Path::openpilot_prefix() + "/";
  }

  inline std::string shm_path() {
#ifdef __APPLE__
    return "/tmp";
#else
    return "/dev/shm";
#endif
  }
}  // namespace Path
