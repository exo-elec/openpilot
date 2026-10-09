"""Bounded Ogg/Opus transport using FFmpeg's ARM-optimized libopus."""
import subprocess

MAX_UTTERANCE_SECONDS = 15
MAX_AUDIO_BYTES = 1024 * 1024


def is_ogg_opus(data: bytes) -> bool:
  return data.startswith(b'OggS') and b'OpusHead' in data[:128]


class OpusCodec:
  def __init__(self, executable='ffmpeg'):
    self.executable = executable

  def _run(self, args, data):
    result = subprocess.run([self.executable, '-hide_banner', '-loglevel', 'error', '-nostdin',
                             *args], input=data, capture_output=True, timeout=10, check=True)
    return result.stdout

  def encode(self, pcm: bytes, rate=16000) -> bytes:
    if rate != 16000 or not pcm or len(pcm) % 2 or len(pcm) > rate * 2 * MAX_UTTERANCE_SECONDS:
      raise ValueError('Voice requires bounded 16 kHz mono PCM16')
    result = self._run(['-f', 's16le', '-ar', str(rate), '-ac', '1', '-i', 'pipe:0',
                        '-c:a', 'libopus', '-application', 'voip', '-b:a', '24k',
                        '-vbr', 'on', '-f', 'ogg', 'pipe:1'], pcm)
    if not is_ogg_opus(result) or len(result) > MAX_AUDIO_BYTES:
      raise ValueError('Invalid Opus encoder result')
    return result

  def decode(self, audio: bytes) -> bytes:
    if len(audio) > MAX_AUDIO_BYTES or not is_ogg_opus(audio):
      raise ValueError('Expected bounded Ogg/Opus reply')
    result = self._run(['-protocol_whitelist', 'pipe', '-f', 'ogg', '-i', 'pipe:0',
                        '-t', '31', '-ar', '48000', '-ac', '1', '-f', 's16le', 'pipe:1'], audio)
    if not result or len(result) % 2 or len(result) > 48000 * 2 * 30:
      raise ValueError('Invalid decoded reply')
    return result
