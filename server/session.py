"""One learner session. The WebSocket server and file mode both use this class."""

import asyncio
import logging
import time
import uuid

from server.audio import TARGET_RATE
from server.config import (
    STT_LANGUAGES,
    log_config,
    merge_session_config,
    normalize_target,
    target_name,
)
from server.log import write_turn
from server.llm import error_strings
from server.stt import UPDATABLE, StreamingSTT, stt_query_params, update_configuration_message
from server.tts import TTSError, audio_ms

logger = logging.getLogger("pingo.session")

# SpeechStarted while the tutor is working means the learner talked over it.
BUSY = ("thinking", "speaking")


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
        self.log_path = settings["log"]["path"] if log_path is None else log_path
        self.target = settings.get("target_language")
        self.stt = None
        self.stt_query = {}
        self.stt_session_id = None
        self.begin_configuration = None
        self.job = None
        self.generation = 0
        self.state = "idle"
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
            await self._replay(message.get("turn_order"))
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
        self.state = "idle"

    async def _start(self, message):
        if self.stt is not None or self.state != "idle":
            await self._replace_upstream()
        self.closed = False
        try:
            self.target = normalize_target(
                message.get("target_language") or self.base_settings.get("target_language")
            )
        except ValueError as exc:
            await self._error(str(exc))
            return
        if message.get("case_id"):
            self.case_id = str(message["case_id"])
        if message.get("session_id"):
            self.session_id = str(message["session_id"])
        self.settings = merge_session_config(self.base_settings, message.get("config") or {})
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
        self.settings = merge_session_config(self.settings, config)
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
            self.target = normalize_target(value)
            self.settings["target_language"] = self.target
        except ValueError as exc:
            await self._error(str(exc))

    async def _replay(self, turn_order):
        try:
            key = int(turn_order)
        except (TypeError, ValueError):
            await self._error("Replay needs a turn_order.")
            return
        saved = self.lessons.get(key)
        if not saved:
            await self._error(f"No lesson stored for turn {key}.")
            return
        self.generation += 1
        generation = self.generation
        await self._cancel_job()
        self.job = asyncio.create_task(
            self._speak(saved["translation"], saved["target_language"], generation, None)
        )

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

    async def _on_speech_started(self):
        if self.state not in BUSY:
            return
        self.generation += 1
        await self._cancel_job()
        await self._emit({"type": "barge_in"})
        await self._status("listening")

    async def _on_turn(self, turn):
        order = turn.get("turn_order")
        if order != self.partial_turn:
            self.partial_turn = order
            self.partials = []
            self.partial_first = None
        view = {
            "transcript": turn.get("transcript") or "",
            "words": [word_view(word) for word in turn.get("words") or []],
        }
        if not turn.get("end_of_turn"):
            if view["transcript"] and self.partial_first is None:
                self.partial_first = view["transcript"]
            self.partials.append(view)
        await self._emit(
            {
                "type": "transcript",
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
        audio_mark = self.audio_bytes
        received_ms = now_ms()
        self.generation += 1
        generation = self.generation
        await self._cancel_job()
        self.job = asyncio.create_task(
            self._respond(turn, view, partials, partial_first, generation, audio_mark, received_ms)
        )

    async def _respond(self, turn, view, partials, partial_first, generation, audio_mark, received_ms):
        record = self._new_record(turn, view, partials, partial_first, audio_mark, received_ms)
        try:
            if not self._alive(generation):
                record["barge_in"] = True
                return
            await self._status("thinking")
            lesson = await self.llm.complete(
                transcript=view["transcript"],
                source_language=turn.get("language_code"),
                language_confidence=turn.get("language_confidence"),
                target_name=target_name(self.target),
            )
            record["timings_ms"]["llm_first_token"] = _ms(lesson.first_token_at)
            record["timings_ms"]["llm_done"] = _ms(lesson.done_at)
            record["llm"] = {
                "model": lesson.model,
                "translation": lesson.translation,
                "pronunciation": lesson.pronunciation,
                "tip": lesson.tip,
                "detected_errors": error_strings(lesson.detected_errors),
                "streamed": lesson.streamed,
            }
            record["usage"]["llm_input_tokens"] = lesson.tokens_in
            record["usage"]["llm_output_tokens"] = lesson.tokens_out
            if lesson.unparsed:
                await self._error("The tutor reply was not valid JSON, so it was not spoken.")
                lesson.tip = "Please try that phrase again."
                record["llm"]["tip"] = lesson.tip
            if not self._alive(generation):
                record["barge_in"] = True
                return
            order = turn.get("turn_order")
            self.lessons[order] = {"translation": lesson.translation, "target_language": self.target}
            await self._emit(
                {
                    "type": "lesson",
                    "turn_order": order,
                    "source_text": view["transcript"],
                    "source_language": turn.get("language_code"),
                    "translation": lesson.translation,
                    "target_language": self.target,
                    "pronunciation": lesson.pronunciation,
                    "tip": lesson.tip,
                }
            )
            await self._emit({"type": "agent_text", "text": lesson.translation, "final": True})
            if lesson.translation and not lesson.unparsed:
                await self._speak(lesson.translation, self.target, generation, record)
            if not lesson.unparsed:
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

    async def _speak(self, text, language_code, generation, record):
        if not text or not self._alive(generation):
            return
        name = target_name(language_code)
        text = text[:300]
        sample_rate = int(self.settings["tts"].get("sample_rate") or 24000)
        seconds = float(self.settings["tts"].get("timeout_s") or 30)
        meta = {}
        total = 0
        started = False
        await self._status("speaking")
        await self._emit({"type": "tts_start", "sample_rate": sample_rate})
        started = True
        try:
            async with asyncio.timeout(seconds):
                async for chunk in self.tts.stream(text, name, meta):
                    if not self._alive(generation):
                        if record is not None:
                            record["barge_in"] = True
                        break
                    if record is not None and record["timings_ms"]["tts_first_audio"] is None:
                        # First byte this process got back from TTS, before the browser plays it.
                        record["timings_ms"]["tts_first_audio"] = now_ms()
                    total += len(chunk)
                    await self._emit(chunk)
        except TimeoutError as exc:
            raise TTSError(f"Speech synthesis timed out after {int(seconds)} seconds.") from exc
        finally:
            if record is not None:
                rate = meta.get("sample_rate") or sample_rate
                chars = meta.get("billed_chars")
                record["usage"]["tts_characters"] = chars if chars is not None else len(text)
                record["tts_audio_ms"] = audio_ms(total, rate)
            if started:
                await self._emit({"type": "tts_end"})
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


def _ms(timestamp):
    if timestamp is None:
        return None
    return int(timestamp * 1000)
