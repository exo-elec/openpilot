#!/bin/bash
# Initialize CARLA submodule (shallow, on-demand).
#
# CARLA's repo is large; we only fetch it when sim tests are needed.
# Uses shallow clone + sparse-checkout to keep size manageable.
#
# Usage:
#   ./tools/sim/init_carla_submodule.sh

set -euo pipefail

CARLA_DIR="third_party/carla"
REPO_URL="https://github.com/carla-simulator/carla.git"
BRANCH="0.9.16"

echo "[INFO] Initializing CARLA submodule (this may take a few minutes)..."

# If the directory exists but is empty, remove it so git can populate it
if [ -d "$CARLA_DIR" ] && [ -z "$(ls -A "$CARLA_DIR" 2>/dev/null)" ]; then
    rmdir "$CARLA_DIR"
fi

# Add submodule if not already tracked
if ! git submodule status "$CARLA_DIR" >/dev/null 2>&1; then
    git submodule add --depth 1 -b "$BRANCH" "$REPO_URL" "$CARLA_DIR" || true
fi

# Init + update (shallow)
git submodule update --init --depth 1 -- "$CARLA_DIR"

cd "$CARLA_DIR"

# Enable sparse-checkout to only keep PythonAPI/examples and PythonAPI/util
if git config --local core.sparsecheckout >/dev/null 2>&1; then
    echo "[INFO] Sparse-checkout already enabled"
else
    git sparse-checkout init --cone
    git sparse-checkout set PythonAPI/examples PythonAPI/util Docs
fi

echo "[INFO] CARLA submodule ready at $CARLA_DIR"
echo "[INFO] Available: PythonAPI/examples, PythonAPI/util"
