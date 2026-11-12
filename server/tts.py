"""Text-to-speech through AssemblyAI's WebSocket TTS session.

Pipeline position: mic audio -> STT -> LLM tutor -> **TTS (this file)** ->
browser. server/session.py turns each lesson into segments (reply in the
learner's language, phrase in the target language, next prompt) and calls
stream() once per segment; every audio chunk it yields goes straight to the
browser as a binary WebSocket frame.

The SDK pattern:

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

Threading: the SDK is blocking, so each segment runs in a worker thread that
pushes chunks into an asyncio.Queue with loop.call_soon_threadsafe; the async
generator reads that queue. A threading.Event tells the worker to stop when
the learner barges in.
"""

import asyncio
import threading

import assemblyai as aai

# US TTS endpoint, and the max characters spoken per segment (keeps cost and latency
# bounded).
TTS_URL = "https://tts.us.assemblyai.com"
TTS_CAP = 300


class TTSError(RuntimeError):
    """Readable speech-synthesis failure (timeout or API error) shown to the learner."""
    pass


class GatewayTTS:
    """Factory and one-shot client for AssemblyAI TTS.

    Built with the API key and the "tts" config section (voices, sample_rate,
    timeout_s). Sessions normally use pool() for long-lived per-voice sessions;
    stream() here opens a new TTS session per call (simpler, but uses up the
    per-minute session limit faster).
    """
    def __init__(self, api_key, tts_settings):
        """Keep the key and TTS settings; nothing is opened here."""
        self.api_key = api_key
        self.settings = tts_settings

    def voice_for(self, language_name):
        """Configured voice id for a language name ("spanish"), or None for the API default."""
        return (self.settings.get("voices") or {}).get(language_name) or None

    def config_for(self, language_name):
        """TTSConfig for one language: its voice, the configured sample rate, raw PCM16."""
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
        """Speak `text` in `language_name`; async-yield raw PCM16 chunks.

        Inputs: text (cut to TTS_CAP characters), a TTS language name, and a `meta`
        dict that this fills with sample_rate and billed_chars for usage logging.
        Raises TTSError on failure. Closing the generator early (barge-in) stops the
        worker thread and cancels the TTS session.
        """
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
        # Bridge between the SDK thread and asyncio: the worker puts chunks on `queue`
        # from its thread; `stop` asks it to end early; `holder` exposes the session so
        # the finally block below can cancel it.
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        stop = threading.Event()
        holder = {}

        def worker():
            """Thread body: open a session, speak, and pass even-length chunks to the queue.

            Always finishes by queueing None (end marker); errors are queued as TTSError.
            """
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
                        # PCM16 samples are 2 bytes. Only pass whole samples on; an odd
                        # leftover byte waits for the next chunk so the browser never
                        # gets a half sample.
                        even = len(pending) - (len(pending) % 2)
                        if even:
                            loop.call_soon_threadsafe(queue.put_nowait, pending[:even])
                            pending = pending[even:]
                    # A final odd byte is padded with a zero byte to complete the last
                    # sample.
                    if pending and not stop.is_set():
                        loop.call_soon_threadsafe(queue.put_nowait, pending + b"\x00")
                    if stop.is_set():
                        session.close()
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, _clean(exc, seconds))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        # Daemon thread: it must not keep the process alive at shutdown.
        threading.Thread(target=worker, name="pingo-tts", daemon=True).start()
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        # Runs on normal end and when the consumer stops early (barge-in): tell the
        # worker to stop and cancel any audio still being generated.
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
        """Hold the config for one voice. The session is opened lazily by _ensure()."""
        self.api_key = api_key
        self.config = config
        self.session = None
        self.lock = threading.Lock()

    def _ensure(self):
        """Return (open session, fresh?), connecting or reconnecting if needed.

        fresh=True means a new connection was just made, which the caller uses to
        decide whether a failure is worth one retry. Call with self.lock held.
        """
        session = self.session
        if session is not None and not _ended(session):
            return session, False
        if session is None:
            aai.settings.tts_base_url = TTS_URL
            # inactivity_timeout=120: keep the socket open between turns for two
            # minutes.
            session = aai.TTSSession(config=self.config, api_key=self.api_key, inactivity_timeout=120)
            self.session = session
        session.connect()  # also reconnects a session the API closed for inactivity
        return session, True

    def close(self):
        """Close the session (ignoring errors) so the next use reconnects."""
        session, self.session = self.session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def _ended(session):
    """True if the SDK session's socket is gone or it was stopped (peeks at private SDK fields)."""
    stopped = getattr(session, "_stopped", None)
    return getattr(session, "_ws", None) is None or (stopped is not None and stopped.is_set())


class VoicePool:
    """Per-connection TTS: one long-lived session per voice. Same stream() as GatewayTTS."""

    def __init__(self, owner):
        """Start empty; a channel per language is created on first use."""
        self.owner = owner
        self.channels = {}

    def _channel(self, language_name):
        """The channel for a language, created (not yet connected) the first time."""
        if language_name not in self.channels:
            self.channels[language_name] = _Channel(self.owner.api_key, self.owner.config_for(language_name))
        return self.channels[language_name]

    async def stream(self, text, language_name, meta):
        """Speak `text` on the language's long-lived session; async-yield PCM16 chunks.

        Same contract as GatewayTTS.stream (meta gets sample_rate, billed_chars and
        reused_session). If a reused session turns out to be closed by the API, it
        reconnects and retries once, but only if no audio was sent yet (so the
        learner never hears a phrase twice).
        """
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
            """Thread body: speak on the channel (one segment at a time per voice), queue chunks.

            The channel lock serialises segments that use the same voice. Always ends by
            queueing None; errors close the channel and are queued as TTSError.
            """
            pending = b""
            try:
                # Only one segment at a time per voice; a cancelled request that was
                # still waiting gives up here.
                with channel.lock:
                    if stop.is_set():
                        return
                    # At most two attempts: the second only after a stale reused session
                    # failed.
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
                            # Do not retry a brand-new session, a segment that already
                            # sent audio, or a second failure.
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
            # Consumer stopped (end or barge-in): the worker sees this and cancels the
            # speech.
            stop.set()

    async def warm(self, language_names):
        """Open the sessions before the first turn so its audio does not wait on a handshake."""

        def connect(channel):
            """Connect one channel; errors are swallowed and surface on first real use."""
            with channel.lock:
                try:
                    channel._ensure()
                except Exception:
                    channel.close()  # the first real segment reports the error

        await asyncio.gather(
            # dict.fromkeys removes duplicate names (native and target voice can be the
            # same language) while keeping order.
            *(asyncio.to_thread(connect, self._channel(name)) for name in dict.fromkeys(language_names))
        )

    async def aclose(self):
        """Close every per-voice session (when the browser socket goes away)."""
        channels, self.channels = list(self.channels.values()), {}
        for channel in channels:
            await asyncio.to_thread(channel.close)


def _clean(exc, seconds):
    """Wrap any SDK error as a TTSError with a short, learner-readable message."""
    text = str(exc)
    if "timeout" in text.lower() or "timed out" in text.lower():
        return TTSError(f"Speech synthesis timed out after {int(seconds)} seconds.")
    return TTSError(f"Speech synthesis failed: {text}")


def audio_ms(nbytes, sample_rate):
    """Duration in ms of `nbytes` of PCM16 mono audio at `sample_rate` (2 bytes per sample)."""
    if not sample_rate:
        return 0
    return int(nbytes / 2 / sample_rate * 1000)
