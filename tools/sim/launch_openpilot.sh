#!/usr/bin/bash

export PASSIVE="0"
export NOBOARD="1"
export SIMULATION="1"
export SKIP_FW_QUERY="1"
# EOP: vehicled is Tesla-protocol, no fingerprinting needed

# Block hardware daemons that cannot run on PC
export BLOCK="${BLOCK},v4l2d,loggerd,encoderd,micd,spkd,logmessaged,socketd,inferenced,uvcd,wdgd,imud,pigeond,hardwared,thermald,bluetoothd,rtkd,mcapd"
if [[ "$CI" ]]; then
  # TODO: offscreen UI should work
  export BLOCK="${BLOCK},ui"
fi

python3 -c "from openpilot.selfdrive.test.helpers import set_params_enabled; set_params_enabled()"

SCRIPT_DIR=$(dirname "$0")
OPENPILOT_DIR=$SCRIPT_DIR/../../

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"
OPENPILOT_ABS="$( cd "$OPENPILOT_DIR" >/dev/null && pwd )"
export PYTHONPATH="$OPENPILOT_ABS${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$OPENPILOT_ABS/system/manager/manager.py"
