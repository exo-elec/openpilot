#include <array>
#include <cstdio>
#include <filesystem>
#include <string>

#include "common/util.h"

extern "C" {

static const std::array stages = {
  std::pair{"Receiving objects: ", 91},
  std::pair{"Resolving deltas: ", 2},
  std::pair{"Updating files: ", 7},
};

int freshClone() {
  std::string cmd = util::string_format("git clone --progress %s -b %s --depth=1 --recurse-submodules %s 2>&1",
                                        "https://github.com/commaai/openpilot.git", "release", "/data/tmppilot");
  return executeGitCommand(cmd);
}

int cachedFetch(const std::string &cache) {
  std::string tmp = "/data/tmppilot";
  util::run(util::string_format("cp -rp %s %s", cache.c_str(), tmp.c_str()));
  util::run(util::string_format("cd %s && git remote set-branches --add origin %s", tmp.c_str(), "release").c_str());
  return executeGitCommand(util::string_format("cd %s && git fetch --progress origin %s 2>&1", tmp.c_str(), "release"));
}

int executeGitCommand(const std::string &cmd) {
  FILE *pipe = popen(cmd.c_str(), "r");
  if (!pipe) return -1;

  char buffer[512];
  while (fgets(buffer, sizeof(buffer), pipe) != nullptr) {
    std::string line(buffer);
    int base = 0;
    for (const auto &[text, weight] : stages) {
      if (line.find(text) != std::string::npos) {
        break;
      }
      base += weight;
    }
  }
  return pclose(pipe);
}
}
