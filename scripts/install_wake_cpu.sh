#!/usr/bin/env bash
# CPU-only wake detector. No STT/TTS model is installed on the vehicle.
set -euo pipefail
wake_python="${1:-.venv/bin/python}"
uv pip install --python "$wake_python" --no-deps openwakeword==0.6.0
uv pip install --python "$wake_python" onnxruntime scipy scikit-learn tqdm requests
if [[ "${2:-}" == --reference-model ]]; then
  "$wake_python" - <<'PY'
from openwakeword.utils import download_models
download_models(model_names=['hey_jarvis'])
PY
fi
echo 'CPU runtime ready. Supply EOP_WAKE_MODEL for Hi EXO; --reference-model installs Hey Jarvis for evaluation.'
