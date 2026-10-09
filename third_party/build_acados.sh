#!/bin/bash
# Build script for acados submodule
# This builds acados with its external dependencies

set -e

ACADOS_DIR="$(cd "$(dirname "$0")/acados" && pwd)"
BUILD_DIR="$ACADOS_DIR/build"
ARCH="${1:-aarch64}"

echo "Building acados for $ARCH..."

# Initialize submodules
cd "$ACADOS_DIR"
git submodule update --init --recursive

# Create build directory
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# Configure with CMake
cmake .. \
  -DACADOS_WITH_QPOASES=ON \
  -DACADOS_WITH_HPIPM=ON \
  -DACADOS_WITH_BLASFEO=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$BUILD_DIR/install"

# Build
make -j$(nproc)

# Install
make install

# Create arch-specific directories (matching original structure)
mkdir -p "$ACADOS_DIR/$ARCH/lib"
mkdir -p "$ACADOS_DIR/$ARCH/bin"

# Copy libraries
cp "$BUILD_DIR/install/lib"/*.so "$ACADOS_DIR/$ARCH/lib/" 2>/dev/null || true
cp "$BUILD_DIR/install/lib"/*.a "$ACADOS_DIR/$ARCH/lib/" 2>/dev/null || true

# Copy t_renderer
cp "$BUILD_DIR/install/bin/t_renderer" "$ACADOS_DIR/$ARCH/bin/" 2>/dev/null || \
cp "$BUILD_DIR/bin/t_renderer" "$ACADOS_DIR/$ARCH/bin/" 2>/dev/null || true

echo "acados build complete for $ARCH"
echo "Libraries in: $ACADOS_DIR/$ARCH/lib/"
echo "Binaries in: $ACADOS_DIR/$ARCH/bin/"
