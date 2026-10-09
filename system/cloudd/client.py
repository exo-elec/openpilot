"""HTTPS device client. Google credentials remain on the gateway server."""
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from openpilot.system.cloudd.codec import MAX_AUDIO_BYTES, is_ogg_opus


class NoRedirect(HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    return None  # Never forward the device credential to a redirected host.


class CloudClient:
  def __init__(self, url, token, opener=None):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
      raise ValueError('EOP_CLOUD_URL must be an HTTPS base URL')
    if not token or len(token) > 4096 or '\n' in token or '\r' in token:
      raise ValueError('Invalid cloud credential')
    self.url, self.token = url.rstrip('/'), token
    self.opener = opener or build_opener(NoRedirect())

  @classmethod
  def configured(cls):
    url = os.environ.get('EOP_CLOUD_URL', '')
    token_file = Path(os.environ.get('EOP_CLOUD_TOKEN_FILE', '/etc/exopilot/cloudd.token'))
    if not url or not token_file.exists():
      return None
    if token_file.stat().st_mode & 0o077 or token_file.stat().st_size > 4096:
      raise ValueError('Cloud credential file must be private and bounded')
    return cls(url, token_file.read_text().strip())

  def _request(self, path, body, content_type, language):
    req = Request(self.url + path, data=body, headers={
      'Authorization': 'Bearer ' + self.token, 'Content-Type': content_type,
      'X-Device-Language': language, 'User-Agent': 'ExoPilot-cloudd/1.0',
    })
    with self.opener.open(req, timeout=45) as response:
      data = response.read(2 * MAX_AUDIO_BYTES + 1)
      if len(data) > 2 * MAX_AUDIO_BYTES:
        raise ValueError('Cloud response exceeds limit')
      if path.endswith('/speak'):
        if not is_ogg_opus(data):
          raise ValueError('Invalid compressed speech response')
        return {'audio': data, 'transcript': '', 'reply': '', 'language': language}
      result = json.loads(data)
      audio = base64.b64decode(result.get('replyAudio') or '', validate=True)
      if not audio or len(audio) > MAX_AUDIO_BYTES or not is_ogg_opus(audio):
        raise ValueError('Cloud response must contain Ogg/Opus audio')
      # Proposed vehicle actions are deliberately not dispatched by this daemon.
      return {'audio': audio, 'transcript': str(result.get('transcribedText', ''))[:2048],
              'reply': str(result.get('replyText', ''))[:2048],
              'language': str(result.get('detectedLanguage', language))[:32]}

  def converse(self, audio, language):
    return self._request('/cloudd/converse', audio, 'audio/ogg; codecs=opus', language)

  def speak(self, text, language):
    if not text or len(text.encode()) > 4000:
      raise ValueError('Speech text exceeds limit')
    return self._request('/cloudd/speak', json.dumps({'text': text, 'language': language}).encode(),
                         'application/json', language)
