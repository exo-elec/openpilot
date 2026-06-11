# Third Party Dependencies

This directory contains external dependencies managed as git submodules for easy updates.

## Git Submodules (All third_party directories)

All dependencies in this directory are now git submodules pointing to official upstream repositories.
**All submodules are pinned to specific release versions for reproducible builds.**

### Core Libraries

| Submodule | Purpose | Used By | Version | Repository |
|-----------|---------|---------|---------|------------|
| `acados/` | MPC solver library for control optimization | lat_mpc.py, long_mpc.py | **v0.5.3** | https://github.com/acados/acados |
| `arm_compute/` | ARM Compute Library for optimized CPU/CL operations | inferenced, ACL backend | **v25.04** | https://github.com/ARM-software/ComputeLibrary |
| `catch2/` | C++ test framework | All C++ unit tests | **v3.14.0** | https://github.com/catchorg/Catch2 |

### Video/Camera

| Submodule | Purpose | Used By | Version | Repository |
|-----------|---------|---------|---------|------------|
| `libyuv/` | Image format conversion (YUV/RGB) | v4l2d, camera pipeline | **4c3d7d5** | https://chromium.googlesource.com/libyuv/libyuv |
| `rockchip_mpp/` | Rockchip Media Process Platform (codec accel) | v4l2d, loggerd | **1.0.11** | https://github.com/rockchip-linux/mpp |
| `rockchip_rga/` | Rockchip 2D Raster Graphic Acceleration | v4l2d, image processing | **v1.10.0** | https://github.com/airockchip/librga |
| `rknpu2/` | Rockchip NPU runtime (RKNN API) | monod, RKNN inference | **v1.5.2** | https://github.com/rockchip-linux/rknpu2 |

### Vendored Algorithm Libraries (No Upstream Package)

These libraries ship as prebuilt `.so` files because Rockchip does not provide
upstream `.deb` packages or independently maintained git repositories for them.

| Directory | Library | Purpose | Size | Source |
|-----------|---------|---------|------|--------|
| `rockchip_algo/aarch64/` | `libod_share.so` | Occlusion detection (camera blockage) | ~12 KB | Rockchip SDK `external/common_algorithm/video/occlusion_detect/lib64/` |
| `rockchip_algo/aarch64/` | `libmd_share.so` | Move detection (frame-gating during standstill) | ~20 KB | Rockchip SDK `external/common_algorithm/video/move_detect/lib64/` |
| `rockchip_algo/aarch64/` | `libRKAP_3A.so` | Audio AEC/AGC/ANR | ~60 KB | Rockchip SDK `external/common_algorithm/audio/rkap_3a/lib64/` |

**Loading order:** At runtime, `_libloader.py` searches **system paths first**
(`/usr/lib`, `/usr/lib/aarch64-linux-gnu`, via `ldconfig`) and falls back to
`third_party/rockchip_algo/aarch64/`. This lets you upgrade the algorithm
libraries system-wide without modifying the project.

### UI Framework

| Submodule | Purpose | Used By | Version | Repository |
|-----------|---------|---------|---------|------------|
| `raylib/` | Raylib game library for installer UI | selfdrive/ui/installer | **5.5** | https://github.com/raysan5/raylib |

### Navigation

| Submodule | Purpose | Used By | Official Repository |
|-----------|---------|---------|---------------------|
| `valhalla/src/` | Valhalla routing engine | navd | https://github.com/valhalla/valhalla |

### Camera Driver References (On-Demand)

| Submodule | Purpose | Used By | Official Repository |
|-----------|---------|---------|---------------------|
| `linux_gc4653/` | Galaxycore GC4653 sensor driver reference | v4l2d development | https://github.com/inindev/linux-rockchip.git (v6.14-rc4-rk3588) |
| `linux_ox03c10/` | OmniVision OX03C10 sensor driver reference | v4l2d development | https://github.com/nxp-imx/linux-imx.git (lf-6.6.y) |

### Python Libraries (On-Demand)

| Submodule | Purpose | Used By | Official Repository |
|-----------|---------|---------|---------------------|
| `python-udsoncan/` | UDS on CAN protocol | obd2d | https://github.com/pylessard/python-udsoncan |
| `python-can-isotp/` | ISO-TP CAN transport | obd2d | https://github.com/pylessard/python-can-isotp |
| `pygnssutils/` | GNSS NTRIP client | rtkd | https://github.com/semuconsulting/pygnssutils |

### Utility Libraries

| Submodule | Purpose | Used By | Version | Repository |
|-----------|---------|---------|---------|------------|
| `json11/` | JSON parsing library (Dropbox - archived) | swaglog.cc | **v1.0.0** | https://github.com/dropbox/json11 |
| `kaitai/` | Kaitai Struct runtime headers | ubloxd | **0.10** | https://github.com/kaitai-io/kaitai_struct_cpp_stl_runtime |
| `qrcode/` | QR code generation | selfdrive/ui | **v1.8.0** | https://github.com/nayuki/QR-Code-generator |

## Initialization

### Initialize all submodules
```bash
git submodule update --init --recursive
```

### Initialize specific submodules
```bash
# Core components (required for build)
git submodule update --init third_party/acados
git submodule update --init third_party/arm_compute
git submodule update --init third_party/catch2
git submodule update --init third_party/libyuv
git submodule update --init third_party/rockchip_mpp
git submodule update --init third_party/rockchip_rga
git submodule update --init third_party/rknpu2
git submodule update --init third_party/raylib

# Navigation
git submodule update --init third_party/valhalla/src

# Optional: Camera driver references
git submodule update --init third_party/linux_gc4653
git submodule update --init third_party/linux_ox03c10

# Optional: Python libraries for OBD2/RTK
git submodule update --init third_party/python-udsoncan
git submodule update --init third_party/python-can-isotp
git submodule update --init third_party/pygnssutils

# Utility libraries
git submodule update --init third_party/json11
git submodule update --init third_party/kaitai
git submodule update --init third_party/qrcode
```

## Building Submodules

Some submodules require compilation before use:

### Auto-built by SCons
These are built automatically during the SCons build:
- **libyuv** - CMake build, outputs to `build/{arch}/`
- **catch2** - CMake build, outputs to `build/lib/`
- **raylib** - CMake build, outputs to `build/raylib/`

### Manual build required
- **acados** - Complex build with external dependencies:
  ```bash
  cd third_party
  ./build_acados.sh [arch]  # arch: aarch64, x86_64, Darwin
  ```
  This builds blasfeo, hpipm, and acados with all dependencies.

## Removed Components

The following components were previously in third_party but have been removed:

| Component | Reason | Replacement |
|-----------|--------|-------------|
| `bootstrap/` | Only used by cabana (removed) | None - icons embedded in UI |
| `linux/` | Qualcomm-specific headers | Rockchip MPP headers from submodule |
| `qt5/` | Binary wrappers for larch64 | System Qt5 tools (lrelease, lupdate) |
| `cabana/` | CAN analyzer tool | External CAN tools |
| `bodyteleop/` | Web teleoperation | None |
| `rknn_toolkit2/` | Large NPU toolkit | System package |
| `tappas/` | Hailo examples | System package |
| `hailo_model_zoo/` | Large model repo | System package |

## Updating Submodules

To update all submodules to the latest upstream version:
```bash
git submodule update --remote
```

To update a specific submodule:
```bash
git submodule update --remote third_party/acados
```

**Note**: After updating acados, you must rebuild it using `build_acados.sh`.

## Summary Table

| Category | Count | Submodules |
|----------|-------|------------|
| **Core Libraries** | 3 | acados, arm_compute, catch2 |
| **Video/Camera** | 4 | libyuv, rockchip_mpp, rockchip_rga, rockchip_rknpu2 |
| **UI** | 1 | raylib |
| **Navigation** | 1 | valhalla |
| **Camera Drivers** | 2 | linux_gc4653, linux_ox03c10 |
| **Python Libraries** | 3 | python-udsoncan, python-can-isotp, pygnssutils |
| **Utility Libraries** | 3 | json11, kaitai, qrcode |
| **Total** | **17** | - |

## Rockchip System Dependencies (Not in Git)

The following libraries are **not** in this repository. They are expected to be
installed on the target system via vendor `.deb` packages:

| Library | `.deb` Package (example) | Install Path |
|---------|--------------------------|--------------|
| `librkaiq.so` | `camera_engine_rkaiq_rk3576_arm64.deb` | `/usr/lib/librkaiq.so` |
| `librockchip_mpp.so` | `librockchip-mpp1_1.5.0-1_arm64.deb` | `/usr/lib/aarch64-linux-gnu/librockchip_mpp.so` |
| `librga.so` | `librga2_2.2.0-1_arm64.deb` | `/usr/lib/aarch64-linux-gnu/librga.so` |

Use the dependency checker to validate/install:
```bash
sudo ./scripts/install_rockchip_deps.sh
```

## See Also

- [CAMERA_DRIVERS.md](CAMERA_DRIVERS.md) - Camera driver details
- [RK3576_ISP_INTEGRATION_GUIDE.md](../docs/eop/01_Core/RK3576_ISP_INTEGRATION_GUIDE.md) - ISP integration
- [THIRD_PARTY_SUBMODULES_STATUS.md](../docs/migration/THIRD_PARTY_SUBMODULES_STATUS.md) - Cleanup status
