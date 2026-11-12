"""One learner session. The WebSocket server and file mode both use this class."""

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
    if text in STT_LANGUAGES or text in OTHER_LANGUAGE_NAMES or (text.isalpha() and len(text) in (2, 3)):
        return previous, True
    return previous, False


def now_ms():
    return int(time.time() * 1000)


def word_view(word):
    return {
        "text": word.get("text", ""),
        "start": word.get("start"),
        "end": word.get("end"),
        "confidence": word.get("confidence"),
        "word_is_final": bool(word.get("word_is_final", False)),
    }


class Session:
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
        self.base_settings = settings
        self.settings = settings
        self.api_key = api_key
        self.send = send
        self.case_id = case_id or ""
        self.session_id = session_id or str(uuid.uuid4())
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
        self.stt = None
        self.stt_query = {}
        self.stt_session_id = None
        self.begin_configuration = None
        self.job = None
        self.generation = 0
        self.state = "idle"
        # Wall-clock ms when the browser should finish playing what we sent.
        self.playback_until = 0
        # turn_order of the final turn the tutor is currently answering.
        self.answering_order = None
        self.lessons = {}
        self.partials = []
        self.partial_first = None
        self.partial_turn = None
        self.audio_bytes = 0
        self.audio_accounted = 0
        self.origin_wall_ms = None
        self._pending_audio = []
        self._stt_ready = False
        self.background = set()
        self.history = []
        self.practice_target = ""
        # Respelling and syllables taught with the practice phrase. Attempts and
        # breakdowns reuse them instead of trusting the model to repeat them.
        self.practice_card = {}
        # Misses on the current practice phrase. After two, the phrase is read slowly.
        self.misses = 0
        self.native_language = "en"
        self._order_ids = {}
        self._stt_epoch = 0
        self._turn_seq = 0
        self._turn_key = None
        self._turn_id = 0
        self.ready = []
        self.closed = False
        self.turns_logged = 0

    async def handle_text(self, message):
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
        self.audio_bytes += len(pcm)
        await self.stt.send_audio(pcm)

    async def _flush_pending_audio(self):
        pending = self._pending_audio
        self._pending_audio = []
        for pcm in pending:
            if self.stt is None:
                return
            await self._forward_audio(pcm)

    async def close(self):
        if self.closed and self.stt is None and not self.ready and self.job is None:
            return
        for task in list(self.background):
            task.cancel()
        self.closed = True
        self.generation += 1
        await self._cancel_job()
        stt = self.stt
        self.stt = None
        if stt is not None:
            await stt.close()
        self._flush_ready(final=True)
        self._forget()
        self.state = "idle"

    async def shutdown(self):
        """The socket is gone: close, then hang up the per-voice TTS sessions."""
        await self.close()
        if hasattr(self.voices, "aclose"):
            await self.voices.aclose()

    async def _start(self, message):
        if self.stt is not None or self.state != "idle":
            await self._replace_upstream()
        self.closed = False
        self.auto_target = bool(message.get("auto_target"))
        raw_target = message.get("target_language")
        try:
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
        if native in TARGET_LANGUAGES:
            self.native_language = native
        try:
            self.settings = merge_session_config(self.base_settings, message.get("config") or {})
        except ValueError as exc:
            await self._error(str(exc))
            return
        self.settings["target_language"] = self.target
        bad_codes = [
            code for code in self.settings["stt"]["language_codes"] if code not in STT_LANGUAGES
        ]
        if bad_codes:
            await self._error("Unknown language code: " + ", ".join(bad_codes))
            return
        if not self.api_key:
            await self._error("ASSEMBLYAI_API_KEY is not set.")
            return
        self.stt_query = stt_query_params(self.settings["stt"])
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
        self._stt_epoch += 1
        self.stt = self.stt_factory(self.api_key, self.stt_query, self._on_stt)
        try:
            await self.stt.connect()
        except Exception as exc:
            self.stt = None
            self._pending_audio = []
            await self._error(f"Could not open speech-to-text: {exc}")
            return
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
        try:
            self.settings = merge_session_config(self.settings, config)
        except ValueError as exc:
            # A bad value leaves the session settings as they were.
            await self._error(str(exc))
            return
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
        self.stt_query = stt_query_params(self.settings["stt"])

    async def _set_target(self, value):
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
        if not isinstance(message, dict):
            message = {"turn_order": message}
        key = None
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
        mode = message.get("mode") if message.get("mode") in REPLAY_MODES else "phrase"
        plan = saved.get(mode) or saved.get("phrase") or saved.get("all") or []
        if not plan:
            await self._error(f"Nothing to replay for turn {key}.")
            return
        self.generation += 1
        generation = self.generation
        await self._cancel_job()
        self.job = asyncio.create_task(self._speak_plan(plan, generation, None, key))

    async def _on_stt(self, message):
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
        if not self._agent_audible():
            return
        await self._barge_in()

    async def _barge_in(self):
        self.playback_until = 0
        self.generation += 1
        await self._cancel_job()
        await self._emit({"type": "barge_in"})
        await self._status("listening")

    async def _on_turn(self, turn):
        order = turn.get("turn_order")
        turn_id = self._turn_id_for(order)
        if order is not None:
            self._order_ids[order] = turn_id
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
        if not turn.get("end_of_turn"):
            if view["transcript"] and self.partial_first is None:
                self.partial_first = view["transcript"]
            self.partials.append(view)
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
        self.generation += 1
        generation = self.generation
        await self._cancel_job()
        self.job = asyncio.create_task(
            self._respond(
                turn, view, partials, partial_first, generation, audio_mark, received_ms, turn_id
            )
        )

    async def _respond(self, turn, view, partials, partial_first, generation, audio_mark, received_ms, turn_id):
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
            if check is None:
                self._note_native(turn, transcript)
                # A pending practice phrase does not swallow a new phrase in the
                # learner's own language ("Good morning" while "Hola" is pending).
                new_phrase = is_native_phrase(transcript, self.practice_target, self.native_language)
                if not new_phrase:
                    other_language = self._other_language(turn, transcript)
            native = self._native_name()
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
            if self.auto_target and not lesson.unparsed:
                check = self._apply_auto_target(lesson, check, record)
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
            record["practice"] = check
            record["routing"] = (
                "practice_attempt" if check
                else "new_phrase" if new_phrase
                else "other_language" if other_language
                else "model"
            )
            record["usage"]["llm_input_tokens"] = lesson.tokens_in
            record["usage"]["llm_output_tokens"] = lesson.tokens_out
            if lesson.unparsed:
                record["llm"]["raw_text"] = (getattr(lesson, "raw_text", "") or "")[:500]
                await self._error("The tutor reply was not valid JSON, so it was not spoken.")
                lesson.tip = "Please try that phrase again."
                record["llm"]["tip"] = lesson.tip
            if not self._alive(generation):
                record["barge_in"] = True
                return
            order = turn.get("turn_order")
            plans = speech_plans(
                lesson,
                native,
                target_name(self.target) if self.target else native,
                slow_read=self.misses >= MISSES_BEFORE_SLOW,
            )
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
            spoken = " ".join(text for text, _lang, _kind in plans["all"])
            await self._emit(
                {"type": "agent_text", "turn_id": turn_id, "text": spoken or lesson.translation, "final": True}
            )
            if plans["all"] and not lesson.unparsed:
                await self._speak_plan(plans["all"], generation, record, turn_id)
            if not lesson.unparsed and not record["barge_in"] and self._alive(generation):
                self._remember(transcript, lesson)
                await self._push_agent_context(lesson.translation)
        except asyncio.CancelledError:
            record["barge_in"] = True
            raise
        except Exception as exc:
            logger.exception("turn failed")
            await self._error(str(exc))
            if self._alive(generation):
                await self._status("listening")
        finally:
            if not self._alive(generation):
                record["barge_in"] = True
            self._hold(record)

    def _new_record(self, turn, view, partials, partial_first, audio_mark, received_ms):
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
        key = (self._stt_epoch, turn_order)
        if key != self._turn_key:
            self._turn_seq += 1
            self._turn_key = key
            self._turn_id = self._turn_seq
        return self._turn_id

    def _forget(self):
        self.history.clear()
        self.practice_target = ""
        # Respelling and syllables taught with the practice phrase. Attempts and
        # breakdowns reuse them instead of trusting the model to repeat them.
        self.practice_card = {}
        # Misses on the current practice phrase. After two, the phrase is read slowly.
        self.misses = 0

    def _warm_voices(self):
        warm = getattr(self.voices, "warm", None)
        if warm is None:
            return
        names = [self._native_name()] + ([target_name(self.target)] if self.target else [])
        task = asyncio.create_task(warm(names))
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    def _native_name(self):
        return TARGET_LANGUAGES.get(self.native_language, "english")

    def _note_native(self, turn, transcript):
        """The learner's own language: what they speak when they are not practising."""
        code = turn.get("language_code")
        confidence = turn.get("language_confidence")
        if code not in TARGET_LANGUAGES or code == self.target:
            return
        if len(transcript.split()) < NATIVE_MIN_WORDS:
            return
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
        if target != previous:
            logger.info("auto target: %s -> %s", previous, target)
            if previous is not None:
                self._forget()
            # The check was against the old language's phrase.
            check = None
            self.target = target
            self.settings["target_language"] = target
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
            if check["verdict"] == "correct":
                lesson.detected_errors = []
        self._pin_practice_phrase(lesson, check)
        if lesson.translation and (lesson.intent != "practice_attempt" or not self.practice_target):
            if lesson.translation != self.practice_target:
                self.misses = 0
                self.practice_card = {}
            self.practice_target = lesson.translation
            self.practice_card = {
                "pronunciation": lesson.pronunciation or self.practice_card.get("pronunciation", ""),
                "syllables": getattr(lesson, "syllables", "") or self.practice_card.get("syllables", ""),
            }
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
        spoken = "\n".join(parts) if (reply or intent) else f"{phrase}\n{tip}".strip()
        self.history.append({"role": "user", "content": transcript})
        self.history.append({"role": "assistant", "content": spoken})
        extra = len(self.history) - HISTORY_TURNS * 2
        if extra > 0:
            del self.history[:extra]

    async def _speak(self, text, language_code, generation, record, turn_id=None):
        """One segment in one language. Kept for callers that speak a single phrase."""
        if not text:
            return
        await self._speak_plan([(text, target_name(language_code), "phrase")], generation, record, turn_id)

    async def _speak_plan(self, segments, generation, record, turn_id=None):
        """Speak segments in order, each in its own voice, as one tts_start..tts_end.

        Every segment's TTS session starts at once so the second voice is ready
        when the first one finishes. Audio is still sent in order, with a short
        silence between segments.
        """
        segments = [(text.strip()[:300], lang, kind) for text, lang, kind in segments if text and text.strip()]
        if not segments or not self._alive(generation):
            return
        sample_rate = int(self.settings["tts"].get("sample_rate") or 24000)
        seconds = float(self.settings["tts"].get("timeout_s") or 30) + 10 * (len(segments) - 1)
        gap = b"\x00\x00" * int(sample_rate * GAP_MS / 1000)
        metas = [{} for _ in segments]
        queues = [asyncio.Queue() for _ in segments]
        total = 0

        async def pump(text, language_name, queue, meta):
            try:
                async for chunk in self.voices.stream(text, language_name, meta):
                    queue.put_nowait(chunk)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                queue.put_nowait(exc)
            finally:
                queue.put_nowait(None)

        await self._status("speaking")
        await self._emit(
            {
                "type": "tts_start",
                "turn_id": turn_id,
                "sample_rate": sample_rate,
                "segments": [{"kind": kind, "language": lang, "text": text} for text, lang, kind in segments],
            }
        )
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
                    if index and total:
                        total += len(gap)
                        await self._emit(gap)
                    while True:
                        item = await queue.get()
                        if item is None:
                            break
                        if isinstance(item, Exception):
                            raise item
                        if not self._alive(generation):
                            if record is not None:
                                record["barge_in"] = True
                            stopped = True
                            break
                        if record is not None and record["timings_ms"]["tts_first_audio"] is None:
                            # First byte this process got back from TTS, before the browser plays it.
                            record["timings_ms"]["tts_first_audio"] = now_ms()
                        total += len(item)
                        await self._emit(item)
                        self.playback_until = play_start + audio_ms(total, sample_rate) + PLAYBACK_PAD_MS
        except TimeoutError as exc:
            raise TTSError(f"Speech synthesis timed out after {int(seconds)} seconds.") from exc
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if record is not None:
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
        stt_cfg = self.settings["stt"]
        if not stt_cfg.get("agent_context_enabled") or not spoken or self.stt is None:
            return
        stt_cfg["agent_context"] = spoken[:1750]
        message = update_configuration_message({"agent_context": stt_cfg["agent_context"]}, stt_cfg)
        if message:
            await self.stt.send_json(message)

    def _speech_end_ms(self, words):
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
        if not self.ready:
            return
        self.ready.sort(key=lambda record: (record.get("turn_order") is None, record.get("turn_order")))
        if not final:
            highest = max(record.get("turn_order") for record in self.ready)
            keep = []
            for record in self.ready:
                if record.get("turn_order") == highest:
                    keep.append(record)
                else:
                    self._commit(record, record.get("_audio_mark") or self.audio_accounted)
            self.ready = keep
            return
        last = len(self.ready) - 1
        for index, record in enumerate(self.ready):
            if index == last:
                upto = max(self.audio_bytes, record.get("_audio_mark") or 0)
            else:
                upto = record.get("_audio_mark") or self.audio_accounted
            self._commit(record, upto)
        self.ready = []

    def _commit(self, record, upto_bytes):
        upto = max(int(upto_bytes), self.audio_accounted)
        seconds = (upto - self.audio_accounted) / 2 / TARGET_RATE
        self.audio_accounted = upto
        record["usage"]["stt_audio_seconds"] = round(seconds, 3)
        record.pop("_audio_mark", None)
        write_turn(self.log_path, record)
        self.turns_logged += 1

    def _alive(self, generation):
        return generation == self.generation and not self.closed

    async def _cancel_job(self):
        job = self.job
        self.job = None
        if job is not None and not job.done():
            job.cancel()
            try:
                await job
            except (asyncio.CancelledError, Exception):
                pass

    async def _status(self, state):
        self.state = state
        await self._emit({"type": "status", "state": state})

    async def _error(self, message):
        await self._emit({"type": "error", "message": str(message)})

    async def _emit(self, payload):
        if self.closed:
            return
        try:
            await self.send(payload)
        except Exception:
            logger.debug("client send failed", exc_info=True)
            self.closed = True


MISSES_BEFORE_SLOW = 2

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
    if next_prompt and reply.lower().endswith(next_prompt.lower()) and len(reply) > len(next_prompt):
        # The model sometimes ends the reply with the next prompt too; say it once.
        reply = reply[: -len(next_prompt)].rstrip(" ,;-")
    slow = slow_text(phrase, getattr(lesson, "syllables", "")) if phrase else ""
    phrase_seg = [(phrase, target_name_, "phrase")] if phrase else []
    slow_seg = ([(slow, target_name_, "slow")] if slow else []) + phrase_seg
    plan = []
    if reply:
        plan.append((reply, native_name, "reply"))
    if intent == "pronunciation":
        plan += slow_seg
    elif intent == "practice_attempt":
        if verdict != "correct":
            # After two misses, syllables slowly, then the whole phrase.
            plan += slow_seg if slow_read else phrase_seg
    else:
        plan += phrase_seg
    if next_prompt and plan:
        plan.append((next_prompt, native_name, "next"))
    if not reply and not next_prompt:
        # Older schema (no reply): speak the phrase alone, as before.
        plan = list(phrase_seg)
    return {"all": plan, "phrase": phrase_seg, "slow": slow_seg}


def _ms(timestamp):
    if timestamp is None:
        return None
    return int(timestamp * 1000)
