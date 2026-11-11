"""AssemblyAI text-to-speech. HTTP stream of 24 kHz pcm_s16le.

POST https://tts.us.assemblyai.com/v1/speech
`language` is a full name (`spanish`), not a code (`es`).
Each language is pinned to its catalog voice so eval runs stay repeatable.
"""

import httpx

TTS_URL = "https://tts.us.assemblyai.com/v1/speech"
TTS_MAX_CHARS = 5000


class TTSError(RuntimeError):
    pass


class GatewayTTS:
    def __init__(self, api_key, tts_settings):
        self.api_key = api_key
        self.settings = tts_settings

    def voice_for(self, language_name):
        return (self.settings.get("voices") or {}).get(language_name) or None

    async def stream(self, text, language_name, meta):
        """Yield even PCM16 chunks. `meta` receives sample_rate and billed_chars.

        The caller stamps time-to-first-audio when the first chunk arrives,
        which is when this process has the first TTS byte.
        """
        spoken = (text or "").strip()[:TTS_MAX_CHARS]
        if not spoken:
            return
        seconds = float(self.settings.get("timeout_s") or 30)
        body = {
            "text": spoken,
            "language": language_name,
            "sample_rate": int(self.settings.get("sample_rate") or 24000),
            "encoding": self.settings.get("encoding") or "pcm_s16le",
            "container": "raw",
            "stream": True,
        }
        voice = self.voice_for(language_name)
        if voice:
            body["voice_id"] = voice
        timeout = httpx.Timeout(connect=10.0, read=seconds, write=10.0, pool=10.0)
        headers = {"Authorization": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", TTS_URL, headers=headers, json=body) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", "replace")[:300]
                        raise TTSError(f"Speech synthesis returned HTTP {response.status_code}: {detail}")
                    meta["sample_rate"] = _int_header(
                        response.headers.get("x-tts-sample-rate"), body["sample_rate"]
                    )
                    meta["billed_chars"] = _int_header(
                        response.headers.get("x-tts-billed-characters"), len(spoken)
                    )
                    pending = b""
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        pending += chunk
                        even = len(pending) - (len(pending) % 2)
                        if even:
                            yield pending[:even]
                            pending = pending[even:]
                    if pending:
                        yield pending + b"\x00"
        except TTSError:
            raise
        except httpx.TimeoutException as exc:
            raise TTSError(f"Speech synthesis timed out after {int(seconds)} seconds.") from exc
        except httpx.HTTPError as exc:
            raise TTSError(f"Speech synthesis could not be reached ({exc.__class__.__name__}).") from exc


def _int_header(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def audio_ms(nbytes, sample_rate):
    if not sample_rate:
        return 0
    return int(nbytes / 2 / sample_rate * 1000)
