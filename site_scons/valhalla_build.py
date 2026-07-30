"""
Valhalla build configuration for ARM64 (RK3588)
Following the same pattern as other third_party dependencies
"""

import os
import subprocess
import SCons


VALHALLA_SRC_DIR = Dir("#third_party/valhalla/src").abspath
BUILD_DIR = Dir("#third_party/valhalla/build").abspath
INSTALL_DIR = Dir("#third_party/valhalla/bin").abspath


def build_valhalla(env):
    """
    Build Valhalla routing engine from third_party/valhalla/valhalla_repo submodule.
    Binaries installed to third_party/valhalla/bin/
    """
    valhalla_service = os.path.join(INSTALL_DIR, "valhalla_service")
    
    # Check if already built
    if os.path.exists(valhalla_service):
        print(f"Valhalla already built: {valhalla_service}")
        return INSTALL_DIR
    
    # Check if submodule exists
    if not os.path.exists(os.path.join(VALHALLA_SRC_DIR, "CMakeLists.txt")):
        print("ERROR: valhalla_repo submodule not initialized!")
        print("Run: git submodule update --init --recursive")
        return None
    
    print("Building Valhalla for ARM64 (this may take a while)...")
    
    # Create directories
    os.makedirs(BUILD_DIR, exist_ok=True)
    os.makedirs(INSTALL_DIR, exist_ok=True)
    
    # CMake configuration optimized for embedded ARM64
    cmake_args = [
        "cmake",
        "-B", BUILD_DIR,
        "-S", VALHALLA_SRC_DIR,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_INSTALL_PREFIX=/usr/local",
        "-DCMAKE_PREFIX_PATH=/usr/local",
        "-DBUILD_SHARED_LIBS=OFF",
        "-DENABLE_BENCHMARKS=OFF",
        "-DENABLE_TESTS=OFF",
        "-DENABLE_TOOLS=ON",
        "-DENABLE_DATA_TOOLS=ON",
        "-DENABLE_SERVICES=ON",
        "-DENABLE_PYTHON_BINDINGS=OFF",
        "-DENABLE_NODE_BINDINGS=OFF",
        "-DENABLE_HTTP=ON",
        "-DENABLE_CCACHE=OFF",
    ]
    
    # Run CMake configure
    print("Configuring Valhalla with CMake...")
    result = subprocess.run(cmake_args, cwd=VALHALLA_SRC_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"CMake configure failed:\n{result.stderr}")
        return None
    
    # Build with limited parallelism (conserve RAM on embedded)
    print("Building Valhalla (this may take 10-30 minutes)...")
    build_args = ["cmake", "--build", BUILD_DIR, "--parallel", "2"]
    result = subprocess.run(build_args, cwd=VALHALLA_SRC_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Valhalla build failed:\n{result.stderr}")
        return None
    
    # Copy binaries to install directory
    binaries = [
        "valhalla_service",
        "valhalla_build_tiles",
        "valhalla_build_config",
        "valhalla_build_admins",
        "valhalla_build_timezones",
    ]
    
    for binary in binaries:
        src = os.path.join(BUILD_DIR, binary)
        if os.path.exists(src):
            dst = os.path.join(INSTALL_DIR, binary)
            print(f"Installing {binary}...")
            subprocess.run(["cp", src, dst], check=True)
            subprocess.run(["chmod", "+x", dst], check=True)
    
    print(f"Valhalla build complete: {INSTALL_DIR}")
    return INSTALL_DIR


def valhalla_installed(env):
    """Check if Valhalla binaries are already built."""
    valhalla_service = os.path.join(INSTALL_DIR, "valhalla_service")
    return os.path.exists(valhalla_service)


Export('build_valhalla', 'valhalla_installed')
