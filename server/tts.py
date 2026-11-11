"""Text-to-speech through AssemblyAI's official SDK.

    tts = TextToSpeech(api_key=...)
    with tts.stream(text, TTSConfig(language="spanish", ...)) as speech:
        for chunk in speech:
            play(chunk)

The HTTP route is still POST /v1/speech. Chunks are not sample-aligned, so
an odd trailing byte is held until the next one. The first yielded chunk is
the first audio byte this process received.
"""

import asyncio
import threading

from assemblyai import Client, Settings, TTSConfig, TTSError as SdkTTSError, TextToSpeech

TTS_MAX_CHARS = 5000


class TTSError(RuntimeError):
    pass


class GatewayTTS:
    def __init__(self, api_key, tts_settings):
        self.api_key = api_key
        self.settings = tts_settings
        self._tts = None

    def voice_for(self, language_name):
        return (self.settings.get("voices") or {}).get(language_name) or None

    def _client(self):
        seconds = float(self.settings.get("timeout_s") or 30)
        if self._tts is None:
            http = Client(settings=Settings(api_key=self.api_key, tts_http_timeout=seconds))
            self._tts = TextToSpeech(client=http)
        else:
            self._tts.client.settings.tts_http_timeout = seconds
        return self._tts

    async def stream(self, text, language_name, meta):
        spoken = (text or "").strip()[:TTS_MAX_CHARS]
        if not spoken:
            return
        seconds = float(self.settings.get("timeout_s") or 30)
        config = TTSConfig(
            language=language_name,
            sample_rate=int(self.settings.get("sample_rate") or 24000),
            encoding="pcm_s16le",
            container="raw",
        )
        voice = self.voice_for(language_name)
        if voice:
            config = TTSConfig(
                voice_id=voice,
                language=language_name,
                sample_rate=config.sample_rate,
                encoding="pcm_s16le",
                container="raw",
            )
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        stop = threading.Event()

        def worker():
            pending = b""
            try:
                with self._client().stream(spoken, config) as speech:
                    info = speech.info
                    meta["sample_rate"] = info.sample_rate
                    meta["billed_chars"] = info.billed_characters
                    for chunk in speech:
                        if stop.is_set():
                            break
                        pending += chunk
                        even = len(pending) - (len(pending) % 2)
                        if even:
                            loop.call_soon_threadsafe(queue.put_nowait, pending[:even])
                            pending = pending[even:]
                    if pending and not stop.is_set():
                        loop.call_soon_threadsafe(queue.put_nowait, pending + b"\x00")
            except SdkTTSError as exc:
                loop.call_soon_threadsafe(queue.put_nowait, _clean(exc, seconds))
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, _clean(exc, seconds))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=worker, name="pingo-tts", daemon=True).start()
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            stop.set()


def _clean(exc, seconds):
    text = str(exc)
    if "timeout" in text.lower() or "timed out" in text.lower():
        return TTSError(f"Speech synthesis timed out after {int(seconds)} seconds.")
    return TTSError(f"Speech synthesis failed: {text}")


def audio_ms(nbytes, sample_rate):
    if not sample_rate:
        return 0
    return int(nbytes / 2 / sample_rate * 1000)
