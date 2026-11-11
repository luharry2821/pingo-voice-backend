"""Pipeline tests with fake AssemblyAI clients. No network."""

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


def _settings(**sets):
    settings = load_settings("configs/baseline.yaml", [f"{key}={value}" for key, value in sets.items()])
    return settings


class ConfigTests(unittest.TestCase):
    def test_baseline_sends_mode_interruption_delay_and_prompt(self):
        settings = load_settings("configs/baseline.yaml")
        params = stt_query_params(settings["stt"])
        self.assertEqual(params["speech_model"], "universal-3-6-pro")
        self.assertEqual(params["mode"], "balanced")
        self.assertEqual(params["interruption_delay"], 500)
        self.assertIn("language learner", params["prompt"])
        self.assertIs(params["language_detection"], True)
        self.assertNotIn("keyterms_prompt", params)
        self.assertNotIn("language_codes", params)
        self.assertNotIn("end_of_turn_confidence_threshold", params)
        self.assertNotIn("llm_gateway", params)

    def test_keyterms_config_sends_terms(self):
        settings = load_settings("configs/keyterms_on.yaml")
        params = stt_query_params(settings["stt"])
        self.assertIn("gracias", params["keyterms_prompt"])

    def test_empty_client_prompt_does_not_wipe_the_server_prompt(self):
        settings = load_settings("configs/baseline.yaml")
        merged = merge_session_config(
            settings,
            {"language_codes": [], "prompt": "", "keyterms_prompt": [], "agent_context": ""},
        )
        self.assertIn("language learner", merged["stt"]["prompt"])
        self.assertEqual(merged["stt"]["keyterms_prompt"], [])

    def test_env_and_set_override(self):
        settings = load_settings(
            "configs/baseline.yaml",
            ["stt.max_turn_silence=1800", "llm.temperature=0"],
        )
        self.assertEqual(settings["stt"]["max_turn_silence"], 1800)
        self.assertEqual(settings["llm"]["temperature"], 0.0)
        self.assertNotIn("end_of_turn_confidence_threshold", settings["stt"])

    def test_update_configuration_forwards_supported_fields(self):
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
        settings = default_settings()
        self.assertEqual(bind_address(settings, env={}), ("127.0.0.1", 8000))
        self.assertEqual(bind_address(settings, env={"PORT": "10000"}), ("127.0.0.1", 10000))
        self.assertEqual(
            bind_address(settings, host="0.0.0.0", port=443, env={"PORT": "10000"}),
            ("0.0.0.0", 443),
        )

    def test_system_prompt_refuses_silent_correction(self):
        settings = load_settings("configs/baseline.yaml")
        text = settings["llm"]["system_prompt"]
        self.assertIn("Never silently correct", text)
        self.assertIn("name the specific difference", text)


class AudioTests(unittest.TestCase):
    def test_resample_doubles_an_8k_clip(self):
        pcm = b"\x00\x10" * 8000
        out = resample_pcm16(pcm, 8000, 16000)
        self.assertEqual(len(out), 16000 * 2)

    def test_wav_stereo_becomes_16k_mono(self):
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
    def test_parses_fenced_json_and_stringifies_errors(self):
        text = """```json
        {"translation": "Tengo frío.", "pronunciation": "TEN-go FREE-o", "tip": "Soy is for identity.",
         "detected_errors": [{"heard": "soy", "issue": "tengo"}]}
        ```"""
        parsed = parse_lesson_text(text)
        self.assertEqual(parsed["translation"], "Tengo frío.")
        self.assertEqual(error_strings(parsed["detected_errors"]), ["soy -> tengo"])

    def test_plain_text_is_the_translation(self):
        parsed = parse_lesson_text("just say hola")
        self.assertEqual(parsed["translation"], "just say hola")
        self.assertEqual(parsed["pronunciation"], "")
        self.assertEqual(parsed["detected_errors"], [])

    def test_playground_post_shape_and_usage(self):
        import server.llm as llm

        captured = {}

        class Response:
            status_code = 200
            text = ""

            def json(self):
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
        finally:
            llm.requests.post = original
        self.assertEqual(captured["url"], "https://llm-gateway.assemblyai.com/v1/chat/completions")
        self.assertEqual(captured["headers"], {"authorization": "secret"})
        self.assertEqual(captured["json"]["model"], "qwen3.5-4b-32k-fast")
        self.assertEqual([item["role"] for item in captured["json"]["messages"]], ["system", "user"])
        self.assertNotIn("response_format", captured["json"])
        self.assertEqual(set(captured["json"]), {"model", "messages"})
        self.assertEqual(lesson.translation, "Hola")
        self.assertEqual(lesson.tokens_in, 11)
        self.assertEqual(lesson.tokens_out, 7)


class _FakeSTT:
    def __init__(self, api_key, params, on_event):
        self.params = params
        self.on_event = on_event
        self.audio = bytearray()
        self.sent = []
        self.closed = False

    async def connect(self):
        async def begin():
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
        self.sent.append(payload)

    async def close(self):
        self.closed = True


class _FakeLLM:
    def __init__(self):
        self.calls = []

    async def complete(self, **kwargs):
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
    def __init__(self, chunks=(b"\x01\x00" * 12, b"\x02\x00" * 8)):
        self.chunks = chunks

    async def stream(self, text, language_name, meta):
        meta["sample_rate"] = 24000
        meta["billed_chars"] = len(text)
        for chunk in self.chunks:
            yield chunk


class _SlowLLM(_FakeLLM):
    async def complete(self, **kwargs):
        await asyncio.sleep(1)
        return await super().complete(**kwargs)


def _run(coro):
    return asyncio.run(coro)


class SessionTests(unittest.TestCase):
    def test_file_mode_writes_the_eval_line_and_appends(self):
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
        for key in ("speech_end", "stt_final", "llm_first_token", "llm_done", "tts_first_audio"):
            self.assertGreater(row["timings_ms"][key], 1_000_000_000_000)
        # speech_end is the last word's end mapped onto the same wall clock.
        self.assertGreaterEqual(row["timings_ms"]["stt_final"], row["timings_ms"]["speech_end"] - 40)
        self.assertGreaterEqual(row["timings_ms"]["tts_first_audio"], row["timings_ms"]["llm_done"])
        # 0.1s of audio plus 2s of endpointing silence.
        self.assertAlmostEqual(row["usage"]["stt_audio_seconds"], 2.1, places=2)
        self.assertEqual(row["usage"]["llm_input_tokens"], 120)
        self.assertEqual(row["usage"]["llm_output_tokens"], 40)
        self.assertGreater(row["usage"]["tts_characters"], 0)
        self.assertEqual(row["config"]["mode"], "balanced")
        self.assertEqual(row["config"]["llm_model"], "qwen3.5-4b-32k-fast")
        self.assertEqual(row["config"]["llm_done"], "full_response")
        self.assertFalse(row["barge_in"])
        self.assertEqual(row["stt_partial_first"], "how do I say")

        write_turn(log_path, row)
        with open(log_path, encoding="utf-8") as handle:
            self.assertEqual(len(handle.readlines()), 2)

    def test_audio_seconds_sum_across_turns(self):
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
        async def scenario():
            sent = []

            async def send(payload):
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
            await session.close()
            rows = [json.loads(line) for line in open(session.log_path, encoding="utf-8")]
            self.assertTrue(rows[0]["barge_in"])
            self.assertNotIn("detected_errors", rows[0]["llm"])

        _run(scenario())

    def test_replay_speaks_without_a_second_lesson(self):
        async def scenario():
            sent = []

            async def send(payload):
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
        async def scenario():
            async def send(payload):
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
        return None


class WebSocketTests(unittest.TestCase):
    def test_health_and_a_turn_over_the_socket(self):
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
