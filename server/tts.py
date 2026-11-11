"""Text-to-speech through AssemblyAI's WebSocket session.

    aai.settings.tts_base_url = "https://tts.us.assemblyai.com"
    with aai.TTSSession(config=aai.TTSConfig(voice_id=..., language="spanish")) as session:
        for chunk in session.speak(text):
            play(chunk)

Raw PCM at 24 kHz. speak() yields the first audio byte as soon as it arrives.
cancel() is the barge-in. TTSSession has been in the SDK since 1.8.2; we run 1.9.1.

The account may open only 5 TTS sessions a minute, and a tutor turn now speaks
in two voices (the learner's language and the target language). So each
learner connection keeps one TTSSession per voice open (VoicePool) and calls
speak() on it for every segment, instead of opening a session per segment.
There is no speed control: TTSConfig has no speed field and the HTTP API only
accepts speed=1.
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

    def config_for(self, language_name):
        return aai.TTSConfig(
            voice_id=self.voice_for(language_name),
            language=language_name,
            sample_rate=int(self.settings.get("sample_rate") or 24000),
            encoding="pcm_s16le",
        )

    def pool(self):
        """Long-lived sessions for one learner connection. Close with aclose()."""
        return VoicePool(self)

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


class _Channel:
    """One open TTSSession for one voice, reused for every segment in that voice."""

    def __init__(self, api_key, config):
        self.api_key = api_key
        self.config = config
        self.session = None
        self.lock = threading.Lock()

    def _ensure(self):
        session = self.session
        if session is not None and not _ended(session):
            return session, False
        if session is None:
            aai.settings.tts_base_url = TTS_URL
            session = aai.TTSSession(config=self.config, api_key=self.api_key, inactivity_timeout=120)
            self.session = session
        session.connect()  # also reconnects a session the API closed for inactivity
        return session, True

    def close(self):
        session, self.session = self.session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def _ended(session):
    stopped = getattr(session, "_stopped", None)
    return getattr(session, "_ws", None) is None or (stopped is not None and stopped.is_set())


class VoicePool:
    """Per-connection TTS: one long-lived session per voice. Same stream() as GatewayTTS."""

    def __init__(self, owner):
        self.owner = owner
        self.channels = {}

    def _channel(self, language_name):
        if language_name not in self.channels:
            self.channels[language_name] = _Channel(self.owner.api_key, self.owner.config_for(language_name))
        return self.channels[language_name]

    async def stream(self, text, language_name, meta):
        spoken = (text or "").strip()[:TTS_CAP]
        if not spoken:
            return
        seconds = float(self.owner.settings.get("timeout_s") or 30)
        channel = self._channel(language_name)
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        stop = threading.Event()
        finished = threading.Event()

        def worker():
            pending = b""
            try:
                with channel.lock:
                    if stop.is_set():
                        return
                    for attempt in (0, 1):
                        session, fresh = channel._ensure()
                        meta["sample_rate"] = channel.config.sample_rate or 24000
                        meta["billed_chars"] = len(spoken)
                        meta["reused_session"] = not fresh
                        try:
                            got_audio = False
                            for chunk in session.speak(spoken):
                                if stop.is_set():
                                    session.cancel()
                                    break
                                if not chunk:
                                    continue
                                got_audio = True
                                pending += chunk
                                even = len(pending) - (len(pending) % 2)
                                if even:
                                    loop.call_soon_threadsafe(queue.put_nowait, pending[:even])
                                    pending = pending[even:]
                            break
                        except Exception:
                            # A reused session the API already closed: reconnect once.
                            if fresh or got_audio or attempt:
                                raise
                            channel.close()
                    if pending and not stop.is_set():
                        loop.call_soon_threadsafe(queue.put_nowait, pending + b"\x00")
                    finished.set()
            except Exception as exc:
                channel.close()
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

    async def warm(self, language_names):
        """Open the sessions before the first turn so its audio does not wait on a handshake."""

        def connect(channel):
            with channel.lock:
                try:
                    channel._ensure()
                except Exception:
                    channel.close()  # the first real segment reports the error

        await asyncio.gather(
            *(asyncio.to_thread(connect, self._channel(name)) for name in dict.fromkeys(language_names))
        )

    async def aclose(self):
        channels, self.channels = list(self.channels.values()), {}
        for channel in channels:
            await asyncio.to_thread(channel.close)


def _clean(exc, seconds):
    text = str(exc)
    if "timeout" in text.lower() or "timed out" in text.lower():
        return TTSError(f"Speech synthesis timed out after {int(seconds)} seconds.")
    return TTSError(f"Speech synthesis failed: {text}")


def audio_ms(nbytes, sample_rate):
    if not sample_rate:
        return 0
    return int(nbytes / 2 / sample_rate * 1000)
