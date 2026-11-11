"""Text-to-speech through AssemblyAI's WebSocket session.

    aai.settings.tts_base_url = "https://tts.us.assemblyai.com"
    with aai.TTSSession(config=aai.TTSConfig(voice_id=..., language="spanish")) as session:
        for chunk in session.speak(text):
            play(chunk)

Raw PCM at 24 kHz. speak() yields the first audio byte as soon as it arrives.
cancel() is the barge-in. TTSSession has been in the SDK since 1.8.2; we run 1.9.1.
"""

import asyncio
import threading

import assemblyai as aai

TTS_URL = "https://tts.us.assemblyai.com"
TTS_CAP = 300


class TTSError(RuntimeError):
    pass


class GatewayTTS:
    def __init__(self, api_key, tts_settings):
        self.api_key = api_key
        self.settings = tts_settings

    def voice_for(self, language_name):
        return (self.settings.get("voices") or {}).get(language_name) or None

    async def stream(self, text, language_name, meta):
        spoken = (text or "").strip()[:TTS_CAP]
        if not spoken:
            return
        seconds = float(self.settings.get("timeout_s") or 30)
        voice = self.voice_for(language_name)
        config = aai.TTSConfig(
            voice_id=voice,
            language=language_name,
            sample_rate=int(self.settings.get("sample_rate") or 24000),
            encoding="pcm_s16le",
        )
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        stop = threading.Event()
        holder = {}

        def worker():
            pending = b""
            try:
                aai.settings.tts_base_url = TTS_URL
                with aai.TTSSession(config=config, api_key=self.api_key) as session:
                    holder["session"] = session
                    meta["sample_rate"] = config.sample_rate or 24000
                    meta["billed_chars"] = len(spoken)
                    for chunk in session.speak(spoken):
                        if stop.is_set():
                            session.cancel()
                            break
                        if not chunk:
                            continue
                        pending += chunk
                        even = len(pending) - (len(pending) % 2)
                        if even:
                            loop.call_soon_threadsafe(queue.put_nowait, pending[:even])
                            pending = pending[even:]
                    if pending and not stop.is_set():
                        loop.call_soon_threadsafe(queue.put_nowait, pending + b"\x00")
                    if stop.is_set():
                        session.close()
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
            session = holder.get("session")
            if session is not None:
                try:
                    session.cancel()
                except Exception:
                    pass


def _clean(exc, seconds):
    text = str(exc)
    if "timeout" in text.lower() or "timed out" in text.lower():
        return TTSError(f"Speech synthesis timed out after {int(seconds)} seconds.")
    return TTSError(f"Speech synthesis failed: {text}")


def audio_ms(nbytes, sample_rate):
    if not sample_rate:
        return 0
    return int(nbytes / 2 / sample_rate * 1000)
