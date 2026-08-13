#!/usr/bin/env python3
"""
Piper TTS Integration for ExoPilot

Offline neural TTS using Piper (https://github.com/rhasspy/piper).
Supports 20 languages (Piper ∩ Whisper overlap, ≥20M speakers) + English fallback.
Language selected via EOPLanguage param; EOPTTSVoice="auto" → auto-select.

Models downloaded on first use to ~/.local/share/piper
"""

from __future__ import annotations

import io
import logging
import urllib.request
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_PIPER_BASE = 'https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0'

# 20 supported languages (Piper ∩ Whisper, ≥20M speakers) + English baseline
# Entry: (onnx_url, json_url)
VOICE_MODELS: dict[str, tuple[str, str]] = {
  'en_US-amy-medium': (
    f'{_PIPER_BASE}/en/en_US/amy/medium/en_US-amy-medium.onnx',
    f'{_PIPER_BASE}/en/en_US/amy/medium/en_US-amy-medium.onnx.json',
  ),
  'en_US-ryan-medium': (
    f'{_PIPER_BASE}/en/en_US/ryan/medium/en_US-ryan-medium.onnx',
    f'{_PIPER_BASE}/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json',
  ),
  # 1. Chinese ~1.1B speakers
  'zh_CN-huayan-medium': (
    f'{_PIPER_BASE}/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx',
    f'{_PIPER_BASE}/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json',
  ),
  # 2. Spanish ~500M speakers
  'es_ES-davefx-medium': (
    f'{_PIPER_BASE}/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx',
    f'{_PIPER_BASE}/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json',
  ),
  'es_MX-ald-medium': (
    f'{_PIPER_BASE}/es/es_MX/ald/medium/es_MX-ald-medium.onnx',
    f'{_PIPER_BASE}/es/es_MX/ald/medium/es_MX-ald-medium.onnx.json',
  ),
  # 3. Hindi ~600M speakers
  'hi_IN-priyamvada-medium': (
    f'{_PIPER_BASE}/hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx',
    f'{_PIPER_BASE}/hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx.json',
  ),
  # 4. Arabic ~350M speakers
  'ar_JO-kareem-medium': (
    f'{_PIPER_BASE}/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx',
    f'{_PIPER_BASE}/ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx.json',
  ),
  # 5. Portuguese ~250M speakers
  'pt_BR-faber-medium': (
    f'{_PIPER_BASE}/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx',
    f'{_PIPER_BASE}/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json',
  ),
  # 6. Russian ~260M speakers
  'ru_RU-denis-medium': (
    f'{_PIPER_BASE}/ru/ru_RU/denis/medium/ru_RU-denis-medium.onnx',
    f'{_PIPER_BASE}/ru/ru_RU/denis/medium/ru_RU-denis-medium.onnx.json',
  ),
  # 7. German ~130M speakers
  'de_DE-thorsten-medium': (
    f'{_PIPER_BASE}/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx',
    f'{_PIPER_BASE}/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json',
  ),
  # 8. French ~280M speakers
  'fr_FR-siwis-medium': (
    f'{_PIPER_BASE}/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx',
    f'{_PIPER_BASE}/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json',
  ),
  # 9. Vietnamese ~85M speakers
  'vi_VN-vais1000-medium': (
    f'{_PIPER_BASE}/vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium.onnx',
    f'{_PIPER_BASE}/vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium.onnx.json',
  ),
  # 10. Turkish ~85M speakers
  'tr_TR-fahrettin-medium': (
    f'{_PIPER_BASE}/tr/tr_TR/fahrettin/medium/tr_TR-fahrettin-medium.onnx',
    f'{_PIPER_BASE}/tr/tr_TR/fahrettin/medium/tr_TR-fahrettin-medium.onnx.json',
  ),
  # 11. Persian/Farsi ~80M speakers
  'fa_IR-gyro-medium': (
    f'{_PIPER_BASE}/fa/fa_IR/gyro/medium/fa_IR-gyro-medium.onnx',
    f'{_PIPER_BASE}/fa/fa_IR/gyro/medium/fa_IR-gyro-medium.onnx.json',
  ),
  # 12. Italian ~65M speakers
  'it_IT-riccardo-x_low': (
    f'{_PIPER_BASE}/it/it_IT/riccardo/x_low/it_IT-riccardo-x_low.onnx',
    f'{_PIPER_BASE}/it/it_IT/riccardo/x_low/it_IT-riccardo-x_low.onnx.json',
  ),
  # 13. Swahili ~100M speakers
  'sw_CD-lanfrica-medium': (
    f'{_PIPER_BASE}/sw/sw_CD/lanfrica/medium/sw_CD-lanfrica-medium.onnx',
    f'{_PIPER_BASE}/sw/sw_CD/lanfrica/medium/sw_CD-lanfrica-medium.onnx.json',
  ),
  # 14. Polish ~45M speakers
  'pl_PL-darkman-medium': (
    f'{_PIPER_BASE}/pl/pl_PL/darkman/medium/pl_PL-darkman-medium.onnx',
    f'{_PIPER_BASE}/pl/pl_PL/darkman/medium/pl_PL-darkman-medium.onnx.json',
  ),
  # 15. Ukrainian ~40M speakers
  'uk_UA-ukrainian_tts-medium': (
    f'{_PIPER_BASE}/uk/uk_UA/ukrainian_tts/medium/uk_UA-ukrainian_tts-medium.onnx',
    f'{_PIPER_BASE}/uk/uk_UA/ukrainian_tts/medium/uk_UA-ukrainian_tts-medium.onnx.json',
  ),
  # 16. Malayalam ~38M speakers
  'ml_IN-arjun-medium': (
    f'{_PIPER_BASE}/ml/ml_IN/arjun/medium/ml_IN-arjun-medium.onnx',
    f'{_PIPER_BASE}/ml/ml_IN/arjun/medium/ml_IN-arjun-medium.onnx.json',
  ),
  # 17. Nepali ~30M speakers
  'ne_NP-chitwan-medium': (
    f'{_PIPER_BASE}/ne/ne_NP/chitwan/medium/ne_NP-chitwan-medium.onnx',
    f'{_PIPER_BASE}/ne/ne_NP/chitwan/medium/ne_NP-chitwan-medium.onnx.json',
  ),
  # 18. Dutch ~25M speakers
  'nl_NL-mls-medium': (
    f'{_PIPER_BASE}/nl/nl_NL/mls/medium/nl_NL-mls-medium.onnx',
    f'{_PIPER_BASE}/nl/nl_NL/mls/medium/nl_NL-mls-medium.onnx.json',
  ),
  # 19. Romanian ~24M speakers
  'ro_RO-mihai-medium': (
    f'{_PIPER_BASE}/ro/ro_RO/mihai/medium/ro_RO-mihai-medium.onnx',
    f'{_PIPER_BASE}/ro/ro_RO/mihai/medium/ro_RO-mihai-medium.onnx.json',
  ),
}

# 2-letter language code → default voice
LANGUAGE_TO_VOICE: dict[str, str] = {
  'en': 'en_US-amy-medium',
  'zh': 'zh_CN-huayan-medium',
  'es': 'es_ES-davefx-medium',
  'hi': 'hi_IN-priyamvada-medium',
  'ar': 'ar_JO-kareem-medium',
  'pt': 'pt_BR-faber-medium',
  'ru': 'ru_RU-denis-medium',
  'de': 'de_DE-thorsten-medium',
  'fr': 'fr_FR-siwis-medium',
  'vi': 'vi_VN-vais1000-medium',
  'tr': 'tr_TR-fahrettin-medium',
  'fa': 'fa_IR-gyro-medium',
  'it': 'it_IT-riccardo-x_low',
  'sw': 'sw_CD-lanfrica-medium',
  'pl': 'pl_PL-darkman-medium',
  'uk': 'uk_UA-ukrainian_tts-medium',
  'ml': 'ml_IN-arjun-medium',
  'ne': 'ne_NP-chitwan-medium',
  'nl': 'nl_NL-mls-medium',
  'ro': 'ro_RO-mihai-medium',
}

FALLBACK_VOICE = 'en_US-amy-medium'
VOICES_DIR = Path.home() / '.local/share/piper'
SAMPLE_RATE = 22050


def resolve_voice(voice_param: str, language: str) -> str:
  """Resolve Piper voice from EOPTTSVoice + EOPLanguage params.

  Priority:
  1. Explicit voice name in VOICE_MODELS → use it
  2. "auto" + known language → LANGUAGE_TO_VOICE lookup
  3. Unknown language → FALLBACK_VOICE (English)
  """
  if voice_param and voice_param != 'auto' and voice_param in VOICE_MODELS:
    return voice_param
  voice = LANGUAGE_TO_VOICE.get(language, FALLBACK_VOICE)
  if language not in LANGUAGE_TO_VOICE:
    logger.warning('Language "%s" not in EOP10 20-language set; falling back to English. ' +
                   'Use VisionPilot for extended language support.', language)
  return voice


def _ensure_model(voice_name: str) -> Path | None:
  """Return path to model file, downloading if not present."""
  if voice_name not in VOICE_MODELS:
    logger.error('Unknown voice: %s', voice_name)
    return None

  VOICES_DIR.mkdir(parents=True, exist_ok=True)
  model_file = VOICES_DIR / f'{voice_name}.onnx'
  config_file = VOICES_DIR / f'{voice_name}.onnx.json'

  if model_file.exists() and config_file.exists():
    return model_file

  model_url, config_url = VOICE_MODELS[voice_name]
  logger.info('Downloading Piper voice: %s', voice_name)
  try:
    urllib.request.urlretrieve(model_url, model_file)
    urllib.request.urlretrieve(config_url, config_file)
    logger.info('Downloaded: %s', voice_name)
  except Exception:
    logger.exception('Failed to download voice %s', voice_name)
    return None
  return model_file


class PiperTTS:
  """Piper Text-to-Speech engine for ExoPilot.

  20 languages supported (Piper ∩ Whisper, ≥20M speakers) + English fallback.
  For additional languages upgrade to VisionPilot.
  """

  def __init__(self, voice_id: str = FALLBACK_VOICE):
    self.voice_id = voice_id
    self.sample_rate = SAMPLE_RATE
    self._piper = None
    self._initialized = False
    self._init_piper()

  def _init_piper(self) -> bool:
    try:
      from piper import PiperVoice
    except ImportError:
      logger.warning('piper-tts not installed; TTS disabled. Run: pip install piper-tts')
      return False

    model_path = _ensure_model(self.voice_id)
    if model_path is None:
      return False

    try:
      self._piper = PiperVoice.load(str(model_path))
      self._initialized = True
      logger.info('Piper TTS ready: %s', self.voice_id)
      return True
    except Exception:
      logger.exception('Failed to load voice %s', self.voice_id)
      return False

  def set_voice(self, voice_id: str):
    if voice_id == self.voice_id and self._initialized:
      return
    self.voice_id = voice_id
    self._initialized = False
    self._piper = None
    self._init_piper()

  def is_available(self) -> bool:
    return self._initialized

  def synthesize(self, text: str) -> np.ndarray | None:
    """Synthesize text → float32 audio array [-1, 1] at SAMPLE_RATE Hz."""
    if not self._initialized or self._piper is None:
      return None
    try:
      buf = io.BytesIO()
      self._piper.synthesize(text, buf, length_scale=1.0, noise_scale=0.667, noise_w=0.8)
      buf.seek(0)
      samples = np.frombuffer(buf.read(), dtype=np.int16)
      return samples.astype(np.float32) / 32768.0
    except Exception:
      logger.exception('TTS synthesis failed')
      return None
