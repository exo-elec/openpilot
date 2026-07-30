#!/bin/bash
# Install pygnssutils for NTRIP RTK support

set -e

echo "Installing pygnssutils for NTRIP RTK GPS support..."

# Check if we're in the openpilot directory
if [ ! -f "SConstruct" ]; then
    echo "Error: Run this script from the openpilot root directory"
    exit 1
fi

# Initialize submodule if not already done
if [ ! -d "third_party/pygnssutils/.git" ]; then
    echo "Initializing pygnssutils submodule..."
    git submodule update --init --recursive third_party/pygnssutils
fi

# Install pygnssutils in development mode
echo "Installing pygnssutils..."
pip install -e third_party/pygnssutils

echo "pygnssutils installed successfully!"
echo ""
echo "You can now use NTRIP RTK GPS with:"
echo "  params put_bool EOPRTKEnabled true"
echo "  params put_bool EOPNTRIPEnabled true"
echo "  params put EOPNTRIPCaster 'rtk2go.com'"
echo "  params put EOPNTRIPMount 'YOUR-MOUNTPOINT'"
