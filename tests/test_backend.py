"""End-to-end pipeline tests with fake AssemblyAI clients. No network, no API key.

Covers the whole path (mic audio -> STT -> LLM tutor -> TTS -> browser) using
_FakeSTT / _FakeLLM / _FakeTTS stand-ins: config loading and STT parameters,
WAV handling, lesson JSON parsing and the LLM request shape, the Session
(file mode log line, audio accounting, barge-in, replay, restarts, chat
memory) and the real FastAPI WebSocket endpoint.
"""

import asyncio
import json
import os
import tempfile
import unittest
import wave

from server.audio import iter_chunks, read_pcm16_mono, resample_pcm16
from server.config import bind_address, default_settings, load_settings, merge_session_config
from server.llm import error_strings, parse_lesson_text
from server.log import write_turn
from server.session import Session
from server.stt import stt_query_params, update_configuration_message


class ConfigTests(unittest.TestCase):
    """Settings loading and the STT/LLM parameters built from them."""
    def test_baseline_sends_mode_interruption_delay_and_prompt(self):
        """Baseline config produces the expected STT connect params and LLM/TTS defaults, and omits unsupported fields.
        """
        settings = load_settings("configs/baseline.yaml")
        params = stt_query_params(settings["stt"])
        self.assertEqual(params["speech_model"], "universal-3-6-pro")
        self.assertEqual(params["mode"], "balanced")
        self.assertEqual(params["interruption_delay"], 250)
        self.assertEqual(params["min_turn_silence"], 400)
        self.assertEqual(params["max_turn_silence"], 2400)
        self.assertEqual(params["vad_threshold"], 0.2)
        self.assertIn("language learner", params["prompt"])
        self.assertIs(params["language_detection"], True)
        self.assertNotIn("keyterms_prompt", params)
        self.assertNotIn("language_codes", params)
        self.assertNotIn("end_of_turn_confidence_threshold", params)
        self.assertNotIn("llm_gateway", params)
        self.assertEqual(settings["llm"]["model"], "gemma-4-31b")
        self.assertEqual(settings["llm"]["max_tokens"], 450)
        self.assertEqual(
            settings["tts"]["voices"]["spanish"],
            "bff56a11-dc95-552e-a93a-59f68ca60b64",
        )

    def test_keyterms_config_sends_terms(self):
        """A non-empty keyterms_prompt, set as a config override, is forwarded to STT."""
        settings = merge_session_config(
            load_settings("configs/baseline.yaml"),
            {"keyterms_prompt": ["gracias"]},
        )
        params = stt_query_params(settings["stt"])
        self.assertIn("gracias", params["keyterms_prompt"])

    def test_empty_client_prompt_does_not_wipe_the_server_prompt(self):
        """An empty client prompt keeps the server prompt, while an empty keyterms list does clear keyterms.
        """
        settings = load_settings("configs/baseline.yaml")
        merged = merge_session_config(
            settings,
            {"language_codes": [], "prompt": "", "keyterms_prompt": [], "agent_context": ""},
        )
        self.assertIn("language learner", merged["stt"]["prompt"])
        self.assertEqual(merged["stt"]["keyterms_prompt"], [])

    def test_env_and_set_override(self):
        """--set overrides win over the YAML file and get the right types."""
        settings = load_settings(
            "configs/baseline.yaml",
            ["stt.max_turn_silence=1800", "llm.temperature=0"],
        )
        self.assertEqual(settings["stt"]["max_turn_silence"], 1800)
        self.assertEqual(settings["llm"]["temperature"], 0.0)
        self.assertNotIn("end_of_turn_confidence_threshold", settings["stt"])

    def test_update_configuration_forwards_supported_fields(self):
        """UpdateConfiguration carries live-updatable fields but drops mode (set_params has no mode).
        """
        settings = load_settings("configs/baseline.yaml")
        message = update_configuration_message(
            {"keyterms_prompt": ["hola"], "mode": "max_accuracy", "min_turn_silence": 400},
            settings["stt"],
        )
        self.assertEqual(message["type"], "UpdateConfiguration")
        self.assertEqual(message["keyterms_prompt"], ["hola"])
        self.assertEqual(message["min_turn_silence"], 400)
        # RealTimeSessionParameters has no mode field, so set_params cannot send it.
        self.assertNotIn("mode", message)
        self.assertNotIn("end_of_turn_confidence_threshold", message)

    def test_local_bind_stays_on_localhost_until_port_is_set(self):
        """Bind address defaults to 127.0.0.1:8000; PORT env and --host/--port override it in order.
        """
        settings = default_settings()
        self.assertEqual(bind_address(settings, env={}), ("127.0.0.1", 8000))
        self.assertEqual(bind_address(settings, env={"PORT": "10000"}), ("127.0.0.1", 10000))
        self.assertEqual(
            bind_address(settings, host="0.0.0.0", port=443, env={"PORT": "10000"}),
            ("0.0.0.0", 443),
        )

    def test_system_prompt_refuses_silent_correction(self):
        """The default tutor prompt tells the model never to silently correct the learner."""
        settings = load_settings("configs/baseline.yaml")
        text = settings["llm"]["system_prompt"]
        self.assertIn("Never silently correct", text)
        self.assertIn("name the specific difference", text)


class AudioTests(unittest.TestCase):
    """WAV reading, resampling and chunking."""
    def test_resample_doubles_an_8k_clip(self):
        """Resampling 8 kHz to 16 kHz doubles the number of samples."""
        pcm = b"\x00\x10" * 8000
        out = resample_pcm16(pcm, 8000, 16000)
        self.assertEqual(len(out), 16000 * 2)

    def test_wav_stereo_becomes_16k_mono(self):
        """A stereo 8 kHz WAV becomes 16 kHz mono, and 50 ms chunks rejoin to the same bytes."""
        path = os.path.join(tempfile.mkdtemp(), "clip.wav")
        with wave.open(path, "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\x00\x00\x02\x00" * 800)
        pcm = read_pcm16_mono(path)
        self.assertEqual(len(pcm), 1600 * 2)
        chunks = list(iter_chunks(pcm, chunk_ms=50))
        self.assertTrue(all(len(chunk) <= 1600 for chunk in chunks))
        self.assertEqual(b"".join(chunks), pcm)


class LessonParseTests(unittest.TestCase):
    """Parsing the tutor's JSON and the shape of the LLM Gateway request."""
    def test_parses_fenced_json_and_stringifies_errors(self):
        """JSON inside a ``` fence parses, and detected errors become "heard -> issue" strings."""
        text = """```json
        {"translation": "Tengo frío.", "pronunciation": "TEN-go FREE-o", "tip": "Soy is for identity.",
         "detected_errors": [{"heard": "soy", "issue": "tengo"}]}
        ```"""
        parsed = parse_lesson_text(text)
        self.assertEqual(parsed["translation"], "Tengo frío.")
        self.assertEqual(error_strings(parsed["detected_errors"]), ["soy -> tengo"])

    def test_plain_text_is_not_spoken(self):
        """A non-JSON reply parses to None (so it is never spoken)."""
        self.assertIsNone(parse_lesson_text("just say hola"))

    def test_newline_inside_a_json_string(self):
        """A raw newline inside a JSON string value is accepted."""
        raw = (
            '{"translation":"Hola","pronunciation":"OH-la",'
            '"tip":"line one\nline two","detected_errors":[]}'
        )
        parsed = parse_lesson_text(raw)
        self.assertEqual(parsed["translation"], "Hola")
        self.assertEqual(parsed["tip"], "line one\nline two")

    def test_playground_post_shape_and_usage(self):
        """The POST matches the playground request; only gemma gets response_format; token usage is read.
        """
        import server.llm as llm

        captured = {}

        class Response:
            """Minimal fake of a requests.Response with a lesson in it."""
            status_code = 200
            text = ""

            def json(self):
                """Gateway-shaped body: one choice with lesson JSON, plus token usage."""
                return {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"translation":"Hola","pronunciation":"OH-la",'
                                    '"tip":"Short.","detected_errors":[]}'
                                )
                            }
                        }
                    ],
                    "usage": {"input_tokens": 11, "output_tokens": 7},
                }

        def fake_post(url, headers=None, json=None, timeout=None):
            """Stand-in for requests.post that records the request and returns the fake response."""
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return Response()

        original = llm.requests.post
        llm.requests.post = fake_post
        try:
            lesson = _run(
                llm.GatewayLLM(
                    "secret",
                    {
                        "model": "qwen3.5-4b-32k-fast",
                        "system_prompt": "You are Pingo. {target}",
                        "timeout_s": 5,
                    },
                ).complete("hello", "en", 0.9, "Spanish")
            )
            qwen = dict(captured)
            captured.clear()
            gemma = _run(
                llm.GatewayLLM(
                    "secret",
                    {
                        "model": "gemma-4-31b",
                        "temperature": 0.2,
                        "max_tokens": 300,
                        "system_prompt": "You are Pingo. {target}",
                        "timeout_s": 5,
                    },
                ).complete("hello", "en", 0.9, "Spanish")
            )
        finally:
            llm.requests.post = original
        self.assertEqual(qwen["url"], "https://llm-gateway.assemblyai.com/v1/chat/completions")
        self.assertEqual(qwen["headers"], {"authorization": "secret"})
        self.assertEqual(qwen["json"]["model"], "qwen3.5-4b-32k-fast")
        self.assertEqual(qwen["json"]["max_tokens"], 450)
        self.assertEqual(qwen["json"]["temperature"], 0.2)
        self.assertEqual([item["role"] for item in qwen["json"]["messages"]], ["system", "user"])
        self.assertNotIn("response_format", qwen["json"])
        self.assertEqual(lesson.translation, "Hola")
        self.assertEqual(lesson.tokens_in, 11)
        self.assertEqual(lesson.tokens_out, 7)
        self.assertEqual(captured["json"]["model"], "gemma-4-31b")
        self.assertEqual(captured["json"]["response_format"]["type"], "json_schema")
        self.assertEqual(gemma.translation, "Hola")


class _FakeSTT:
    """Fake streaming STT: sends Begin on connect, then one partial and one final Turn on the first audio.
    """
    def __init__(self, api_key, params, on_event):
        """Same signature as StreamingSTT; records audio and config messages."""
        self.params = params
        self.on_event = on_event
        self.audio = bytearray()
        self.sent = []
        self.closed = False

    async def connect(self):
        """Pretend to connect and deliver a Begin event shortly after."""
        async def begin():
            """Deliver the Begin event on the next loop iteration, like a real server would."""
            await asyncio.sleep(0)
            await self.on_event(
                {
                    "type": "Begin",
                    "id": "stt-session",
                    "expires_at": 0,
                    "configuration": {"model": "universal-3-6-pro", "mode": "balanced"},
                }
            )

        asyncio.create_task(begin())

    async def send_audio(self, pcm):
        """Record audio; the first call also emits a partial and a final turn ("How do I say where is the station?").
        """
        self.audio.extend(pcm)
        if not getattr(self, "_emitted", False):
            self._emitted = True
            await self.on_event(
                {
                    "type": "Turn",
                    "turn_order": 0,
                    "end_of_turn": False,
                    "transcript": "how do I say",
                    "words": [{"text": "how", "start": 0, "end": 200, "confidence": 0.9, "word_is_final": True}],
                }
            )
            await self.on_event(
                {
                    "type": "Turn",
                    "turn_order": 0,
                    "end_of_turn": True,
                    "end_of_turn_confidence": 0.84,
                    "transcript": "How do I say where is the station?",
                    "language_code": "en",
                    "language_confidence": 0.93,
                    "words": [
                        {"text": "How", "start": 0, "end": 10, "confidence": 0.91, "word_is_final": True},
                        {"text": "station?", "start": 10, "end": 40, "confidence": 0.72, "word_is_final": True},
                    ],
                }
            )

    async def send_json(self, payload):
        """Record an UpdateConfiguration message."""
        self.sent.append(payload)

    async def close(self):
        """Mark the fake stream closed."""
        self.closed = True


class _FakeLLM:
    """Fake tutor: always returns the same Spanish lesson and records each call's arguments."""
    def __init__(self):
        """Start with no recorded calls."""
        self.calls = []

    async def complete(self, **kwargs):
        """Record the call and return a fixed lesson ("¿Dónde está la estación?")."""
        self.calls.append(kwargs)
        from server.llm import Lesson
        import time

        return Lesson(
            translation="¿Dónde está la estación?",
            pronunciation="DON-de es-TA la es-ta-SYON",
            tip="Estación is the place, not the season.",
            detected_errors=[],
            model="gemini-2.5-flash-lite",
            tokens_in=120,
            tokens_out=40,
            first_token_at=time.time(),
            done_at=time.time(),
            streamed=True,
        )


class _FakeTTS:
    """Fake TTS: yields a few fixed PCM chunks for any text."""
    def __init__(self, chunks=(b"\x01\x00" * 12, b"\x02\x00" * 8)):
        """Chunks to yield for every segment."""
        self.chunks = chunks

    async def stream(self, text, language_name, meta):
        """Fill the usage metadata and yield the fixed chunks."""
        meta["sample_rate"] = 24000
        meta["billed_chars"] = len(text)
        for chunk in self.chunks:
            yield chunk


class _SlowLLM(_FakeLLM):
    """Fake tutor that takes 1 s, so a test can barge in while it is "thinking"."""
    async def complete(self, **kwargs):
        """Wait 1 s, then return the normal fake lesson."""
        await asyncio.sleep(1)
        return await super().complete(**kwargs)


def _run(coro):
    """Run a coroutine to completion on a fresh event loop."""
    return asyncio.run(coro)


class SessionTests(unittest.TestCase):
    """Session behaviour: logging, audio accounting, barge-in, replay and restarts."""
    def test_file_mode_writes_the_eval_line_and_appends(self):
        """File mode writes one complete eval line (STT, LLM, timings, usage, config) and later writes append.
        """
        from server.__main__ import run_file

        folder = tempfile.mkdtemp()
        wav_path = os.path.join(folder, "clip.wav")
        with wave.open(wav_path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 1600)
        log_path = os.path.join(folder, "turns.jsonl")
        settings = load_settings("configs/baseline.yaml")
        settings["log"]["path"] = log_path
        code = _run(
            run_file(
                wav_path,
                settings,
                "test-key",
                case_id="pm-es-01",
                session_id="baseline-pm-es-01-1",
                pace=False,
                stt_factory=_FakeSTT,
                llm=_FakeLLM(),
                tts=_FakeTTS(),
                quiet_s=0.05,
                timeout_s=5,
            )
        )
        self.assertEqual(code, 0)
        with open(log_path, encoding="utf-8") as handle:
            lines = handle.readlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["case_id"], "pm-es-01")
        self.assertEqual(row["session_id"], "baseline-pm-es-01-1")
        self.assertEqual(row["turn_order"], 0)
        self.assertEqual(row["stt"]["transcript"], "How do I say where is the station?")
        self.assertEqual(row["stt"]["words"][1]["text"], "station?")
        self.assertAlmostEqual(row["stt"]["words"][1]["confidence"], 0.72)
        self.assertEqual(row["stt"]["language_code"], "en")
        self.assertEqual(row["stt"]["end_of_turn_confidence"], 0.84)
        self.assertEqual(row["llm"]["detected_errors"], [])
        self.assertEqual(row["llm"]["translation"], "¿Dónde está la estación?")
        self.assertIn("speech_end", row["timings_ms"])
        self.assertIn("stt_final", row["timings_ms"])
        self.assertIn("llm_first_token", row["timings_ms"])
        self.assertIn("llm_done", row["timings_ms"])
        self.assertIn("tts_first_audio", row["timings_ms"])
        # All timings are wall-clock ms (larger than 10^12 means a real epoch
        # timestamp).
        for key in ("speech_end", "stt_final", "llm_first_token", "llm_done", "tts_first_audio"):
            self.assertGreater(row["timings_ms"][key], 1_000_000_000_000)
        # speech_end is the last word's end mapped onto the same wall clock.
        self.assertGreaterEqual(row["timings_ms"]["stt_final"], row["timings_ms"]["speech_end"] - 40)
        self.assertGreaterEqual(row["timings_ms"]["tts_first_audio"], row["timings_ms"]["llm_done"])
        # 0.1s of audio plus max_turn_silence (2.4s) + 0.7s of endpointing silence.
        self.assertAlmostEqual(row["usage"]["stt_audio_seconds"], 3.2, places=2)
        self.assertEqual(row["usage"]["llm_input_tokens"], 120)
        self.assertEqual(row["usage"]["llm_output_tokens"], 40)
        self.assertGreater(row["usage"]["tts_characters"], 0)
        self.assertEqual(row["config"]["mode"], "balanced")
        self.assertEqual(row["config"]["llm_model"], "gemma-4-31b")
        self.assertEqual(row["config"]["llm_done"], "full_response")
        self.assertFalse(row["barge_in"])
        self.assertEqual(row["stt_partial_first"], "how do I say")

        write_turn(log_path, row)
        with open(log_path, encoding="utf-8") as handle:
            self.assertEqual(len(handle.readlines()), 2)

    def test_first_frame_arrival_is_the_origin(self):
        """The first audio frame's arrival time is the clock origin, and audio before STT is ready is buffered.
        """
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            async def send(_payload):
                """Fake browser socket: receives every message the session sends."""
                return None

            session = Session(
                load_settings("configs/baseline.yaml"),
                "key",
                send,
                log_path=os.path.join(tempfile.mkdtemp(), "t.jsonl"),
            )
            await session.handle_audio(b"\x00\x00", arrived_at=1_700_000_000_000)
            await session.handle_audio(b"\x00\x00", arrived_at=1_700_000_000_400)
            self.assertEqual(session.origin_wall_ms, 1_700_000_000_000)
            self.assertEqual(len(session._pending_audio), 2)

        _run(scenario())

    def test_audio_seconds_sum_across_turns(self):
        """Each log line is billed only its own audio, and the lines add up to all audio sent."""
        settings = load_settings("configs/baseline.yaml")
        folder = tempfile.mkdtemp()
        session = Session(settings, "key", self._ignore, log_path=os.path.join(folder, "t.jsonl"))
        session.audio_bytes = 32000 * 3
        first = {"turn_order": 0, "_audio_mark": 32000, "usage": {}, "llm": {"model": "m"}}
        second = {"turn_order": 1, "_audio_mark": 32000 * 2, "usage": {}, "llm": {"model": "m"}}
        session.ready = [first, second]
        session._flush_ready(final=True)
        rows = [json.loads(line) for line in open(session.log_path, encoding="utf-8")]
        self.assertAlmostEqual(rows[0]["usage"]["stt_audio_seconds"], 1.0, places=3)
        self.assertAlmostEqual(rows[1]["usage"]["stt_audio_seconds"], 2.0, places=3)
        self.assertAlmostEqual(sum(row["usage"]["stt_audio_seconds"] for row in rows), 3.0, places=3)

    def test_barge_in_cancels_the_lesson(self):
        """SpeechStarted while the tutor is thinking sends barge_in, keeps no history and logs barge_in.
        """
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            sent = []

            async def send(payload):
                """Fake browser socket: receives every message the session sends."""
                sent.append(payload)

            settings = load_settings("configs/baseline.yaml")
            llm = _SlowLLM()
            session = Session(
                settings,
                "key",
                send,
                case_id="b",
                session_id="s",
                stt_factory=_FakeSTT,
                llm=llm,
                tts=_FakeTTS(),
                log_path=os.path.join(tempfile.mkdtemp(), "t.jsonl"),
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            stt = session.stt
            await stt.send_audio(b"\x00\x00" * 160)
            # The slow lesson is in flight. A new SpeechStarted barges in.
            for _ in range(20):
                if session.state == "thinking":
                    break
                await asyncio.sleep(0.01)
            await session._on_stt({"type": "SpeechStarted", "timestamp": 10, "confidence": 0.99})
            self.assertTrue(any(isinstance(item, dict) and item.get("type") == "barge_in" for item in sent))
            self.assertEqual(session.history, [])
            await session.close()
            rows = [json.loads(line) for line in open(session.log_path, encoding="utf-8")]
            self.assertTrue(rows[0]["barge_in"])
            self.assertNotIn("detected_errors", rows[0]["llm"])

        _run(scenario())

    def test_replay_speaks_without_a_second_lesson(self):
        """Replay speaks the stored lesson again without another LLM call."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            sent = []

            async def send(payload):
                """Fake browser socket: receives every message the session sends."""
                sent.append(payload)

            settings = load_settings("configs/baseline.yaml")
            llm = _FakeLLM()
            session = Session(
                settings,
                "key",
                send,
                session_id="s",
                stt_factory=_FakeSTT,
                llm=llm,
                tts=_FakeTTS(),
                log_path=os.path.join(tempfile.mkdtemp(), "t.jsonl"),
            )
            await session.handle_text({"type": "start", "target_language": "fr"})
            await session.stt.send_audio(b"\x00\x00" * 80)
            await session.job
            self.assertEqual(len(llm.calls), 1)
            await session.handle_text({"type": "replay", "turn_order": 0})
            await session.job
            self.assertEqual(len(llm.calls), 1)
            kinds = [item.get("type") for item in sent if isinstance(item, dict)]
            self.assertGreaterEqual(kinds.count("tts_start"), 2)
            await session.close()

        _run(scenario())

    def test_second_start_closes_the_first_stt_session(self):
        """A second start closes the first STT stream; update_config and stop act on the new one."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            async def send(payload):
                """Fake browser socket: receives every message the session sends."""
                return None

            settings = load_settings("configs/baseline.yaml")
            session = Session(
                settings,
                "key",
                send,
                stt_factory=_FakeSTT,
                llm=_FakeLLM(),
                tts=_FakeTTS(),
                log_path=os.path.join(tempfile.mkdtemp(), "t.jsonl"),
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            first = session.stt
            await session.handle_text({"type": "start", "target_language": "de"})
            self.assertTrue(first.closed)
            self.assertIsNot(session.stt, first)
            await session.handle_text({"type": "update_config", "config": {"keyterms_prompt": ["Bahn"]}})
            self.assertEqual(session.stt.sent[-1]["keyterms_prompt"], ["Bahn"])
            await session.handle_text({"type": "stop"})
            self.assertTrue(session.stt is None or session.closed)

        _run(scenario())

    async def _ignore(self, payload):
        """Fake browser socket that drops every message."""
        return None


class MemoryTests(unittest.TestCase):
    """Chat history and turn ids."""
    def test_history_is_included_and_capped_at_six_exchanges(self):
        """History is sent to the LLM, capped at six exchanges, and cleared by set_target."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            llm = _FakeLLM()

            async def send(_payload):
                """Fake browser socket: receives every message the session sends."""
                return None

            session = Session(
                load_settings("configs/baseline.yaml"),
                "key",
                send,
                stt_factory=_FakeSTT,
                llm=llm,
                tts=_FakeTTS(),
                log_path=os.path.join(tempfile.mkdtemp(), "t.jsonl"),
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            await session.stt.send_audio(b"\x00\x00" * 80)
            await session.job
            self.assertEqual(llm.calls[0]["history"], [])
            self.assertEqual(len(session.history), 2)
            self.assertEqual(session.history[0]["role"], "user")
            self.assertIn("station", session.history[0]["content"])
            self.assertEqual(session.history[1]["role"], "assistant")
            await session._on_stt(
                {
                    "type": "Turn",
                    "turn_order": 1,
                    "end_of_turn": True,
                    "transcript": "hello",
                    "language_code": "en",
                    "language_confidence": 0.9,
                    "end_of_turn_confidence": 1,
                    "words": [{"text": "hello", "start": 0, "end": 10, "confidence": 0.9, "word_is_final": True}],
                }
            )
            await session.job
            self.assertEqual(llm.calls[1]["history"][0]["content"], session.history[0]["content"])
            self.assertIn("estación", llm.calls[1]["history"][1]["content"])
            # Add 8 more exchanges directly; only the last 6 (12 messages) must remain.
            for index in range(8):
                lesson = llm.calls[0]
                lesson = type("Lesson", (), {"translation": f"t{index}", "tip": "tip"})()
                session._remember(f"u{index}", lesson)
            self.assertEqual(len(session.history), 12)
            self.assertTrue(session.history[0]["content"].startswith("u"))
            self.assertIn("t7", session.history[-1]["content"])
            await session.handle_text({"type": "set_target", "target_language": "fr"})
            self.assertEqual(session.history, [])

        _run(scenario())

    def test_turn_id_climbs_across_a_restart(self):
        """turn_id keeps counting across a restart, and every message of one turn shares it."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            sent = []

            async def send(payload):
                """Fake browser socket: receives every message the session sends."""
                sent.append(payload)

            session = Session(
                load_settings("configs/baseline.yaml"),
                "key",
                send,
                stt_factory=_FakeSTT,
                llm=_FakeLLM(),
                tts=_FakeTTS(),
                log_path=os.path.join(tempfile.mkdtemp(), "t.jsonl"),
            )
            kinds = ("transcript", "lesson", "agent_text", "tts_start")

            async def one_turn():
                """Run one fake turn and return its turn_id (checking all messages share it)."""
                sent.clear()
                await session.stt.send_audio(b"\x00\x00" * 80)
                await session.job
                ids = [item["turn_id"] for item in sent if isinstance(item, dict) and item.get("type") in kinds]
                self.assertGreaterEqual(len(ids), 4)
                self.assertEqual(len(set(ids)), 1)
                return ids[0]

            await session.handle_text({"type": "start", "target_language": "es"})
            first = await one_turn()
            await session.handle_text({"type": "start", "target_language": "es"})
            second = await one_turn()
            self.assertEqual(first, 1)
            self.assertEqual(second, 2)
            self.assertEqual(session.history[0]["role"], "user")

        _run(scenario())


class WebSocketTests(unittest.TestCase):
    """The real FastAPI app and /ws endpoint, with fake clients."""
    def test_health_and_a_turn_over_the_socket(self):
        """/health answers, and one turn over /ws produces status, transcript, lesson, TTS messages and audio.
        """
        from fastapi.testclient import TestClient

        from server.app import create_app

        settings = load_settings("configs/baseline.yaml")
        log_path = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        app = create_app(
            settings,
            "test-key",
            stt_factory=_FakeSTT,
            llm=_FakeLLM(),
            tts=_FakeTTS(),
            log_path=log_path,
        )
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"ok": True})
            with client.websocket_connect("/ws") as socket:
                socket.send_json(
                    {
                        "type": "start",
                        "target_language": "es",
                        "config": {"language_codes": [], "prompt": "", "keyterms_prompt": [], "agent_context": ""},
                    }
                )
                socket.send_bytes(b"\x00\x00" * 160)
                kinds = []
                binary = 0
                for _ in range(30):
                    message = socket.receive()
                    if message.get("bytes"):
                        binary += 1
                        continue
                    body = json.loads(message["text"])
                    kinds.append(body["type"])
                    if body["type"] == "tts_end":
                        break
        self.assertIn("status", kinds)
        self.assertIn("transcript", kinds)
        self.assertIn("lesson", kinds)
        self.assertIn("agent_text", kinds)
        self.assertIn("tts_start", kinds)
        self.assertIn("tts_end", kinds)
        self.assertGreater(binary, 0)

    def test_missing_key_is_a_clean_error(self):
        """With no API key, start returns a clear error message instead of crashing."""
        from fastapi.testclient import TestClient

        from server.app import create_app

        app = create_app(load_settings("configs/baseline.yaml"), "")
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as socket:
                socket.send_json({"type": "start", "target_language": "es"})
                body = socket.receive_json()
        self.assertEqual(body["type"], "error")
        self.assertIn("ASSEMBLYAI_API_KEY", body["message"])


if __name__ == "__main__":
    unittest.main()
