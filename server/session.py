"""One learner session: the heart of the Pingo backend.

The WebSocket server (server/app.py) and file mode (python -m server --input
clip.wav, see server/__main__.py) both create one Session per learner and feed
it the same two things: JSON control messages and raw 16 kHz PCM16 mic audio.

Where this file sits in the pipeline:

    browser mic audio
      -> Session.handle_audio -> AssemblyAI streaming STT (server/stt.py)
      -> STT events (Begin / SpeechStarted / Turn) -> Session._on_stt
      -> final Turn -> Session._respond -> LLM tutor (server/llm.py)
      -> lesson JSON -> speech_plans() -> AssemblyAI TTS (server/tts.py)
      -> 24 kHz PCM + JSON messages -> browser (via the `send` callback)
      -> one JSONL line per final turn (server/log.py)

Key ideas you will meet below:

* state: "idle" -> "listening" -> "thinking" -> "speaking" -> "listening".
  The browser shows it; barge-in only happens while the tutor is audible.
* generation: a counter bumped every time the current reply must die (new
  final turn, barge-in, replay, close). Each reply task remembers the
  generation it started with and stops as soon as it no longer matches.
* playback_until: TTS arrives faster than real time, so the server is done
  sending long before the browser is done playing. This wall-clock estimate of
  when playback ends lets a learner still interrupt ("barge in") the tail.
* practice loop: after Pingo teaches a phrase, the next learner turn is
  checked against it (server/practice.py) and judged correct / close / try
  again; after two misses the phrase is read slowly, syllable by syllable.
* auto-target mode: the learner does not pick a language up front; the LLM
  says which language each turn asks for and this file checks the answer.
"""

import asyncio
import logging
import time
import uuid

from server.audio import TARGET_RATE
from server.config import (
    STT_LANGUAGES,
    TARGET_LANGUAGES,
    log_config,
    merge_session_config,
    normalize_target,
    target_name,
)
from server.log import write_turn
from server.llm import error_strings
from server.practice import (
    CLOSE_AT,
    check_attempt,
    is_native_phrase,
    is_other_script,
    looks_like_attempt,
    match_score,
    slow_text,
)
from server.stt import UPDATABLE, StreamingSTT, stt_query_params, update_configuration_message
from server.tts import TTSError, audio_ms

logger = logging.getLogger("pingo.session")

# SpeechStarted while the tutor is working means the learner talked over it.
BUSY = ("thinking", "speaking")
# TTS arrives faster than real time, so the server finishes sending a reply
# seconds before the browser finishes playing it. Pad the estimated playback end
# for network and scheduling delay.
PLAYBACK_PAD_MS = 400
# How many learner/tutor exchanges of chat history the LLM sees (older ones are
# dropped).
HISTORY_TURNS = 6
# Silence between spoken segments (reply, phrase, prompt), in milliseconds.
GAP_MS = 250
REPLAY_MODES = ("phrase", "slow", "all")
# A learner turn this long or longer, outside practice, sets the learner's own language.
NATIVE_MIN_WORDS = 3
# A near-miss "attempt" STT tags this confidently as another language is
# that language (French, Italian...), not a try at the practice phrase.
FOREIGN_ATTEMPT_CONFIDENCE = 0.5
# What a `start` may send as target_language in auto-target mode.
AUTO_TARGET_VALUES = (None, "", "auto")

# Auto-target mode: said in the learner's language when Pingo cannot tell which
# language they want, or when they ask for one TTS cannot speak.
ASK_TARGET = {
    "en": "Which language would you like to learn that in? I can teach English, Spanish, French, German, Italian or Portuguese.",
    "es": "¿En qué idioma te gustaría aprenderlo? Puedo enseñarte inglés, español, francés, alemán, italiano o portugués.",
    "fr": "Dans quelle langue veux-tu l'apprendre ? Je peux t'apprendre l'anglais, l'espagnol, le français, l'allemand, l'italien ou le portugais.",
    "de": "In welcher Sprache möchtest du das lernen? Ich kann dir Englisch, Spanisch, Französisch, Deutsch, Italienisch oder Portugiesisch beibringen.",
    "it": "In quale lingua vuoi impararlo? Posso insegnarti inglese, spagnolo, francese, tedesco, italiano o portoghese.",
    "pt": "Em que idioma você quer aprender isso? Posso ensinar inglês, espanhol, francês, alemão, italiano ou português.",
}
UNSUPPORTED_TARGET = {
    "en": "Sorry, I can't teach that language yet. I can teach English, Spanish, French, German, Italian or Portuguese.",
    "es": "Lo siento, todavía no puedo enseñar ese idioma. Puedo enseñarte inglés, español, francés, alemán, italiano o portugués.",
    "fr": "Désolé, je ne peux pas encore enseigner cette langue. Je peux t'apprendre l'anglais, l'espagnol, le français, l'allemand, l'italien ou le portugais.",
    "de": "Diese Sprache kann ich leider noch nicht unterrichten. Ich kann dir Englisch, Spanisch, Französisch, Deutsch, Italienisch oder Portugiesisch beibringen.",
    "it": "Mi dispiace, non posso ancora insegnare quella lingua. Posso insegnarti inglese, spagnolo, francese, tedesco, italiano o portoghese.",
    "pt": "Desculpe, ainda não posso ensinar esse idioma. Posso ensinar inglês, espanhol, francês, alemão, italiano ou português.",
}
# Names the model may return instead of a code for a language TTS cannot speak.
OTHER_LANGUAGE_NAMES = {
    "japanese", "chinese", "mandarin", "cantonese", "korean", "hindi", "arabic", "russian",
    "turkish", "dutch", "swedish", "norwegian", "danish", "finnish", "hebrew", "persian",
    "farsi", "urdu", "marathi", "vietnamese", "romanian", "catalan", "galician", "estonian",
    "afrikaans", "xhosa", "zulu", "polish", "greek", "thai", "indonesian", "ukrainian",
}


def resolve_auto_target(value, previous):
    """The model's target_language for this turn, checked.

    Returns (target code or None, unsupported). One of the six codes or names
    wins. Empty, missing or junk keeps the previous target. A real language TTS
    cannot speak (e.g. "ja") keeps the previous target and is flagged.
    """
    text = str(value or "").strip().lower()
    if not text:
        return previous, False
    try:
        return normalize_target(text), False
    except ValueError:
        pass
    # Not one of the six teachable languages, but it looks like a real language (a known
    # STT code, a known name, or a 2-3 letter code): keep the old target and flag it as
    # unsupported so the learner hears why.
    if text in STT_LANGUAGES or text in OTHER_LANGUAGE_NAMES or (text.isalpha() and len(text) in (2, 3)):
        return previous, True
    return previous, False


def now_ms():
    """Current wall-clock time in integer milliseconds (used for timings and playback_until)."""
    return int(time.time() * 1000)


def word_view(word):
    """Copy just the fields the browser and the log need from one STT word.

    Input: a word dict from an AssemblyAI Turn message. Output: a new dict with
    text, start/end (ms into the stream), confidence and word_is_final (forced
    to a real bool). Missing fields become None/"" instead of raising.
    """
    return {
        "text": word.get("text", ""),
        "start": word.get("start"),
        "end": word.get("end"),
        "confidence": word.get("confidence"),
        "word_is_final": bool(word.get("word_is_final", False)),
    }


class Session:
    """State and logic for one learner connection.

    Created with the merged settings, the AssemblyAI API key and an async `send`
    callback that delivers a dict (JSON) or bytes (audio) to the browser. The
    STT/LLM/TTS clients are injectable so the unit tests can use fakes.
    Public entry points: handle_text(), handle_audio(), close(), shutdown().
    """
    def __init__(
        self,
        settings,
        api_key,
        send,
        *,
        case_id="",
        session_id="",
        stt_factory=None,
        llm=None,
        tts=None,
        log_path=None,
    ):
        """Set up an idle session. Nothing is opened until a `start` message arrives.

        Inputs:
          settings: the merged config dict (see server/config.py).
          api_key: AssemblyAI key, kept server-side only.
          send: async callable(payload) that writes to the browser socket.
          case_id / session_id: labels copied into every log line (evals use case_id).
          stt_factory: builds the streaming STT client; defaults to StreamingSTT.
          llm / tts: the tutor and speech clients (fakes in tests).
          log_path: overrides settings["log"]["path"]; "" disables logging.
        """
        self.base_settings = settings
        self.settings = settings
        self.api_key = api_key
        self.send = send
        self.case_id = case_id or ""
        self.session_id = session_id or str(uuid.uuid4())
        # Default: the real AssemblyAI streaming client. Tests pass a fake factory.
        self.stt_factory = stt_factory or (
            lambda key, params, on_event: StreamingSTT(key, params, on_event)
        )
        self.llm = llm
        self.tts = tts
        # One open TTS session per voice for this connection, when the engine supports it.
        self.voices = tts.pool() if hasattr(tts, "pool") else tts
        self.log_path = settings["log"]["path"] if log_path is None else log_path
        self.target = settings.get("target_language")
        # Auto-target mode: no language picked up front; each turn says which one.
        self.auto_target = False
        # STT connection and what we asked it for / what it confirmed in Begin.
        self.stt = None
        self.stt_query = {}
        self.stt_session_id = None
        self.begin_configuration = None
        # self.job is the task answering the current turn (or replaying).
        # self.generation is bumped whenever that task must stop; a task compares its
        # own copy with _alive(). self.state is idle / listening / thinking / speaking.
        self.job = None
        self.generation = 0
        self.state = "idle"
        # Wall-clock ms when the browser should finish playing what we sent.
        self.playback_until = 0
        # turn_order of the final turn the tutor is currently answering.
        self.answering_order = None
        # Stored lessons by turn_id (for replay), and partial transcripts for the turn
        # in progress (kept for the log).
        self.lessons = {}
        self.partials = []
        self.partial_first = None
        self.partial_turn = None
        # Audio bookkeeping: bytes sent to STT, bytes already billed to written log
        # lines, and the wall-clock ms the first chunk arrived (to turn word times into
        # wall-clock times). _pending_audio holds chunks that arrive before STT is
        # connected.
        self.audio_bytes = 0
        self.audio_accounted = 0
        self.origin_wall_ms = None
        self._pending_audio = []
        self._stt_ready = False
        # Background tasks (voice warm-up) that close() must cancel.
        self.background = set()
        # Chat history the LLM sees: alternating user/assistant messages.
        self.history = []
        # The phrase the learner is currently practising (in the target language).
        self.practice_target = ""
        # Respelling and syllables taught with the practice phrase. Attempts and
        # breakdowns reuse them instead of trusting the model to repeat them.
        self.practice_card = {}
        # Misses on the current practice phrase. After two, the phrase is read slowly.
        self.misses = 0
        # Learner's own language. Defaults to English; updated from `start` and from
        # longer confident turns (_note_native).
        self.native_language = "en"
        # Turn ids: STT's turn_order restarts on every new STT stream, so we keep our
        # own climbing id. _order_ids maps the current stream's turn_order to it.
        self._order_ids = {}
        self._stt_epoch = 0
        self._turn_seq = 0
        self._turn_key = None
        self._turn_id = 0
        # Finished log records waiting to be written (see _hold).
        self.ready = []
        self.closed = False
        self.turns_logged = 0

    async def handle_text(self, message):
        """Route one JSON control message from the browser by its "type".

        Message types: start (open STT and begin listening), update_config (change
        STT settings mid-stream), set_target (switch the language being learned),
        replay (speak a stored lesson again), stop (close the session). Anything
        else is answered with an error message rather than ignored silently.
        """
        kind = message.get("type")
        if kind == "start":
            await self._start(message)
        elif kind == "update_config":
            await self._update_config(message.get("config") or {})
        elif kind == "set_target":
            await self._set_target(message.get("target_language"))
        elif kind == "replay":
            await self._replay(message)
        elif kind == "stop":
            await self.close()
        else:
            await self._error(f"Unknown message type: {kind}")

    async def handle_audio(self, pcm: bytes, arrived_at=None):
        """Accept one chunk of 16 kHz PCM16 mic audio from the browser.

        Input: raw bytes and, for live sockets, the wall-clock ms they arrived.
        Side effects: records when audio started (so word timestamps can be turned
        into wall-clock times) and either forwards the chunk to STT or, if STT is
        still connecting, buffers it so the first syllables are not lost.
        """
        if self.closed or not pcm:
            return
        # Live frames pass the time they were read off the socket. File mode
        # omits it and stamps here, which matches real-time playback.
        if self.origin_wall_ms is None:
            self.origin_wall_ms = now_ms() if arrived_at is None else int(arrived_at)
        if not self._stt_ready or self.stt is None:
            self._pending_audio.append(pcm)
            return
        await self._forward_audio(pcm)

    async def _forward_audio(self, pcm: bytes):
        """Send one chunk to STT and count its bytes (used later to bill audio seconds per turn)."""
        self.audio_bytes += len(pcm)
        await self.stt.send_audio(pcm)

    async def _flush_pending_audio(self):
        """Send audio that arrived while STT was still connecting, oldest first.

        Stops early if STT was closed in the meantime.
        """
        pending = self._pending_audio
        self._pending_audio = []
        for pcm in pending:
            if self.stt is None:
                return
            await self._forward_audio(pcm)

    async def close(self):
        # Already fully closed: nothing to do.
        """Stop everything for this session but keep the TTS voices for a later `start`.

        Side effects: cancels background tasks and the current reply, bumps the
        generation so any in-flight reply stops, closes STT, writes any log lines
        still held in memory, clears the practice history and goes back to idle.
        Safe to call twice: the early return makes a second call a no-op.
        """
        if self.closed and self.stt is None and not self.ready and self.job is None:
            return
        for task in list(self.background):
            task.cancel()
        self.closed = True
        # Bump the generation so any in-flight reply stops sending.
        self.generation += 1
        await self._cancel_job()
        stt = self.stt
        self.stt = None
        if stt is not None:
            await stt.close()
        # Write every held log line, giving the last one any trailing audio.
        self._flush_ready(final=True)
        self._forget()
        self.state = "idle"

    async def shutdown(self):
        """The socket is gone: close, then hang up the per-voice TTS sessions."""
        await self.close()
        if hasattr(self.voices, "aclose"):
            await self.voices.aclose()

    async def _start(self, message):
        # A second `start` on the same socket: tear down the old stream first.
        """Handle a `start` message: validate it, open streaming STT, start listening.

        Reads target_language (or auto-target mode), optional case_id/session_id,
        the learner's native_language and per-session config overrides. Any bad
        value is reported to the browser as an error and the session stays idle.
        On success the session status becomes "listening" and the TTS voices are
        warmed up in the background so the first reply starts faster.
        """
        if self.stt is not None or self.state != "idle":
            await self._replace_upstream()
        self.closed = False
        self.auto_target = bool(message.get("auto_target"))
        raw_target = message.get("target_language")
        try:
            # Auto-target mode with no language given (None, '' or 'auto').
            if self.auto_target and (raw_target is None or str(raw_target).strip().lower() in AUTO_TARGET_VALUES):
                # The learner names the language when they speak.
                self.target = None
            else:
                self.target = normalize_target(raw_target or self.base_settings.get("target_language"))
        except ValueError as exc:
            await self._error(str(exc))
            return
        if message.get("case_id"):
            self.case_id = str(message["case_id"])
        if message.get("session_id"):
            self.session_id = str(message["session_id"])
        native = str(message.get("native_language") or "").strip().lower()
        # Only accept a native language Pingo can also speak; otherwise keep the current
        # one.
        if native in TARGET_LANGUAGES:
            self.native_language = native
        try:
            self.settings = merge_session_config(self.base_settings, message.get("config") or {})
        except ValueError as exc:
            await self._error(str(exc))
            return
        # Keep the resolved target in the settings so log lines record it.
        self.settings["target_language"] = self.target
        # Reject language hints STT does not know before opening a paid stream.
        bad_codes = [
            code for code in self.settings["stt"]["language_codes"] if code not in STT_LANGUAGES
        ]
        if bad_codes:
            await self._error("Unknown language code: " + ", ".join(bad_codes))
            return
        if not self.api_key:
            await self._error("ASSEMBLYAI_API_KEY is not set.")
            return
        # Build the STT connection query string from the session's STT settings.
        self.stt_query = stt_query_params(self.settings["stt"])
        # Fresh stream: reset audio accounting, partials and barge-in timing.
        self.audio_bytes = 0
        self.audio_accounted = 0
        self.origin_wall_ms = None
        self._pending_audio = []
        self._stt_ready = False
        self.begin_configuration = None
        self.stt_session_id = None
        self.partials = []
        self.partial_first = None
        self.answering_order = None
        self.playback_until = 0
        self._forget()
        # New STT stream: turn_order will restart from 0, so start a new epoch for turn
        # ids.
        self._stt_epoch += 1
        self.stt = self.stt_factory(self.api_key, self.stt_query, self._on_stt)
        try:
            await self.stt.connect()
        except Exception as exc:
            self.stt = None
            self._pending_audio = []
            await self._error(f"Could not open speech-to-text: {exc}")
            return
        # Now forward anything the learner said while we were connecting.
        self._stt_ready = True
        await self._flush_pending_audio()
        await self._status("listening")
        self._warm_voices()

    async def _replace_upstream(self):
        """A new `start` on the same socket closes the previous STT session."""
        self.generation += 1
        await self._cancel_job()
        self._flush_ready(final=True)
        stt = self.stt
        self.stt = None
        if stt is not None:
            await stt.close()
        self.state = "idle"

    async def _update_config(self, config):
        """Apply an `update_config` message mid-session.

        Merges the new values into the session settings (rejecting bad ones), then
        sends AssemblyAI an UpdateConfiguration message with only the fields that
        streaming STT allows changing on a live stream (UPDATABLE in server/stt.py).
        """
        try:
            self.settings = merge_session_config(self.settings, config)
        except ValueError as exc:
            # A bad value leaves the session settings as they were.
            await self._error(str(exc))
            return
        # Collect only the fields STT can change on a live stream, from either the top
        # level or a nested "stt" object.
        changes = {key: config[key] for key in UPDATABLE if key in config}
        nested = config.get("stt") if isinstance(config.get("stt"), dict) else {}
        for key in UPDATABLE:
            if key in nested:
                changes[key] = nested[key]
        if "agent_context_enabled" in config or "agent_context_enabled" in nested:
            # The flag itself is not an STT field. Re-send the stored text if it just turned on.
            if self.settings["stt"].get("agent_context_enabled") and self.settings["stt"].get("agent_context"):
                changes["agent_context"] = self.settings["stt"]["agent_context"]
        message = update_configuration_message(changes, self.settings["stt"])
        if message and self.stt is not None:
            await self.stt.send_json(message)
        # Remember the new settings for the next reconnect too.
        self.stt_query = stt_query_params(self.settings["stt"])

    async def _set_target(self, value):
        """Switch the language being learned (a `set_target` message).

        A different language clears the practice phrase and chat history, since
        they belonged to the old language. A bad code is reported as an error.
        """
        try:
            target = normalize_target(value)
        except ValueError as exc:
            await self._error(str(exc))
            return
        if target != self.target:
            self._forget()
        self.target = target
        self.settings["target_language"] = self.target

    async def _replay(self, message):
        """Speak a stored turn again without a second LLM call.

        Keyed by turn_id (turn_order restarts on every STT stream). An old
        client that only sends turn_order gets the latest turn with that order.
        mode: phrase (default, the target phrase), slow (syllables then the
        phrase) or all (the whole tutor turn).
        """
        # Very old clients sent just the turn number.
        if not isinstance(message, dict):
            message = {"turn_order": message}
        key = None
        # Prefer turn_id (unique for the whole session); fall back to the latest turn
        # with that turn_order on the current stream.
        for field in ("turn_id", "turn_order"):
            value = message.get(field)
            if value is None:
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            key = number if field == "turn_id" else self._order_ids.get(number)
            break
        if key is None:
            await self._error("Replay needs a turn_id.")
            return
        saved = self.lessons.get(key)
        if not saved:
            await self._error(f"No lesson stored for turn {key}.")
            return
        # Unknown modes fall back to "phrase". If the stored plan for the mode is empty,
        # fall back to whatever was stored.
        mode = message.get("mode") if message.get("mode") in REPLAY_MODES else "phrase"
        plan = saved.get(mode) or saved.get("phrase") or saved.get("all") or []
        if not plan:
            await self._error(f"Nothing to replay for turn {key}.")
            return
        # A replay cancels whatever is playing now, like a new reply would.
        self.generation += 1
        generation = self.generation
        await self._cancel_job()
        self.job = asyncio.create_task(self._speak_plan(plan, generation, None, key))

    async def _on_stt(self, message):
        """Handle one event from AssemblyAI streaming STT.

        Begin: remember the STT session id and the configuration it echoed back.
        SpeechStarted: the learner started talking (may trigger a barge-in).
        Turn: a partial or final transcript (see _on_turn).
        Error: forwarded to the browser as an error message.
        """
        if self.closed:
            return
        kind = message.get("type")
        if kind == "Begin":
            self.stt_session_id = message.get("id")
            self.begin_configuration = message.get("configuration")
            logger.info("STT session %s configuration %s", self.stt_session_id, self.begin_configuration)
        elif kind == "SpeechStarted":
            await self._on_speech_started()
        elif kind == "Turn":
            await self._on_turn(message)
        elif kind == "Error" or (kind is None and message.get("error")):
            text = message.get("error") or "Speech-to-text error."
            await self._error(text)
        # Heartbeat, Warning, Termination: nothing for the browser to render.

    def _agent_audible(self):
        """True while the tutor is thinking, sending audio, or the browser is still playing it."""
        return self.state in BUSY or now_ms() < self.playback_until

    async def _on_speech_started(self):
        """SpeechStarted from STT: interrupt the tutor only if it is currently audible."""
        if not self._agent_audible():
            return
        await self._barge_in()

    async def _barge_in(self):
        """The learner talked over the tutor: stop the reply and tell the browser to flush audio.

        Side effects: clears playback_until, bumps the generation (so the reply task
        stops at its next check), cancels the reply task, sends {"type": "barge_in"}
        so the browser drops queued audio, and goes back to "listening".
        """
        self.playback_until = 0
        self.generation += 1
        await self._cancel_job()
        await self._emit({"type": "barge_in"})
        await self._status("listening")

    async def _on_turn(self, turn):
        """Handle a Turn event: show the transcript, and answer it once it is final.

        Partials (end_of_turn false) only update the live transcript in the browser
        and are kept for the log. A final turn with words starts a new reply task
        (_respond) and cancels any older one. Real words from the learner while the
        tutor is audible also count as a barge-in (backup for SpeechStarted).
        """
        order = turn.get("turn_order")
        turn_id = self._turn_id_for(order)
        if order is not None:
            self._order_ids[order] = turn_id
        # A new turn_order means a new learner turn: start a fresh list of partials.
        if order != self.partial_turn:
            self.partial_turn = order
            self.partials = []
            self.partial_first = None
        view = {
            "transcript": turn.get("transcript") or "",
            "words": [word_view(word) for word in turn.get("words") or []],
        }
        # Backup for SpeechStarted: real words from the learner while Pingo is
        # still audible also stop it.
        # A repeat of the turn Pingo is already answering is not a new interruption.
        if view["transcript"].strip() and order != self.answering_order and self._agent_audible():
            await self._barge_in()
        # Partial (not final): remember it for the log; the first non-empty one is kept
        # separately to measure how early STT got it right.
        if not turn.get("end_of_turn"):
            if view["transcript"] and self.partial_first is None:
                self.partial_first = view["transcript"]
            self.partials.append(view)
        # Every partial and final is shown live in the browser.
        await self._emit(
            {
                "type": "transcript",
                "turn_id": turn_id,
                "turn_order": order,
                "end_of_turn": bool(turn.get("end_of_turn")),
                "transcript": view["transcript"],
                "language_code": turn.get("language_code"),
                "language_confidence": turn.get("language_confidence"),
                "words": view["words"],
            }
        )
        # Only a final turn with real words gets a tutor reply.
        if not turn.get("end_of_turn") or not view["transcript"].strip():
            return
        partials = list(self.partials)
        partial_first = self.partial_first
        self.partials = []
        self.partial_first = None
        # Stamp these here, not when the reply task runs. More audio can be
        # forwarded before that task is scheduled.
        self.answering_order = order
        audio_mark = self.audio_bytes
        received_ms = now_ms()
        # New reply: invalidate any older reply task, then start this one.
        self.generation += 1
        generation = self.generation
        await self._cancel_job()
        self.job = asyncio.create_task(
            self._respond(
                turn, view, partials, partial_first, generation, audio_mark, received_ms, turn_id
            )
        )

    async def _respond(self, turn, view, partials, partial_first, generation, audio_mark, received_ms, turn_id):
        """Answer one final learner turn: practice check, LLM tutor, TTS, log line.

        Steps: decide whether the turn is an attempt at the pending practice phrase,
        a new phrase in the learner's own language, or a phrase in some other
        language; ask the LLM for a lesson; correct the lesson with the practice
        check; send a `lesson` and an `agent_text` message; speak the plan; update
        history and STT agent context. Runs as a cancellable task: if the generation
        changes (barge-in, newer turn) it stops and the log line says barge_in.
        The log record is always handed to _hold() in the finally block.
        """
        record = self._new_record(turn, view, partials, partial_first, audio_mark, received_ms)
        try:
            if not self._alive(generation):
                record["barge_in"] = True
                return
            await self._status("thinking")
            transcript = view["transcript"]
            check = None
            new_phrase = False
            other_language = ""
            # Routing: is this an attempt at the pending practice phrase?
            # looks_like_attempt is a cheap text-similarity test (server/practice.py).
            if self.practice_target and looks_like_attempt(
                transcript, self.practice_target, self.target, self.native_language
            ):
                check = check_attempt(transcript, self.practice_target, self.target, view["words"])
                check["misses_before"] = self.misses
                if check["verdict"] != "correct" and self._spoken_elsewhere(turn):
                    # "Je voudrais une tasse de café" after "Quisiera una taza de
                    # café": spelled close, but it is French, not a try at the phrase.
                    logger.info("not an attempt: STT says %s", turn.get("language_code"))
                    check = None
            # Not a practice attempt: maybe learn the learner's own language from it,
            # and detect whether it is a new phrase to teach or a phrase in a third
            # language.
            if check is None:
                self._note_native(turn, transcript)
                # A pending practice phrase does not swallow a new phrase in the
                # learner's own language ("Good morning" while "Hola" is pending).
                new_phrase = is_native_phrase(transcript, self.practice_target, self.native_language)
                if not new_phrase:
                    other_language = self._other_language(turn, transcript)
            native = self._native_name()
            # Auto-target mode tells the LLM to also return which language the learner
            # wants, plus what it was last turn.
            extra = {"auto_target": True, "previous_target": self.target or ""} if self.auto_target else {}
            lesson = await self.llm.complete(
                transcript=transcript,
                source_language=turn.get("language_code"),
                language_confidence=turn.get("language_confidence"),
                target_name=target_name(self.target) if self.target else "",
                history=list(self.history),
                native_name=native,
                practice_target=self.practice_target,
                practice_check=check,
                new_phrase=new_phrase,
                other_language=other_language,
                **extra,
            )
            record["timings_ms"]["llm_first_token"] = _ms(lesson.first_token_at)
            record["timings_ms"]["llm_done"] = _ms(lesson.done_at)
            # Auto-target: check the model's language choice (may reset the practice
            # phrase or turn the reply into a canned question).
            if self.auto_target and not lesson.unparsed:
                check = self._apply_auto_target(lesson, check, record)
            # Let the deterministic practice check overrule the model's verdict and move
            # the practice phrase forward.
            if not lesson.unparsed:
                self._apply_practice(lesson, check, new_phrase)
            record["llm"] = {
                "model": lesson.model,
                "intent": getattr(lesson, "intent", None),
                "reply": getattr(lesson, "reply", ""),
                "translation": lesson.translation,
                "pronunciation": lesson.pronunciation,
                "syllables": getattr(lesson, "syllables", ""),
                "tip": lesson.tip,
                "verdict": getattr(lesson, "verdict", "none"),
                "next_prompt": getattr(lesson, "next_prompt", ""),
                "detected_errors": error_strings(lesson.detected_errors),
                "streamed": lesson.streamed,
                "retried": bool(getattr(lesson, "retried", False)),
                "salvaged": bool(getattr(lesson, "salvaged", False)),
            }
            # Routing label in the log: how this turn was classified before the LLM
            # call.
            record["practice"] = check
            record["routing"] = (
                "practice_attempt" if check
                else "new_phrase" if new_phrase
                else "other_language" if other_language
                else "model"
            )
            record["usage"]["llm_input_tokens"] = lesson.tokens_in
            record["usage"]["llm_output_tokens"] = lesson.tokens_out
            # The model's reply was not valid JSON: log the start of the raw text, tell
            # the browser, and do not speak it.
            if lesson.unparsed:
                record["llm"]["raw_text"] = (getattr(lesson, "raw_text", "") or "")[:500]
                await self._error("The tutor reply was not valid JSON, so it was not spoken.")
                lesson.tip = "Please try that phrase again."
                record["llm"]["tip"] = lesson.tip
            # Barged-in while the LLM was thinking: drop the reply.
            if not self._alive(generation):
                record["barge_in"] = True
                return
            order = turn.get("turn_order")
            # Turn the lesson into ordered speech segments. After MISSES_BEFORE_SLOW
            # misses, the phrase is read slowly first.
            plans = speech_plans(
                lesson,
                native,
                target_name(self.target) if self.target else native,
                slow_read=self.misses >= MISSES_BEFORE_SLOW,
            )
            # Store the plans so the play buttons (replay) can speak them again without
            # another LLM call.
            self.lessons[turn_id] = {
                "translation": lesson.translation,
                "target_language": self.target,
                "turn_id": turn_id,
                **plans,
            }
            message = {
                "type": "lesson",
                "turn_id": turn_id,
                "turn_order": order,
                "source_text": transcript,
                "source_language": turn.get("language_code"),
                "native_language": self.native_language,
                "target_language": self.target,
                "intent": getattr(lesson, "intent", None),
                "reply": getattr(lesson, "reply", ""),
                "target_phrase": lesson.translation,
                "translation": lesson.translation,
                "pronunciation": lesson.pronunciation,
                "syllables": getattr(lesson, "syllables", ""),
                "tip": lesson.tip,
                "verdict": getattr(lesson, "verdict", "none"),
                "verdict_reason": getattr(lesson, "verdict_reason", ""),
                "next_prompt": getattr(lesson, "next_prompt", ""),
                "detected_errors": lesson.detected_errors,
                "practice": check,
                "practice_target": self.practice_target,
            }
            if self.auto_target:
                message["auto_target"] = True
            await self._emit(message)
            # agent_text: the full text the tutor is about to say, for captions.
            spoken = " ".join(text for text, _lang, _kind in plans["all"])
            await self._emit(
                {"type": "agent_text", "turn_id": turn_id, "text": spoken or lesson.translation, "final": True}
            )
            if plans["all"] and not lesson.unparsed:
                await self._speak_plan(plans["all"], generation, record, turn_id)
            # Only a reply that was fully spoken becomes history and STT context.
            if not lesson.unparsed and not record["barge_in"] and self._alive(generation):
                self._remember(transcript, lesson)
                await self._push_agent_context(lesson.translation)
        # Cancelled by barge-in or a newer turn: mark it and let the cancellation
        # propagate.
        except asyncio.CancelledError:
            record["barge_in"] = True
            raise
        # Any other failure: tell the browser and go back to listening if this reply is
        # still current.
        except Exception as exc:
            logger.exception("turn failed")
            await self._error(str(exc))
            if self._alive(generation):
                await self._status("listening")
        finally:
            # Always log the turn, marking it as barged-in if it was superseded.
            if not self._alive(generation):
                record["barge_in"] = True
            self._hold(record)

    def _new_record(self, turn, view, partials, partial_first, audio_mark, received_ms):
        """Build the log record (one future JSONL line) for a final turn.

        It starts with the STT result, the partials, timing stamps and empty usage
        counters; _respond fills in the LLM and TTS parts as they happen. The
        private "_audio_mark" key is removed before the line is written.
        """
        return {
            "case_id": self.case_id or None,
            "session_id": self.session_id,
            "turn_order": turn.get("turn_order"),
            "config": log_config(self.settings),
            "stt": {
                "transcript": view["transcript"],
                "words": view["words"],
                "language_code": turn.get("language_code"),
                "language_confidence": turn.get("language_confidence"),
                "end_of_turn_confidence": turn.get("end_of_turn_confidence"),
            },
            "stt_partial_first": partial_first,
            "stt_partials": partials,
            "stt_session_id": self.stt_session_id,
            "end_of_turn_reason": turn.get("end_of_turn_reason"),
            "llm": {"model": self.settings["llm"].get("model")},
            "barge_in": False,
            "timings_ms": {
                "speech_end": self._speech_end_ms(view["words"]),
                "stt_final": received_ms,
                "llm_first_token": None,
                "llm_done": None,
                "tts_first_audio": None,
            },
            "usage": {
                "stt_audio_seconds": 0.0,
                "llm_input_tokens": None,
                "llm_output_tokens": None,
                "tts_characters": 0,
            },
            # Bytes forwarded to STT by the time this final arrived.
            "_audio_mark": audio_mark,
        }

    def _turn_id_for(self, turn_order):
        """One id per learner turn. It keeps climbing across STT reconnects."""
        # STT turn_order restarts on reconnect, so pair it with the stream epoch.
        key = (self._stt_epoch, turn_order)
        if key != self._turn_key:
            self._turn_seq += 1
            self._turn_key = key
            self._turn_id = self._turn_seq
        return self._turn_id

    def _forget(self):
        """Clear the tutor's memory: chat history, practice phrase, its card and miss count."""
        self.history.clear()
        self.practice_target = ""
        # Respelling and syllables taught with the practice phrase. Attempts and
        # breakdowns reuse them instead of trusting the model to repeat them.
        self.practice_card = {}
        # Misses on the current practice phrase. After two, the phrase is read slowly.
        self.misses = 0

    def _warm_voices(self):
        """Open the TTS sessions for the learner's and target voices in the background.

        Opening a TTS session takes a round trip, so doing it at `start` makes the
        first spoken reply faster. Skipped if the TTS client has no warm() method.
        The task is tracked in self.background so close() can cancel it.
        """
        warm = getattr(self.voices, "warm", None)
        if warm is None:
            return
        names = [self._native_name()] + ([target_name(self.target)] if self.target else [])
        task = asyncio.create_task(warm(names))
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    def _native_name(self):
        """The learner's own language as a TTS language name (e.g. "english")."""
        return TARGET_LANGUAGES.get(self.native_language, "english")

    def _note_native(self, turn, transcript):
        """The learner's own language: what they speak when they are not practising."""
        code = turn.get("language_code")
        confidence = turn.get("language_confidence")
        # Only switch to a teachable language that is not the target itself.
        if code not in TARGET_LANGUAGES or code == self.target:
            return
        # Short turns ("Hola", "merci") are usually practice, not the learner's own
        # language.
        if len(transcript.split()) < NATIVE_MIN_WORDS:
            return
        # Low-confidence language detection is too noisy to change the native language.
        if isinstance(confidence, (int, float)) and confidence < 0.6:
            return
        self.native_language = code

    def _other_language(self, turn, transcript):
        """A turn in some language other than the learner's own or the target.

        Returns the STT language code (or "other") when the turn should be
        translated and taught, else "". Such a turn is never something Pingo
        "didn't catch". Non-Latin script decides on its own (all six languages
        Pingo speaks are Latin script). Otherwise any STT code outside the
        learner's language and the target counts, whatever the confidence:
        live, short turns and French/Italian/Portuguese came back with low
        confidence, and a wrong flag only adds a routing hint the model can
        still overrule for meta talk.
        """
        code = turn.get("language_code") or ""
        if is_other_script(transcript):
            return code if code and code not in TARGET_LANGUAGES else "other"
        if not code or code in (self.target, self.native_language):
            return ""
        return code

    def _spoken_elsewhere(self, turn):
        """STT is fairly sure this turn is not the target or the learner's language."""
        code = turn.get("language_code")
        confidence = turn.get("language_confidence")
        if not code or code in (self.target, self.native_language):
            return False
        return isinstance(confidence, (int, float)) and confidence >= FOREIGN_ATTEMPT_CONFIDENCE

    def _apply_auto_target(self, lesson, check, record):
        """Auto-target mode: take this turn's target from the model, checked.

        A new target resets the practice phrase like set_target does. With no
        target yet, or one TTS cannot speak, the turn becomes a chat that says
        which languages Pingo teaches. Returns the practice check still valid.
        """
        previous = self.target
        target, unsupported = resolve_auto_target(getattr(lesson, "target_language", ""), previous)
        record["auto_target"] = {
            "model": getattr(lesson, "target_language", ""),
            "previous": previous,
            "resolved": target,
            "unsupported": unsupported,
        }
        # The model chose a different target language this turn.
        if target != previous:
            logger.info("auto target: %s -> %s", previous, target)
            if previous is not None:
                self._forget()
            # The check was against the old language's phrase.
            check = None
            self.target = target
            self.settings["target_language"] = target
        # No usable target yet (or an unspeakable one): replace the lesson with a canned
        # chat reply in the learner's language.
        if unsupported or self.target is None:
            canned = UNSUPPORTED_TARGET if unsupported else ASK_TARGET
            lesson.intent = "chat"
            lesson.reply = canned.get(self.native_language, canned["en"])
            lesson.translation = ""
            lesson.pronunciation = ""
            lesson.syllables = ""
            lesson.tip = ""
            lesson.verdict = "none"
            lesson.verdict_reason = ""
            lesson.next_prompt = ""
            lesson.detected_errors = []
            return None
        return check

    def _apply_practice(self, lesson, check, new_phrase=False):
        """Ground the verdict in the practice check and move the practice phrase."""
        intent = getattr(lesson, "intent", None)
        if check is None and new_phrase and intent in ("practice_attempt", "chat") and lesson.translation:
            # A new phrase in the learner's own language is taught, never judged
            # or answered as small talk.
            logger.info("new %s phrase: %s -> teach_phrase", self._native_name(), intent)
            lesson.intent = intent = "teach_phrase"
            lesson.verdict = "none"
            lesson.verdict_reason = ""
        # A pronunciation breakdown after a miss is still a judged attempt: the
        # verdict badge stays, and the slow read comes from the miss count.
        if check is not None and intent != "teach_phrase":
            if intent != "practice_attempt" or lesson.verdict != check["verdict"]:
                logger.info(
                    "practice check overrides the model: %s/%s -> practice_attempt/%s",
                    intent,
                    getattr(lesson, "verdict", None),
                    check["verdict"],
                )
                # The model judged differently from the check: for English learners swap
                # in the canned verdict line so the spoken reply matches the badge.
                if lesson.verdict not in ("none", check["verdict"]) and self.native_language == "en":
                    lesson.reply = CANNED_VERDICT[check["verdict"]]
                    lesson.verdict_reason = check["reason"]
            lesson.intent = "practice_attempt"
            lesson.verdict = check["verdict"]
            if not lesson.verdict_reason:
                lesson.verdict_reason = check["reason"]
            if not lesson.reply:
                lesson.reply = CANNED_VERDICT[check["verdict"]]
            if not lesson.translation:
                lesson.translation = check["target"]
            # A correct attempt has no errors to show, whatever the model said.
            if check["verdict"] == "correct":
                lesson.detected_errors = []
        self._pin_practice_phrase(lesson, check)
        # Teaching a new phrase (or nothing pending yet): it becomes the phrase to
        # practise, with its respelling and syllables. A different phrase resets the
        # miss count.
        if lesson.translation and (lesson.intent != "practice_attempt" or not self.practice_target):
            if lesson.translation != self.practice_target:
                self.misses = 0
                self.practice_card = {}
            self.practice_target = lesson.translation
            self.practice_card = {
                "pronunciation": lesson.pronunciation or self.practice_card.get("pronunciation", ""),
                "syllables": getattr(lesson, "syllables", "") or self.practice_card.get("syllables", ""),
            }
        # Count misses on this phrase; a correct attempt resets the count.
        if lesson.intent == "practice_attempt":
            self.misses = 0 if lesson.verdict == "correct" else self.misses + 1

    def _pin_practice_phrase(self, lesson, check):
        """Attempts and breakdowns are about the stored phrase, not what the model echoes.

        Seen live: a correct attempt at "Hola" came back with target_phrase
        "Hola. corticosteroids". A judged attempt always uses the stored phrase.
        A model-labelled attempt or a pronunciation turn uses it when the model's
        phrase is empty or close to it; a clearly different phrase is a new one.
        """
        if not self.practice_target or lesson.intent not in ("practice_attempt", "pronunciation"):
            return
        phrase = lesson.translation or ""
        # Treat the model's phrase as the stored phrase when this is a judged attempt,
        # when it is empty, or when it is close enough (match_score >= CLOSE_AT).
        same = (
            (lesson.intent == "practice_attempt" and check is not None)
            or not phrase
            or match_score(phrase, self.practice_target, self.target) >= CLOSE_AT
        )
        if not same:
            return
        if phrase != self.practice_target:
            logger.info("pinning the practice phrase: %r -> %r", phrase, self.practice_target)
        lesson.translation = self.practice_target
        if self.practice_card.get("pronunciation"):
            lesson.pronunciation = self.practice_card["pronunciation"]
        if self.practice_card.get("syllables"):
            lesson.syllables = self.practice_card["syllables"]

    def _remember(self, transcript, lesson):
        """Add this turn to the short chat history the LLM sees next time.

        The user side is the transcript; the assistant side is a compact summary of
        what the tutor said (intent/verdict, reply, phrase, tip, next prompt). Only
        the last HISTORY_TURNS exchanges are kept so prompts stay small and cheap.
        """
        phrase = getattr(lesson, "translation", "") or ""
        reply = getattr(lesson, "reply", "") or ""
        parts = []
        intent = getattr(lesson, "intent", None)
        verdict = getattr(lesson, "verdict", "none")
        if intent:
            parts.append(f"[{intent}{'' if verdict in (None, 'none') else ': ' + verdict}]")
        if reply:
            parts.append(reply)
        if phrase:
            parts.append(f"Phrase: {phrase}")
        tip = getattr(lesson, "tip", "") or ""
        if tip:
            parts.append(f"Tip: {tip}")
        next_prompt = getattr(lesson, "next_prompt", "") or ""
        if next_prompt:
            parts.append(next_prompt)
        # Older schema with no reply/intent: just the phrase and tip.
        spoken = "\n".join(parts) if (reply or intent) else f"{phrase}\n{tip}".strip()
        self.history.append({"role": "user", "content": transcript})
        self.history.append({"role": "assistant", "content": spoken})
        # Trim to the last HISTORY_TURNS exchanges (2 messages each).
        extra = len(self.history) - HISTORY_TURNS * 2
        if extra > 0:
            del self.history[:extra]

    async def _speak_plan(self, segments, generation, record, turn_id=None):
        """Speak segments in order, each in its own voice, as one tts_start..tts_end.

        Every segment's TTS session starts at once so the second voice is ready
        when the first one finishes. Audio is still sent in order, with a short
        silence between segments.
        """
        # Drop empty segments and cap each at 300 characters (TTS cost and latency).
        segments = [(text.strip()[:300], lang, kind) for text, lang, kind in segments if text and text.strip()]
        if not segments or not self._alive(generation):
            return
        # Overall timeout: the configured TTS timeout plus 10 s for each extra segment.
        sample_rate = int(self.settings["tts"].get("sample_rate") or 24000)
        seconds = float(self.settings["tts"].get("timeout_s") or 30) + 10 * (len(segments) - 1)
        # GAP_MS of silence as PCM16 zero samples (2 bytes each).
        gap = b"\x00\x00" * int(sample_rate * GAP_MS / 1000)
        # One metadata dict and one queue per segment; pump() fills them concurrently.
        metas = [{} for _ in segments]
        queues = [asyncio.Queue() for _ in segments]
        total = 0

        async def pump(text, language_name, queue, meta):
            """Stream one segment's TTS audio into its queue; always end with None.

            An error is put on the queue (not raised here) so the sender loop can raise
            it in order. Cancellation is re-raised so the task really stops.
            """
            try:
                async for chunk in self.voices.stream(text, language_name, meta):
                    queue.put_nowait(chunk)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                queue.put_nowait(exc)
            finally:
                queue.put_nowait(None)

        # tts_start tells the browser the sample rate and what is about to be spoken.
        await self._status("speaking")
        await self._emit(
            {
                "type": "tts_start",
                "turn_id": turn_id,
                "sample_rate": sample_rate,
                "segments": [{"kind": kind, "language": lang, "text": text} for text, lang, kind in segments],
            }
        )
        # Start every segment's TTS at once so later voices are ready in time; the loop
        # below still sends audio strictly in segment order.
        tasks = [
            asyncio.create_task(pump(text, lang, queue, meta))
            for (text, lang, _kind), queue, meta in zip(segments, queues, metas)
        ]
        play_start = now_ms()
        try:
            async with asyncio.timeout(seconds):
                stopped = False
                for index, queue in enumerate(queues):
                    if stopped:
                        break
                    # Silence between segments (not before the first, and only if audio
                    # was sent).
                    if index and total:
                        total += len(gap)
                        await self._emit(gap)
                    while True:
                        item = await queue.get()
                        if item is None:
                            break
                        if isinstance(item, Exception):
                            raise item
                        # Barge-in or a newer turn: stop sending immediately.
                        if not self._alive(generation):
                            if record is not None:
                                record["barge_in"] = True
                            stopped = True
                            break
                        if record is not None and record["timings_ms"]["tts_first_audio"] is None:
                            # First byte this process got back from TTS, before the browser plays it.
                            record["timings_ms"]["tts_first_audio"] = now_ms()
                        total += len(item)
                        # Send the chunk and push forward the estimate of when the
                        # browser will finish playing everything sent so far (plus
                        # PLAYBACK_PAD_MS of slack). Barge-in stays possible until then.
                        await self._emit(item)
                        self.playback_until = play_start + audio_ms(total, sample_rate) + PLAYBACK_PAD_MS
        except TimeoutError as exc:
            raise TTSError(f"Speech synthesis timed out after {int(seconds)} seconds.") from exc
        finally:
            # Whatever happened, stop the TTS streams, record usage and close the
            # tts_start..tts_end bracket so the browser is never left waiting.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if record is not None:
                # Bill the characters TTS reports, falling back to the text length.
                rate = next((m["sample_rate"] for m in metas if m.get("sample_rate")), sample_rate)
                chars = sum(
                    m["billed_chars"] if m.get("billed_chars") is not None else len(text)
                    for m, (text, _l, _k) in zip(metas, segments)
                )
                record["usage"]["tts_characters"] = chars
                record["tts_audio_ms"] = audio_ms(total, rate)
                record["tts_segments"] = [kind for _t, _l, kind in segments]
            await self._emit({"type": "tts_end", "turn_id": turn_id})
            if self._alive(generation):
                await self._status("listening")

    async def _push_agent_context(self, spoken):
        """Tell streaming STT what the tutor just said (when agent_context is enabled).

        AssemblyAI can use the agent's last utterance as context to transcribe the
        learner's reply better (e.g. a repeated foreign phrase). Capped at 1750
        characters to stay within the API limit.
        """
        stt_cfg = self.settings["stt"]
        if not stt_cfg.get("agent_context_enabled") or not spoken or self.stt is None:
            return
        stt_cfg["agent_context"] = spoken[:1750]
        message = update_configuration_message({"agent_context": stt_cfg["agent_context"]}, stt_cfg)
        if message:
            await self.stt.send_json(message)

    def _speech_end_ms(self, words):
        """Wall-clock ms when the learner stopped speaking, from the last word's end time.

        Word times are relative to the start of the audio stream, so they are added
        to origin_wall_ms. Returns None when there are no timed words.
        """
        ends = [word["end"] for word in words if isinstance(word.get("end"), (int, float))]
        if not ends or self.origin_wall_ms is None:
            return None
        return int(self.origin_wall_ms + max(ends))

    def _hold(self, record):
        """Remember a finished turn. Every line but the latest is appended now.

        The latest line stays in memory so it can own audio that arrives after
        its final (trailing silence). The next final, or close(), appends it.
        Lines in one session therefore sum to the audio we forwarded.
        """
        self.ready.append(record)
        self._flush_ready(final=False)

    def _flush_ready(self, final):
        """Write held log records. final=False keeps the newest turn; final=True writes all.

        Records are sorted by turn_order. Each record is billed the audio up to its
        own _audio_mark; on final, the last record also gets all trailing audio, so
        the lines of one session add up to all the audio forwarded to STT.
        """
        if not self.ready:
            return
        # Order by turn_order (None last).
        self.ready.sort(key=lambda record: (record.get("turn_order") is None, record.get("turn_order")))
        if not final:
            # Not final: write everything except the newest turn, which may still own
            # trailing audio that arrives after it.
            highest = max(record.get("turn_order") for record in self.ready)
            keep = []
            for record in self.ready:
                if record.get("turn_order") == highest:
                    keep.append(record)
                else:
                    self._commit(record, record.get("_audio_mark") or self.audio_accounted)
            self.ready = keep
            return
        # Final: write everything; the last record gets all audio forwarded so far.
        last = len(self.ready) - 1
        for index, record in enumerate(self.ready):
            if index == last:
                upto = max(self.audio_bytes, record.get("_audio_mark") or 0)
            else:
                upto = record.get("_audio_mark") or self.audio_accounted
            self._commit(record, upto)
        self.ready = []

    def _commit(self, record, upto_bytes):
        # Never bill the same bytes twice: start from what is already accounted.
        """Write one log line, billing it the audio forwarded since the previous line.

        upto_bytes is how far into the forwarded audio this line reaches. Bytes are
        converted to seconds as PCM16 mono (2 bytes per sample) at TARGET_RATE.
        """
        upto = max(int(upto_bytes), self.audio_accounted)
        seconds = (upto - self.audio_accounted) / 2 / TARGET_RATE
        self.audio_accounted = upto
        record["usage"]["stt_audio_seconds"] = round(seconds, 3)
        # Internal bookkeeping key; not part of the log schema.
        record.pop("_audio_mark", None)
        write_turn(self.log_path, record)
        self.turns_logged += 1

    def _alive(self, generation):
        """True while a task started at `generation` is still the current one and the session is open.
        """
        return generation == self.generation and not self.closed

    async def _cancel_job(self):
        """Cancel the current reply/replay task and wait for it to finish unwinding.

        Errors from the cancelled task are swallowed: it is being thrown away.
        """
        job = self.job
        self.job = None
        if job is not None and not job.done():
            job.cancel()
            try:
                await job
            except (asyncio.CancelledError, Exception):
                pass

    async def _status(self, state):
        """Set the session state and tell the browser ({"type": "status", "state": ...})."""
        self.state = state
        await self._emit({"type": "status", "state": state})

    async def _error(self, message):
        """Send a user-visible error message to the browser ({"type": "error"})."""
        await self._emit({"type": "error", "message": str(message)})

    async def _emit(self, payload):
        """Send one payload (dict -> JSON text frame, bytes -> audio frame) to the browser.

        If the socket is gone, the session marks itself closed instead of raising,
        so a dropped browser cannot crash a reply task halfway.
        """
        if self.closed:
            return
        try:
            await self.send(payload)
        except Exception:
            logger.debug("client send failed", exc_info=True)
            self.closed = True


# After this many misses on the same practice phrase, read it slowly by syllables.
MISSES_BEFORE_SLOW = 2

# Fallback spoken replies for a practice verdict when the model's reply is missing or
# contradicts the check.
CANNED_VERDICT = {
    "correct": "Correct! That sounded right.",
    "close": "Close! Almost there.",
    "try_again": "Not quite. Listen once more.",
}


def speech_plans(lesson, native_name, target_name_, slow_read=False):
    """What to say for one tutor turn, as (text, tts language, kind) segments.

    all: the whole turn. The reply in the learner's voice, the target phrase in
    the target-language voice (slowly first for pronunciation), then the next
    prompt. phrase / slow: what the play buttons replay.
    """
    phrase = (lesson.translation or "").strip()
    reply = (getattr(lesson, "reply", "") or "").strip()
    intent = getattr(lesson, "intent", None)
    verdict = getattr(lesson, "verdict", "none")
    next_prompt = (getattr(lesson, "next_prompt", "") or "").strip()
    # If the reply already ends with next_prompt, cut it off so the prompt is not spoken
    # twice.
    if next_prompt and reply.lower().endswith(next_prompt.lower()) and len(reply) > len(next_prompt):
        # The model sometimes ends the reply with the next prompt too; say it once.
        reply = reply[: -len(next_prompt)].rstrip(" ,;-")
    # The slow read: syllables first, then the whole phrase at normal speed.
    slow = slow_text(phrase, getattr(lesson, "syllables", "")) if phrase else ""
    phrase_seg = [(phrase, target_name_, "phrase")] if phrase else []
    slow_seg = ([(slow, target_name_, "slow")] if slow else []) + phrase_seg
    plan = []
    if reply:
        plan.append((reply, native_name, "reply"))
    # Pronunciation questions get the slow read; practice attempts get the phrase again
    # only when not correct; everything else (teach, translate) gets the phrase once.
    if intent == "pronunciation":
        plan += slow_seg
    elif intent == "practice_attempt":
        if verdict != "correct":
            # After two misses, syllables slowly, then the whole phrase.
            plan += slow_seg if slow_read else phrase_seg
    else:
        plan += phrase_seg
    # End with the next prompt, in the learner's voice, if anything else is spoken.
    if next_prompt and plan:
        plan.append((next_prompt, native_name, "next"))
    if not reply and not next_prompt:
        # Older schema (no reply): speak the phrase alone, as before.
        plan = list(phrase_seg)
    return {"all": plan, "phrase": phrase_seg, "slow": slow_seg}


def _ms(timestamp):
    """Convert a time in seconds (float, or None) to integer milliseconds (or None)."""
    if timestamp is None:
        return None
    return int(timestamp * 1000)
