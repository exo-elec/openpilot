#!/usr/bin/env bash
# Install Rockchip vendor dependencies for openpilot on RK3588.
#
# This script detects the SoC, checks for required .deb packages,
# installs missing ones from SDK paths, and verifies algorithm libraries.
#
# Usage:
#   sudo ./scripts/install_rockchip_deps.sh [--sdk-path PATH] [--iq-src PATH]
#
# Packages installed via dpkg (vendor BSP):
#   - camera_engine_rkaiq_rkXXXX_arm64.deb   -> /usr/lib/librkaiq.so
#   - librockchip-mpp1_1.5.0-1_arm64.deb    -> /usr/lib/aarch64-linux-gnu/librockchip_mpp.so
#   - librga2_2.X.0-1_arm64.deb             -> /usr/lib/aarch64-linux-gnu/librga.so
#
# Libraries shipped in third_party/rockchip_algo/ (no upstream .deb):
#   - libod_share.so   (occlusion detection)
#   - libmd_share.so   (move detection)
#   - libRKAP_3A.so    (audio AEC/AGC/ANR)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Options ---
SDK_PATH=""
IQ_SRC=""
FORCE_INSTALL=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sdk-path) SDK_PATH="$2"; shift 2 ;;
    --iq-src)   IQ_SRC="$2";   shift 2 ;;
    --force)    FORCE_INSTALL=true; shift ;;
    --dry-run)  DRY_RUN=true;   shift ;;
    -h|--help)
      echo "Usage: $0 [--sdk-path PATH] [--iq-src PATH] [--force] [--dry-run]"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# --- Detect SoC ---
detect_soc() {
  local soc="unknown"
  if [[ -f /proc/device-tree/compatible ]]; then
    local compat
    compat="$(tr '\0' '\n' < /proc/device-tree/compatible | head -1)"
    case "$compat" in
      *rk3588*)  soc="rk3588" ;;
      *rk3568*)  soc="rk3568" ;;
      *rk3566*)  soc="rk3566" ;;
      *rk3562*)  soc="rk3562" ;;
    esac
  fi
  if [[ "$soc" == "unknown" ]] && [[ -r /proc/cpuinfo ]]; then
    if grep -q "RK3588" /proc/cpuinfo 2>/dev/null; then soc="rk3588"
    elif grep -q "RK3568" /proc/cpuinfo 2>/dev/null; then soc="rk3568"
    fi
  fi
  echo "$soc"
}

SOC="$(detect_soc)"
if [[ "$SOC" == "unknown" ]]; then
  log_warn "Could not detect Rockchip SoC from device tree. Assuming rk3588."
  SOC="rk3588"
fi
log_info "Detected SoC: $SOC"

# --- SDK path discovery ---
find_sdk_path() {
  local candidates=()
  if [[ -n "$SDK_PATH" ]]; then
    candidates+=("$SDK_PATH")
  fi
  # Common locations
  candidates+=(
    "${PROJECT_ROOT}/../LubanCat_Work/LubanCat_Linux_Generic_Full_SDK_20250826"
    "${PROJECT_ROOT}/../LubanCat_Work/LubanCat_Linux_Generic_SDK_20250916"
    "/opt/rockchip-sdk"
    "/opt/rk3588-sdk"
  )
  for p in "${candidates[@]}"; do
    if [[ -d "$p/external" ]] || [[ -d "$p/debian/packages" ]]; then
      echo "$p"
      return 0
    fi
  done
  echo ""
}

FOUND_SDK="$(find_sdk_path)"
if [[ -n "$FOUND_SDK" ]]; then
  log_info "Found SDK at: $FOUND_SDK"
else
  log_warn "No Rockchip SDK found in default paths. Pass --sdk-path if you have one."
fi

# --- Package definitions ---
# Map: package_name -> library_file -> description
declare -A PKG_LIBS=(
  ["camera_engine_rkaiq_${SOC}_arm64"]="/usr/lib/librkaiq.so:RKAIQ 3A ISP engine"
  ["librockchip-mpp1"]="/usr/lib/aarch64-linux-gnu/librockchip_mpp.so.0:MPP hardware codec"
  ["librga2"]="/usr/lib/aarch64-linux-gnu/librga.so.2:RGA 2D accelerator"
)

declare -A PKG_DEB_NAMES=(
  ["camera_engine_rkaiq_${SOC}_arm64"]="camera_engine_rkaiq_${SOC}_arm64"
  ["librockchip-mpp1"]="librockchip-mpp1"
  ["librga2"]="librga2"
)

# --- Check installed packages ---
check_package() {
  local pkg_name="$1"
  dpkg -l "$pkg_name" 2>/dev/null | grep -q "^ii" || return 1
  return 0
}

# --- Find .deb file in SDK ---
find_deb() {
  local pkg_name="$1"
  local soc="$2"
  if [[ -z "$FOUND_SDK" ]]; then
    echo ""
    return 0
  fi
  local search_dirs=(
    "$FOUND_SDK/debian/packages/arm64/rkaiq"
    "$FOUND_SDK/debian/packages/arm64/mpp"
    "$FOUND_SDK/debian/packages/arm64/rga2"
    "$FOUND_SDK/debian/packages/arm64/rga"
    "$FOUND_SDK/debian11/packages/arm64/rkaiq"
    "$FOUND_SDK/debian11/packages/arm64/mpp"
    "$FOUND_SDK/debian11/packages/arm64/rga2"
    "$FOUND_SDK/debian11/packages/arm64/rga"
    "$FOUND_SDK/debian12/packages/arm64/rkaiq"
    "$FOUND_SDK/debian12/packages/arm64/mpp"
    "$FOUND_SDK/debian12/packages/arm64/rga2"
    "$FOUND_SDK/debian12/packages/arm64/rga"
    "$FOUND_SDK/ubuntu20.04/packages/arm64/rkaiq"
    "$FOUND_SDK/ubuntu20.04/packages/arm64/mpp"
    "$FOUND_SDK/ubuntu20.04/packages/arm64/rga2"
    "$FOUND_SDK/ubuntu20.04/packages/arm64/rga"
    "$FOUND_SDK/ubuntu22.04/packages/arm64/rkaiq"
    "$FOUND_SDK/ubuntu22.04/packages/arm64/mpp"
    "$FOUND_SDK/ubuntu22.04/packages/arm64/rga2"
    "$FOUND_SDK/ubuntu22.04/packages/arm64/rga"
    "$FOUND_SDK/ubuntu/packages/arm64/rkaiq"
    "$FOUND_SDK/ubuntu/packages/arm64/mpp"
    "$FOUND_SDK/ubuntu/packages/arm64/rga2"
    "$FOUND_SDK/ubuntu/packages/arm64/rga"
    "$FOUND_SDK/debian/packages/arm64/camera_engine"
  )
  for d in "${search_dirs[@]}"; do
    if [[ -d "$d" ]]; then
      local found
      found="$(find "$d" -maxdepth 1 -name "${pkg_name}*.deb" -print -quit 2>/dev/null)"
      if [[ -n "$found" ]]; then
        echo "$found"
        return 0
      fi
    fi
  done
  echo ""
}

# --- Install a single package ---
install_pkg() {
  local pkg_name="$1"
  local deb_path="$2"

  if $DRY_RUN; then
    log_info "[DRY-RUN] Would install: $deb_path"
    return 0
  fi

  log_info "Installing $pkg_name from $deb_path ..."
  dpkg -i "$deb_path"
  # Fix any dependency issues
  apt-get install -f -y || true
}

# --- Main package loop ---
ALL_OK=true
MISSING_PKGS=()

log_info "Checking Rockchip BSP packages ..."

for pkg_id in "camera_engine_rkaiq_${SOC}_arm64" "librockchip-mpp1" "librga2"; do
  lib_path="${PKG_LIBS[$pkg_id]%%:*}"
  desc="${PKG_LIBS[$pkg_id]#*:}"
  deb_name="${PKG_DEB_NAMES[$pkg_id]}"

  if [[ -f "$lib_path" ]] && ! $FORCE_INSTALL; then
    log_info "  OK: $desc ($lib_path)"
    continue
  fi

  if check_package "$deb_name"; then
    log_info "  OK: $deb_name installed (library may need ldconfig)"
    ldconfig 2>/dev/null || true
    if [[ -f "$lib_path" ]]; then
      continue
    fi
    # Installed but library not found — maybe different path
    log_warn "  $deb_name installed but $lib_path not found"
  fi

  log_warn "  MISSING: $desc"
  ALL_OK=false
  MISSING_PKGS+=("$pkg_id")

  # Try to find and install .deb
  deb_file="$(find_deb "$deb_name" "$SOC")"
  if [[ -n "$deb_file" ]]; then
    if $DRY_RUN; then
      log_info "  [DRY-RUN] Would install $deb_file"
    else
      install_pkg "$deb_name" "$deb_file"
      ldconfig 2>/dev/null || true
    fi
  else
    log_warn "  No .deb found for $deb_name in SDK paths."
  fi
done

# --- Algorithm libraries (shipped in repo, no .deb) ---
log_info "Checking algorithm libraries (shipped in third_party/rockchip_algo) ..."
ALGO_DIR="${PROJECT_ROOT}/third_party/rockchip_algo/aarch64"
for lib in libod_share.so libmd_share.so libRKAP_3A.so; do
  if [[ -f "${ALGO_DIR}/${lib}" ]]; then
    log_info "  OK: ${lib}"
  else
    log_warn "  MISSING: ${lib} — copy from SDK external/common_algorithm/"
    ALL_OK=false
  fi
done

# --- ldconfig check for runtime loading ---
log_info "Verifying ldconfig cache ..."
for lib in librkaiq.so librockchip_mpp.so librga.so; do
  if ldconfig -p 2>/dev/null | grep -q "$lib"; then
    log_info "  OK: $lib in ldconfig"
  else
    log_warn "  $lib not in ldconfig cache — may need ldconfig or LD_LIBRARY_PATH"
  fi
done

# --- IQ files ---
log_info "Checking IQ files ..."
IQ_ETC="/etc/iqfiles"
if [[ -d "$IQ_ETC" ]]; then
  iq_count="$(find "$IQ_ETC" -maxdepth 1 -name "*.json" | wc -l)"
  log_info "  $iq_count IQ files in $IQ_ETC"
else
  log_warn "  $IQ_ETC does not exist. RKAIQ needs IQ files there."
  if [[ -n "$IQ_SRC" ]] && [[ -d "$IQ_SRC" ]]; then
    if ! $DRY_RUN; then
      mkdir -p "$IQ_ETC"
      cp -v "$IQ_SRC"/*.json "$IQ_ETC/" || true
      log_info "  Copied IQ files from $IQ_SRC"
    else
      log_info "  [DRY-RUN] Would copy IQ files from $IQ_SRC"
    fi
  elif [[ -n "$FOUND_SDK" ]]; then
    local sdk_iq
    sdk_iq="$(find "$FOUND_SDK" -path "*/external/rkaiq/iqfiles" -type d -print -quit 2>/dev/null)"
    if [[ -z "$sdk_iq" ]]; then
      sdk_iq="$(find "$FOUND_SDK" -path "*/iqfiles" -type d -print -quit 2>/dev/null)"
    fi
    if [[ -n "$sdk_iq" ]]; then
      log_info "  Found SDK IQ files at: $sdk_iq"
      if ! $DRY_RUN; then
        mkdir -p "$IQ_ETC"
        cp -v "$sdk_iq"/*.json "$IQ_ETC/" || true
      else
        log_info "  [DRY-RUN] Would copy IQ files from $sdk_iq"
      fi
    fi
  fi
fi

# IQ tuning files ship from the closed exopilot `hal` package (installed via
# exopilot/scripts/install/setup_rk3588.sh) into /etc/iqfiles — this repo no longer
# carries its own copy.

# --- Summary ---
echo ""
if $ALL_OK; then
  log_info "All Rockchip dependencies satisfied."
  exit 0
else
  log_warn "Some dependencies are missing."
  if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    echo ""
    echo "Missing .deb packages:"
    for p in "${MISSING_PKGS[@]}"; do
      echo "  - $p"
    done
    echo ""
    echo "To install manually, copy the .deb files from your Rockchip SDK and run:"
    echo "  sudo dpkg -i camera_engine_rkaiq_${SOC}_arm64.deb"
    echo "  sudo dpkg -i librockchip-mpp1_1.5.0-1_arm64.deb"
    echo "  sudo dpkg -i librga2_2.2.0-1_arm64.deb"
    echo "  sudo apt-get install -f"
  fi
  exit 1
fi
