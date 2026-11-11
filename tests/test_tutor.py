"""The conversational tutor: tutor-turn schema, practice matching, practice state, speech plan."""

import asyncio
import json
import os
import tempfile
import time
import unittest

from server.config import load_settings
from server.llm import LESSON_SCHEMA, Lesson, build_user_text, parse_lesson_text
from server.practice import check_attempt, fold, looks_like_attempt, match_score, slow_text, sound_key
from server.session import Session, speech_plans


def _run(coro):
    return asyncio.run(coro)


class SchemaTests(unittest.TestCase):
    def test_schema_has_every_tutor_field_required(self):
        schema = LESSON_SCHEMA["json_schema"]["schema"]
        for field in ("intent", "reply", "target_phrase", "pronunciation", "syllables", "tip",
                      "verdict", "verdict_reason", "next_prompt", "detected_errors"):
            self.assertIn(field, schema["properties"])
            self.assertIn(field, schema["required"])
        self.assertEqual(
            schema["properties"]["intent"]["enum"],
            ["teach_phrase", "pronunciation", "practice_attempt", "correction", "chat"],
        )
        self.assertIn("try_again", schema["properties"]["verdict"]["enum"])

    def test_parses_a_tutor_turn(self):
        parsed = parse_lesson_text(json.dumps({
            "intent": "practice_attempt", "reply": "Correct! Nice.", "target_phrase": "Hola",
            "pronunciation": "OH-lah", "syllables": "ho - la", "tip": "The h is silent.",
            "verdict": "Correct", "verdict_reason": "clear", "next_prompt": "Want another word?",
            "detected_errors": [],
        }))
        self.assertEqual(parsed["intent"], "practice_attempt")
        self.assertEqual(parsed["translation"], "Hola")
        self.assertEqual(parsed["verdict"], "correct")
        self.assertEqual(parsed["syllables"], "ho - la")

    def test_old_lesson_schema_still_parses(self):
        parsed = parse_lesson_text('{"translation":"Hola","pronunciation":"OH-la","tip":"t","detected_errors":[]}')
        self.assertEqual(parsed["translation"], "Hola")
        self.assertEqual(parsed["intent"], "teach_phrase")
        self.assertEqual(parsed["verdict"], "none")
        self.assertEqual(parsed["reply"], "")

    def test_unknown_intent_and_stray_verdict_are_normalized(self):
        parsed = parse_lesson_text('{"intent":"lesson","target_phrase":"","verdict":"correct","reply":"Hi!"}')
        self.assertEqual(parsed["intent"], "chat")
        self.assertEqual(parsed["verdict"], "none")

    def test_placeholders_are_not_spoken(self):
        parsed = parse_lesson_text('{"intent":"teach_phrase","reply":"In Spanish, hello is... {target_phrase}.","target_phrase":"Hola"}')
        self.assertNotIn("{", parsed["reply"])
        self.assertTrue(parsed["reply"].startswith("In Spanish, hello is"))

    def test_prompt_carries_practice_state(self):
        check = check_attempt("ola", "Hola", "es", [{"text": "ola", "confidence": 0.9}])
        text = build_user_text("ola", "es", 0.7, "spanish", "english", "Hola", check)
        self.assertIn("Current practice phrase: Hola", text)
        self.assertIn("Practice check", text)
        self.assertIn("verdict correct", text)
        self.assertTrue(text.endswith("ola"))
        self.assertIn("(none yet)", build_user_text("hi", "en", 0.9, "spanish"))


class RetryTests(unittest.TestCase):
    def test_non_json_reply_is_retried_once(self):
        import server.llm as llm

        replies = ["sure! hola", '{"intent":"teach_phrase","reply":"Hello is","target_phrase":"Hola"}']
        calls = []

        class Response:
            status_code = 200
            text = ""

            def __init__(self, content):
                self.content = content

            def json(self):
                return {"choices": [{"message": {"content": self.content}}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            calls.append(json)
            return Response(replies[len(calls) - 1])

        original = llm.requests.post
        llm.requests.post = fake_post
        try:
            lesson = _run(llm.GatewayLLM("k", {"model": "gemma-4-31b", "system_prompt": "x", "timeout_s": 9}).complete("hi", "en", 0.9, "spanish"))
        finally:
            llm.requests.post = original
        self.assertEqual(len(calls), 2)
        self.assertFalse(lesson.unparsed)
        self.assertTrue(lesson.retried)
        self.assertEqual(lesson.translation, "Hola")


class PracticeMatchTests(unittest.TestCase):
    def test_spelling_variants_of_hola_are_correct(self):
        for heard in ("hola", "Hola", "ola", "Ola.", "Hola!", "olá", "¡Hola!", "HOLA", "uh, hola"):
            with self.subTest(heard=heard):
                self.assertGreaterEqual(match_score(heard, "Hola", "es"), 0.85)
                self.assertEqual(check_attempt(heard, "Hola", "es")["verdict"], "correct")
                self.assertTrue(looks_like_attempt(heard, "Hola", "es"))

    def test_folding(self):
        self.assertEqual(fold("¡Olá!"), "ola")
        self.assertEqual(sound_key("Hola"), sound_key("ola"))
        self.assertEqual(sound_key("¿Cómo estás?"), sound_key("como estas"))
        self.assertEqual(sound_key("chico"), "chiko".replace("k", "c"))  # ch is kept

    def test_wrong_words_are_not_correct(self):
        self.assertEqual(check_attempt("adios", "Hola", "es")["verdict"], "try_again")
        self.assertFalse(looks_like_attempt("how do I pronounce it", "Hola", "es"))
        self.assertFalse(looks_like_attempt("how do I say goodbye in Spanish", "Hola", "es"))

    def test_close_attempt(self):
        result = check_attempt("buenos dia", "Buenos días", "es")
        self.assertIn(result["verdict"], ("correct", "close"))
        self.assertEqual(check_attempt("gracias", "Buenos días", "es")["verdict"], "try_again")
        self.assertEqual(check_attempt("hora", "Hola", "es")["verdict"], "close")

    def test_low_confidence_downgrades_correct(self):
        result = check_attempt("hola", "Hola", "es", [{"text": "hola", "confidence": 0.31}])
        self.assertEqual(result["verdict"], "close")
        self.assertEqual(result["low_confidence_words"], [{"text": "hola", "confidence": 0.31}])

    def test_slow_text(self):
        self.assertEqual(slow_text("Hola", "ho - la"), "ho... la.")
        self.assertEqual(slow_text("Buenos días", ""), "Buenos... días.")
        self.assertEqual(slow_text("Sí", ""), "")


def _turn(intent, phrase="Hola", reply="", verdict="none", next_prompt="", syllables="ho - la"):
    return Lesson(
        translation=phrase, pronunciation="OH-lah", tip="The h is silent.", detected_errors=[],
        model="gemma-4-31b", tokens_in=1, tokens_out=1, first_token_at=time.time(), done_at=time.time(),
        streamed=False, intent=intent, reply=reply, syllables=syllables, verdict=verdict,
        verdict_reason="", next_prompt=next_prompt,
    )


class SpeechPlanTests(unittest.TestCase):
    def test_teach_uses_two_voices(self):
        plan = speech_plans(_turn("teach_phrase", reply="In Spanish, hello is", next_prompt="Now you try."), "english", "spanish")
        self.assertEqual(
            [(kind, lang) for _t, lang, kind in plan["all"]],
            [("reply", "english"), ("phrase", "spanish"), ("next", "english")],
        )

    def test_pronunciation_reads_slowly_then_whole(self):
        plan = speech_plans(_turn("pronunciation", reply="Say it like OH-lah.", next_prompt="Now you try."), "english", "spanish")
        self.assertEqual([kind for _t, _l, kind in plan["all"]], ["reply", "slow", "phrase", "next"])
        self.assertEqual(plan["slow"][0][0], "ho... la.")

    def test_correct_attempt_does_not_repeat_the_phrase(self):
        correct = speech_plans(_turn("practice_attempt", reply="Correct!", verdict="correct", next_prompt="Another?"), "english", "spanish")
        self.assertEqual([kind for _t, _l, kind in correct["all"]], ["reply", "next"])
        close = speech_plans(_turn("practice_attempt", reply="Close!", verdict="close", next_prompt="Again."), "english", "spanish")
        self.assertEqual([kind for _t, _l, kind in close["all"]], ["reply", "phrase", "next"])

    def test_next_prompt_is_said_once(self):
        plan = speech_plans(
            _turn("practice_attempt", reply="Correct! Nice. Want to learn goodbye?", verdict="correct", next_prompt="Want to learn goodbye?"),
            "english", "spanish",
        )
        self.assertEqual([t for t, _l, _k in plan["all"]], ["Correct! Nice.", "Want to learn goodbye?"])

    def test_old_schema_speaks_only_the_phrase(self):
        plan = speech_plans(_turn("teach_phrase", reply="", next_prompt=""), "english", "spanish")
        self.assertEqual(plan["all"], [("Hola", "spanish", "phrase")])


class _ScriptedSTT:
    """Each send_audio emits the next scripted final turn."""

    script = []

    def __init__(self, api_key, params, on_event):
        self.on_event = on_event
        self.sent = []
        self.closed = False
        self.index = 0

    async def connect(self):
        return None

    async def send_audio(self, pcm):
        text, lang, conf = self.script[self.index]
        self.index += 1
        await self.on_event({
            "type": "Turn", "turn_order": self.index - 1, "end_of_turn": True, "transcript": text,
            "language_code": lang, "language_confidence": 0.8, "end_of_turn_confidence": 1,
            "words": [{"text": w, "start": 0, "end": 10, "confidence": conf, "word_is_final": True} for w in text.split()],
        })

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self):
        self.closed = True


class _ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        data = self.replies.pop(0)
        lesson = _turn(**data)
        return lesson


class _VoiceTTS:
    def __init__(self):
        self.spoken = []

    async def stream(self, text, language_name, meta):
        self.spoken.append((language_name, text))
        meta["sample_rate"] = 24000
        yield b"\x01\x00" * 4


class PracticeStateTests(unittest.TestCase):
    def _session(self, script, replies):
        _ScriptedSTT.script = script
        sent = []

        async def send(payload):
            sent.append(payload)

        llm = _ScriptedLLM(replies)
        tts = _VoiceTTS()
        session = Session(
            load_settings("configs/baseline.yaml"), "key", send,
            stt_factory=_ScriptedSTT, llm=llm, tts=tts,
            log_path=os.path.join(tempfile.mkdtemp(), "t.jsonl"),
        )
        return session, llm, tts, sent

    def test_teach_pronounce_then_attempt(self):
        async def scenario():
            session, llm, tts, sent = self._session(
                [
                    ("How do I say hello in Spanish?", "en", 0.99),
                    ("How do I pronounce it?", "en", 0.99),
                    ("ola", "es", 0.88),
                ],
                [
                    dict(intent="teach_phrase", reply="In Spanish, hello is", next_prompt="Now you try."),
                    dict(intent="pronunciation", reply="Say it like OH-lah.", next_prompt="Now you try."),
                    # The model mislabels the attempt; the practice check wins.
                    dict(intent="chat", phrase="", reply="Hi there!", next_prompt="Want another word?"),
                ],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            for _ in range(3):
                await session.stt.send_audio(b"\x00\x00")
                await session.job
            self.assertEqual(llm.calls[0]["practice_target"], "")
            self.assertIsNone(llm.calls[0]["practice_check"])
            self.assertEqual(llm.calls[1]["practice_target"], "Hola")
            self.assertIsNone(llm.calls[1]["practice_check"])
            self.assertEqual(llm.calls[2]["practice_check"]["verdict"], "correct")
            self.assertEqual(llm.calls[2]["native_name"], "english")
            lessons = [p for p in sent if isinstance(p, dict) and p.get("type") == "lesson"]
            self.assertEqual([l["intent"] for l in lessons], ["teach_phrase", "pronunciation", "practice_attempt"])
            self.assertEqual(lessons[2]["verdict"], "correct")
            self.assertEqual(lessons[2]["target_phrase"], "Hola")
            self.assertEqual([l["turn_id"] for l in lessons], [1, 2, 3])
            # Two voices: the reply in English, the phrase in Spanish, slowly for pronunciation.
            self.assertIn(("spanish", "Hola"), tts.spoken)
            self.assertIn(("spanish", "ho... la."), tts.spoken)
            self.assertIn(("english", "In Spanish, hello is"), tts.spoken)
            starts = [p for p in sent if isinstance(p, dict) and p.get("type") == "tts_start"]
            self.assertEqual([s["kind"] for s in starts[1]["segments"]], ["reply", "slow", "phrase", "next"])
            self.assertEqual(session.practice_target, "Hola")
            self.assertIn("practice_attempt: correct", session.history[-1]["content"])
            await session.handle_text({"type": "set_target", "target_language": "fr"})
            self.assertEqual(session.practice_target, "")

        _run(scenario())

    def test_replay_is_keyed_by_turn_id_across_restarts(self):
        async def scenario():
            session, llm, tts, sent = self._session(
                [("How do I say thank you?", "en", 0.99)],
                [
                    dict(intent="teach_phrase", phrase="Gracias", reply="Thank you is", next_prompt="Try it.", syllables="gra - cias"),
                    dict(intent="teach_phrase", phrase="Adiós", reply="Goodbye is", next_prompt="Try it.", syllables="a - diós"),
                ],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            await session.stt.send_audio(b"\x00\x00")
            await session.job
            _ScriptedSTT.script = [("How do I say goodbye?", "en", 0.99)]
            await session.handle_text({"type": "start", "target_language": "es"})
            await session.stt.send_audio(b"\x00\x00")
            await session.job
            # Both turns had turn_order 0; turn_id keeps them apart.
            tts.spoken.clear()
            await session.handle_text({"type": "replay", "turn_id": 1})
            await session.job
            self.assertEqual(tts.spoken, [("spanish", "Gracias")])
            tts.spoken.clear()
            await session.handle_text({"type": "replay", "turn_id": 2, "mode": "slow"})
            await session.job
            self.assertEqual(tts.spoken, [("spanish", "a... diós."), ("spanish", "Adiós")])
            tts.spoken.clear()
            await session.handle_text({"type": "replay", "turn_order": 0})
            await session.job
            self.assertEqual(tts.spoken, [("spanish", "Adiós")])
            ends = [p for p in sent if isinstance(p, dict) and p.get("type") == "tts_end"]
            self.assertEqual(ends[-1]["turn_id"], 2)

        _run(scenario())

    def test_short_target_language_turn_does_not_change_native_language(self):
        async def scenario():
            session, llm, tts, sent = self._session(
                [("hola", "es", 0.9), ("Wie sagt man danke auf Spanisch", "de", 0.95)],
                [dict(intent="chat", phrase="", reply="Hi!", next_prompt="Ask me a word."),
                 dict(intent="teach_phrase", phrase="Gracias", reply="Danke heißt", next_prompt="Jetzt du.")],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            await session.stt.send_audio(b"\x00\x00")
            await session.job
            self.assertEqual(session.native_language, "en")
            await session.stt.send_audio(b"\x00\x00")
            await session.job
            self.assertEqual(session.native_language, "de")
            self.assertEqual(llm.calls[1]["native_name"], "german")

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
