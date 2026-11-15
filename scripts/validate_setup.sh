#!/usr/bin/env bash
# EnhancedOpenPilot Validation Script
# Run this to check the health of the EOP implementation

set -e

echo "=========================================="
echo "EnhancedOpenPilot (EOP) Validation"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS=0
FAIL=0
WARN=0

check_pass() {
    echo -e "${GREEN}✓${NC} $1"
    PASS=$((PASS + 1))
}

check_fail() {
    echo -e "${RED}✗${NC} $1"
    FAIL=$((FAIL + 1))
}

check_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    WARN=$((WARN + 1))
}

# 1. Python Syntax Checks
echo "1. Python Syntax Checks"
echo "-----------------------"

MODULES=(
    "exopilot/selfdrive/pathd/pathd.py"
    "exopilot/selfdrive/pathd/track.py"
    "exopilot/selfdrive/pathd/predict.py"
    "exopilot/selfdrive/pathd/lane_change.py"
    "exopilot/selfdrive/pathd/blindspot.py"
    "exopilot/selfdrive/pathd/path_corridor_fusion.py"
    "exopilot/selfdrive/gridd/gridd.py"
    "exopilot/selfdrive/gridd/lazy_bev.py"
    "system/socketd/socketd.py"
    "system/bluetoothd/bluetoothd.py"
    "exopilot/system/obd2d/obd2d.py"
)

for module in "${MODULES[@]}"; do
    if python3 -m py_compile "$module" 2>/dev/null; then
        check_pass "$module"
    else
        check_fail "$module"
    fi
done

echo ""

# 2. Import Checks (skip hardware registry due to RK3588 requirement)
echo "2. Import Checks (non-hardware modules)"
echo "----------------------------------------"

IMPORTS=(
    "exopilot.selfdrive.pathd.track:ObjectTracker"
    "exopilot.selfdrive.pathd.predict:predict"
    "exopilot.selfdrive.pathd.lane_change:evaluate_lane_change_gate"
    "exopilot.selfdrive.pathd.blindspot:check_lane_change_safety"
    "exopilot.selfdrive.pathd.path_corridor_fusion:fuse_corridor_boundaries"
    "exopilot.selfdrive.gridd.lazy_bev:LazyBEV"
    "exopilot.system.obd2d.obd2d:OBD2SocketCANBridge"
)

for import_str in "${IMPORTS[@]}"; do
    module="${import_str%%:*}"
    class="${import_str##*:}"
    
    if python3 -c "from $module import $class" 2>/dev/null; then
        check_pass "$module"
    else
        check_fail "$module"
    fi
done

echo ""

# 3. Check for broken pandad references
echo "3. Checking for stale references to removed daemons"
echo "---------------------------------------------------"

STALE_PATTERNS=(
    "from openpilot.selfdrive.pandad:pandad"
    "from openpilot.selfdrive.car:car (use vehicled)"
    "from openpilot.system.sensord:sensord (use imud)"
)

for pattern_desc in "${STALE_PATTERNS[@]}"; do
    pattern="${pattern_desc%%:*}"
    desc="${pattern_desc##*:}"
    count=$(grep -r "$pattern" --include="*.py" selfdrive/ system/ tools/ 2>/dev/null | grep -v __pycache__ | wc -l)
    if [ "$count" -eq 0 ]; then
        check_pass "No stale $desc references"
    else
        check_warn "Found $count stale $desc references"
    fi
done

echo ""

# 4. Check documentation exists
echo "4. Documentation Check"
echo "---------------------"

DOCS=(
    "AGENTS.md"
    "exopilot/selfdrive/pathd/README.md"
    "docs/eop/EOP.md"
)

for doc in "${DOCS[@]}"; do
    if [ -f "$doc" ]; then
        check_pass "$doc"
    else
        check_fail "$doc missing"
    fi
done

echo ""

# 5. Check process configuration
echo "5. Process Configuration"
echo "-----------------------"

if grep -q "pathd.*exopilot.selfdrive.pathd.avoidance" system/manager/process_config.py; then
    check_pass "pathd registered in process_config.py"
else
    check_fail "pathd not found in process_config.py"
fi

if grep -q "socketd.*system.socketd.socketd" system/manager/process_config.py; then
    check_pass "socketd registered in process_config.py"
else
    check_fail "socketd not found in process_config.py"
fi

if grep -q "bluetoothd.*system.bluetoothd.bluetoothd" system/manager/process_config.py; then
    check_pass "bluetoothd registered in process_config.py"
else
    check_fail "bluetoothd not found in process_config.py"
fi

echo ""

# 6. Check cereal message definitions
echo "7. Cereal Message Definitions"
echo "----------------------------"

if grep -q "enhancedTrajectory" cereal/services.py; then
    check_pass "enhancedTrajectory in services.py"
else
    check_fail "enhancedTrajectory not in services.py"
fi

if grep -q "gridObjects" cereal/services.py; then
    check_pass "gridObjects in services.py"
else
    check_fail "gridObjects not in services.py"
fi

if grep -q "stereoGround" cereal/services.py; then
    check_pass "stereoGround in services.py"
else
    check_fail "stereoGround not in services.py"
fi

echo ""

# Summary
echo "=========================================="
echo "Validation Summary"
echo "=========================================="
echo -e "${GREEN}Passed:${NC} $PASS"
echo -e "${RED}Failed:${NC} $FAIL"
echo -e "${YELLOW}Warnings:${NC} $WARN"
echo ""

if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}All critical checks passed!${NC}"
    exit 0
else
    echo -e "${RED}Some checks failed. Please review.${NC}"
    exit 1
fi
