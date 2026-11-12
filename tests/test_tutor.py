"""Tests for the conversational tutor: tutor-turn schema, practice matching, practice state, speech plan.

These sit in the middle of the pipeline (STT -> **practice check + LLM tutor**
-> TTS): the JSON schema and parsing in server/llm.py, the deterministic
attempt scoring in server/practice.py, speech_plans() in server/session.py,
and full teach / pronounce / attempt / replay flows through a Session with
scripted fakes. No network.
"""

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
    """Run a coroutine to completion on a fresh event loop."""
    return asyncio.run(coro)


class SchemaTests(unittest.TestCase):
    """The tutor-turn JSON schema, parsing and the per-turn user prompt."""
    def test_schema_has_every_tutor_field_required(self):
        """Every tutor field is in the strict schema and required; intent and verdict enums are right.
        """
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
        """A full tutor turn parses, with the verdict normalised to lowercase."""
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
        """The older lesson schema (translation, no intent) still parses with sensible defaults."""
        parsed = parse_lesson_text('{"translation":"Hola","pronunciation":"OH-la","tip":"t","detected_errors":[]}')
        self.assertEqual(parsed["translation"], "Hola")
        self.assertEqual(parsed["intent"], "teach_phrase")
        self.assertEqual(parsed["verdict"], "none")
        self.assertEqual(parsed["reply"], "")

    def test_unknown_intent_and_stray_verdict_are_normalized(self):
        """An unknown intent becomes chat, and a verdict outside a practice attempt becomes none."""
        parsed = parse_lesson_text('{"intent":"lesson","target_phrase":"","verdict":"correct","reply":"Hi!"}')
        self.assertEqual(parsed["intent"], "chat")
        self.assertEqual(parsed["verdict"], "none")

    def test_placeholders_are_not_spoken(self):
        """Template slots like {target_phrase} copied by the model are removed from the reply."""
        parsed = parse_lesson_text('{"intent":"teach_phrase","reply":"In Spanish, hello is... {target_phrase}.","target_phrase":"Hola"}')
        self.assertNotIn("{", parsed["reply"])
        self.assertTrue(parsed["reply"].startswith("In Spanish, hello is"))

    def test_prompt_carries_practice_state(self):
        """The user prompt includes the practice phrase and check, and ends with the transcript."""
        check = check_attempt("ola", "Hola", "es", [{"text": "ola", "confidence": 0.9}])
        text = build_user_text("ola", "es", 0.7, "spanish", "english", "Hola", check)
        self.assertIn("Current practice phrase: Hola", text)
        self.assertIn("Practice check", text)
        self.assertIn("verdict correct", text)
        self.assertTrue(text.endswith("ola"))
        self.assertIn("(none yet)", build_user_text("hi", "en", 0.9, "spanish"))


class RetryTests(unittest.TestCase):
    """Retry of unusable model replies."""
    def test_non_json_reply_is_retried_once(self):
        """A non-JSON first reply is retried once and the good retry is used (marked retried)."""
        import server.llm as llm

        replies = ["sure! hola", '{"intent":"teach_phrase","reply":"Hello is","target_phrase":"Hola"}']
        calls = []

        class Response:
            """Minimal fake of a requests.Response."""
            status_code = 200
            text = ""

            def __init__(self, content):
                """Hold the model text this response will carry."""
                self.content = content

            def json(self):
                """Gateway-shaped body with the model text as the first choice."""
                return {"choices": [{"message": {"content": self.content}}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            """Stand-in for requests.post: records the call and returns a canned response."""
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
    """Deterministic matching of an attempt to the practice phrase."""
    def test_spelling_variants_of_hola_are_correct(self):
        """STT spellings of "Hola" (ola, hola., ...) all score as correct attempts."""
        for heard in ("hola", "Hola", "ola", "Ola.", "Hola!", "olá", "¡Hola!", "HOLA", "uh, hola"):
            with self.subTest(heard=heard):
                self.assertGreaterEqual(match_score(heard, "Hola", "es"), 0.85)
                self.assertEqual(check_attempt(heard, "Hola", "es")["verdict"], "correct")
                self.assertTrue(looks_like_attempt(heard, "Hola", "es"))

    def test_folding(self):
        """Folding removes case, accents and punctuation; the sound key ignores silent h but keeps ch.
        """
        self.assertEqual(fold("¡Olá!"), "ola")
        self.assertEqual(sound_key("Hola"), sound_key("ola"))
        self.assertEqual(sound_key("¿Cómo estás?"), sound_key("como estas"))
        self.assertEqual(sound_key("chico"), "chiko".replace("k", "c"))  # ch is kept

    def test_wrong_words_are_not_correct(self):
        """A different word is try_again, and questions are not treated as attempts."""
        self.assertEqual(check_attempt("adios", "Hola", "es")["verdict"], "try_again")
        self.assertFalse(looks_like_attempt("how do I pronounce it", "Hola", "es"))
        self.assertFalse(looks_like_attempt("how do I say goodbye in Spanish", "Hola", "es"))

    def test_close_attempt(self):
        """A near miss is close; an unrelated word is try_again."""
        result = check_attempt("buenos dia", "Buenos días", "es")
        self.assertIn(result["verdict"], ("correct", "close"))
        self.assertEqual(check_attempt("gracias", "Buenos días", "es")["verdict"], "try_again")
        self.assertEqual(check_attempt("hora", "Hola", "es")["verdict"], "close")

    def test_low_confidence_downgrades_correct(self):
        """A spelling match heard with low STT confidence is downgraded to close."""
        result = check_attempt("hola", "Hola", "es", [{"text": "hola", "confidence": 0.31}])
        self.assertEqual(result["verdict"], "close")
        self.assertEqual(result["low_confidence_words"], [{"text": "hola", "confidence": 0.31}])

    def test_slow_text(self):
        """The slow read uses syllables, falls back to words, and is empty for one word without syllables.
        """
        self.assertEqual(slow_text("Hola", "ho - la"), "ho... la.")
        self.assertEqual(slow_text("Buenos días", ""), "Buenos... días.")
        self.assertEqual(slow_text("Sí", ""), "")


def _turn(intent, phrase="Hola", reply="", verdict="none", next_prompt="", syllables="ho - la"):
    """Build a Lesson with the given tutor-turn fields (handy defaults for 'Hola')."""
    return Lesson(
        translation=phrase, pronunciation="OH-lah", tip="The h is silent.", detected_errors=[],
        model="gemma-4-31b", tokens_in=1, tokens_out=1, first_token_at=time.time(), done_at=time.time(),
        streamed=False, intent=intent, reply=reply, syllables=syllables, verdict=verdict,
        verdict_reason="", next_prompt=next_prompt,
    )


class SpeechPlanTests(unittest.TestCase):
    """speech_plans(): which segments are spoken, in which voice, for each kind of turn."""
    def test_teach_uses_two_voices(self):
        """Teaching speaks the reply in the learner's voice and the phrase in the target voice."""
        plan = speech_plans(_turn("teach_phrase", reply="In Spanish, hello is", next_prompt="Now you try."), "english", "spanish")
        self.assertEqual(
            [(kind, lang) for _t, lang, kind in plan["all"]],
            [("reply", "english"), ("phrase", "spanish"), ("next", "english")],
        )

    def test_pronunciation_reads_slowly_then_whole(self):
        """A pronunciation turn reads the syllables slowly, then the whole phrase."""
        plan = speech_plans(_turn("pronunciation", reply="Say it like OH-lah.", next_prompt="Now you try."), "english", "spanish")
        self.assertEqual([kind for _t, _l, kind in plan["all"]], ["reply", "slow", "phrase", "next"])
        self.assertEqual(plan["slow"][0][0], "ho... la.")

    def test_correct_attempt_does_not_repeat_the_phrase(self):
        """A correct attempt is not followed by the phrase again; a close one is."""
        correct = speech_plans(_turn("practice_attempt", reply="Correct!", verdict="correct", next_prompt="Another?"), "english", "spanish")
        self.assertEqual([kind for _t, _l, kind in correct["all"]], ["reply", "next"])
        close = speech_plans(_turn("practice_attempt", reply="Close!", verdict="close", next_prompt="Again."), "english", "spanish")
        self.assertEqual([kind for _t, _l, kind in close["all"]], ["reply", "phrase", "next"])

    def test_next_prompt_is_said_once(self):
        """A next prompt the model also appended to the reply is spoken only once."""
        plan = speech_plans(
            _turn("practice_attempt", reply="Correct! Nice. Want to learn goodbye?", verdict="correct", next_prompt="Want to learn goodbye?"),
            "english", "spanish",
        )
        self.assertEqual([t for t, _l, _k in plan["all"]], ["Correct! Nice.", "Want to learn goodbye?"])

    def test_old_schema_speaks_only_the_phrase(self):
        """An old-schema lesson (no reply) speaks just the phrase."""
        plan = speech_plans(_turn("teach_phrase", reply="", next_prompt=""), "english", "spanish")
        self.assertEqual(plan["all"], [("Hola", "spanish", "phrase")])


class _ScriptedSTT:
    """Each send_audio emits the next scripted final turn."""

    script = []

    def __init__(self, api_key, params, on_event):
        """Same signature as StreamingSTT; starts at the first scripted turn."""
        self.on_event = on_event
        self.sent = []
        self.closed = False
        self.index = 0

    async def connect(self):
        """Pretend to connect (nothing to do)."""
        return None

    async def send_audio(self, pcm):
        """Emit the next scripted (transcript, language, word confidence) as a final Turn."""
        text, lang, conf = self.script[self.index]
        self.index += 1
        await self.on_event({
            "type": "Turn", "turn_order": self.index - 1, "end_of_turn": True, "transcript": text,
            "language_code": lang, "language_confidence": 0.8, "end_of_turn_confidence": 1,
            "words": [{"text": w, "start": 0, "end": 10, "confidence": conf, "word_is_final": True} for w in text.split()],
        })

    async def send_json(self, payload):
        """Record an UpdateConfiguration message."""
        self.sent.append(payload)

    async def close(self):
        """Mark the fake stream closed."""
        self.closed = True


class _ScriptedLLM:
    """Fake tutor that returns scripted lessons in order and records each call."""
    def __init__(self, replies):
        """Queue of lesson field dicts to return, one per call."""
        self.replies = list(replies)
        self.calls = []

    async def complete(self, **kwargs):
        """Record the call arguments and return the next scripted lesson."""
        self.calls.append(kwargs)
        data = self.replies.pop(0)
        lesson = _turn(**data)
        return lesson


class _VoiceTTS:
    """Fake TTS that records (language, text) for every spoken segment."""
    def __init__(self):
        """Start with nothing spoken."""
        self.spoken = []

    async def stream(self, text, language_name, meta):
        """Record the segment and yield one tiny PCM chunk."""
        self.spoken.append((language_name, text))
        meta["sample_rate"] = 24000
        yield b"\x01\x00" * 4


class PracticeStateTests(unittest.TestCase):
    """Practice state across several turns of a real Session."""
    def _session(self, script, replies):
        """Build a Session wired to scripted STT/LLM fakes; returns (session, llm, tts, sent)."""
        _ScriptedSTT.script = script
        sent = []

        async def send(payload):
            """Fake browser socket: receives every message the session sends."""
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
        """Teach -> pronounce -> attempt: practice phrase, check, intents, voices and history all line up.
        """
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
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
        """Replay finds lessons by turn_id across an STT restart, in phrase and slow modes."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
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
        """A short target-language turn keeps the native language; a longer confident one changes it.
        """
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
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
