import difflib
import os

from openpilot.common.params import Params

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
SIMILARITY_THRESHOLD = 0.85


def get_nn_model_path(car_fingerprint: str) -> tuple[str, str, bool]:
  """Return (model_path, model_name, exact_match)."""
  if not os.path.isdir(MODELS_DIR):
    return "", "MOCK", False

  candidates = [f.replace('.json', '') for f in os.listdir(MODELS_DIR) if f.endswith('.json')]
  if car_fingerprint in candidates:
    return os.path.join(MODELS_DIR, f"{car_fingerprint}.json"), car_fingerprint, True

  best_match = None
  best_ratio = 0.0
  for c in candidates:
    ratio = difflib.SequenceMatcher(None, car_fingerprint, c).ratio()
    if ratio > best_ratio:
      best_ratio = ratio
      best_match = c

  if best_match and best_ratio >= SIMILARITY_THRESHOLD:
    return os.path.join(MODELS_DIR, f"{best_match}.json"), best_match, False

  return "", "MOCK", False
