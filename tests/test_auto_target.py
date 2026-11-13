"""Tests for auto-target mode: the learner names the language they want while they speak.

In auto-target mode the browser sends `start` without a target language. The
LLM tutor returns `target_language` each turn, and server/session.py checks
it (resolve_auto_target), switches voices, or answers with a canned question
when no teachable language is known. These tests drive a Session through
that flow with scripted STT/LLM fakes, and check the prompt and schema parts
in server/llm.py. No network.
"""

import time
import unittest

from server.config import DEFAULT_SYSTEM_PROMPT
from server.llm import AUTO_LESSON_SCHEMA, LESSON_SCHEMA, Lesson, build_user_text, parse_lesson_text, system_prompt
from server.session import ASK_TARGET, UNSUPPORTED_TARGET, resolve_auto_target
from tests import test_tutor
from tests.test_tutor import _run


def _auto_turn(intent="teach_phrase", phrase="", reply="", target="", next_prompt="", syllables="",
               verdict="none", pronunciation="", tip=""):
    """Build a Lesson that also carries target_language (what the model says the learner asked for).
    """
    return Lesson(
        translation=phrase, pronunciation=pronunciation, tip=tip, detected_errors=[],
        model="gemma-4-31b", tokens_in=1, tokens_out=1, first_token_at=time.time(), done_at=time.time(),
        streamed=False, intent=intent, reply=reply, syllables=syllables, verdict=verdict,
        verdict_reason="", next_prompt=next_prompt, target_language=target,
    )


class _AutoLLM(test_tutor._ScriptedLLM):
    """Scripted tutor whose lessons include target_language."""
    async def complete(self, **kwargs):
        """Record the call and return the next scripted auto-target lesson."""
        self.calls.append(kwargs)
        return _auto_turn(**self.replies.pop(0))


def _lessons(sent):
    """Only the `lesson` messages the session sent to the browser."""
    return [p for p in sent if isinstance(p, dict) and p.get("type") == "lesson"]


class AutoTargetSessionTests(unittest.TestCase):
    """Auto-target mode through a real Session."""
    def _session(self, script, replies):
        """A scripted Session (from test_tutor) with the auto-target LLM fake swapped in."""
        session, _llm, tts, sent = test_tutor.PracticeStateTests._session(self, script, [])
        llm = _AutoLLM(replies)
        session.llm = llm
        return session, llm, tts, sent

    async def _turns(self, session, count):
        """Play `count` scripted learner turns and wait for each reply."""
        for _ in range(count):
            await session.stt.send_audio(b"\x00\x00")
            await session.job

    def test_start_without_a_target_is_accepted(self):
        """start with auto_target and no language is accepted, with no target set yet."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            for value in (None, "", "auto"):
                session, llm, tts, sent = self._session([], [])
                start = {"type": "start", "auto_target": True}
                if value is not None:
                    start["target_language"] = value
                await session.handle_text(start)
                self.assertFalse([p for p in sent if isinstance(p, dict) and p.get("type") == "error"])
                self.assertIsNone(session.target)
                self.assertTrue(session.auto_target)
                self.assertEqual(sent[-1], {"type": "status", "state": "listening"})

        _run(scenario())

    def test_named_language_sets_the_target_and_the_voice(self):
        """Naming a language sets the target and the phrase is spoken in that language's voice."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            session, llm, tts, sent = self._session(
                [("How do I say thank you very much in French?", "en", 0.99)],
                [dict(phrase="Merci beaucoup", target="fr", reply="In French, that's",
                      next_prompt="Now you say it.", syllables="mer - ci - beau - coup")],
            )
            await session.handle_text({"type": "start", "auto_target": True})
            await self._turns(session, 1)
            self.assertTrue(llm.calls[0]["auto_target"])
            self.assertEqual(llm.calls[0]["previous_target"], "")
            lesson = _lessons(sent)[0]
            self.assertEqual(lesson["target_language"], "fr")
            self.assertTrue(lesson["auto_target"])
            self.assertEqual(lesson["source_language"], "en")
            self.assertEqual(lesson["native_language"], "en")
            self.assertEqual(lesson["target_phrase"], "Merci beaucoup")
            self.assertEqual(session.target, "fr")
            self.assertEqual(session.practice_target, "Merci beaucoup")
            self.assertIn(("french", "Merci beaucoup"), tts.spoken)
            self.assertIn(("english", "In French, that's"), tts.spoken)

        _run(scenario())

    def test_no_language_and_no_previous_target_asks(self):
        """With no language named and none before, Pingo asks which language (canned reply)."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            session, llm, tts, sent = self._session(
                [("How do I say good morning?", "en", 0.99), ("In Italian please.", "en", 0.99)],
                [
                    # The model guesses a phrase without a language; the session asks instead.
                    dict(intent="teach_phrase", phrase="Buenos días", target="", reply="Here it is"),
                    dict(phrase="Buongiorno", target="it", reply="In Italian, that's", next_prompt="Now you say it."),
                ],
            )
            await session.handle_text({"type": "start", "auto_target": True, "target_language": "auto"})
            await self._turns(session, 1)
            ask = _lessons(sent)[0]
            self.assertIsNone(ask["target_language"])
            self.assertEqual(ask["intent"], "chat")
            self.assertEqual(ask["target_phrase"], "")
            self.assertEqual(ask["reply"], ASK_TARGET["en"])
            self.assertEqual(tts.spoken, [("english", ASK_TARGET["en"])])
            # The answer names the language; the ask turn is in the history the model sees.
            await self._turns(session, 1)
            self.assertEqual(llm.calls[1]["previous_target"], "")
            self.assertEqual(llm.calls[1]["history"][0]["content"], "How do I say good morning?")
            self.assertEqual(_lessons(sent)[1]["target_language"], "it")
            self.assertIn(("italian", "Buongiorno"), tts.spoken)

        _run(scenario())

    def test_unsupported_language_is_a_chat(self):
        """A language TTS cannot speak becomes a chat explaining which languages Pingo teaches."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            session, llm, tts, sent = self._session(
                [("How do I say thank you in Japanese?", "en", 0.99)],
                [dict(phrase="Arigatou", target="ja", reply="In Japanese, that's")],
            )
            await session.handle_text({"type": "start", "auto_target": True})
            await self._turns(session, 1)
            lesson = _lessons(sent)[0]
            self.assertEqual(lesson["intent"], "chat")
            self.assertEqual(lesson["target_phrase"], "")
            self.assertIsNone(lesson["target_language"])
            self.assertEqual(lesson["reply"], UNSUPPORTED_TARGET["en"])
            self.assertEqual(tts.spoken, [("english", UNSUPPORTED_TARGET["en"])])

        _run(scenario())

    def test_target_follows_requests_and_native_follows_the_learner(self):
        """The target follows each request while the native language follows what the learner speaks.
        """
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            session, llm, tts, sent = self._session(
                [
                    ("How do I say thank you very much in French?", "en", 0.99),
                    ("¿Cómo se dice buenos días en alemán?", "es", 0.99),
                    ("guten morgen", "de", 0.95),
                    ("I'm hungry, how do I say that?", "en", 0.99),
                ],
                [
                    dict(phrase="Merci beaucoup", target="fr", reply="In French, that's", syllables="mer - ci - beau - coup"),
                    dict(phrase="Guten Morgen", target="de", reply="En alemán, se dice", syllables="gu - ten - mor - gen"),
                    # A practice attempt: the model leaves target_language empty, so German stays.
                    dict(intent="practice_attempt", phrase="Guten Morgen", target="", verdict="correct",
                         reply="¡Correcto!", syllables="gu - ten - mor - gen"),
                    dict(phrase="Ich habe Hunger", target="", reply="In German, that's", syllables="ich - ha - be - hun - ger"),
                ],
            )
            await session.handle_text({"type": "start", "auto_target": True})
            await self._turns(session, 4)
            lessons = _lessons(sent)
            self.assertEqual([l["target_language"] for l in lessons], ["fr", "de", "de", "de"])
            self.assertEqual([l["source_language"] for l in lessons], ["en", "es", "de", "en"])
            # Spanish question -> Spanish explanations; the short German attempt does not flip it.
            self.assertEqual([l["native_language"] for l in lessons], ["en", "es", "es", "en"])
            self.assertEqual([c["previous_target"] for c in llm.calls], ["", "fr", "de", "de"])
            self.assertEqual(llm.calls[2]["practice_target"], "Guten Morgen")
            self.assertEqual(lessons[2]["verdict"], "correct")
            self.assertEqual(lessons[3]["target_phrase"], "Ich habe Hunger")
            self.assertIn(("spanish", "En alemán, se dice"), tts.spoken)
            self.assertIn(("german", "Ich habe Hunger"), tts.spoken)
            # Switching French -> German reset the practice phrase and the history.
            self.assertEqual(llm.calls[1]["practice_target"], "Merci beaucoup")
            self.assertEqual(llm.calls[2]["practice_target"], "Guten Morgen")
            self.assertNotIn("Merci", " ".join(m["content"] for m in session.history))

        _run(scenario())

    def test_fixed_target_mode_is_unchanged(self):
        """Without auto_target, the fixed target language behaves as before."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            session, llm, tts, sent = test_tutor.PracticeStateTests._session(
                self,
                [("How do I say hello in French?", "en", 0.99)],
                [dict(intent="teach_phrase", reply="In Spanish, hello is", next_prompt="Now you say it.")],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            await self._turns(session, 1)
            self.assertNotIn("auto_target", llm.calls[0])
            lesson = _lessons(sent)[0]
            self.assertNotIn("auto_target", lesson)
            self.assertEqual(lesson["target_language"], "es")
            self.assertIn(("spanish", "Hola"), tts.spoken)
            # Without auto_target a missing target still falls back to the configured one.
            await session.handle_text({"type": "start"})
            self.assertEqual(session.target, "es")

        _run(scenario())


class AutoTargetPartsTests(unittest.TestCase):
    """The pure helpers behind auto-target mode."""
    def test_resolve(self):
        """resolve_auto_target accepts codes and names, keeps the previous target on blanks, flags unsupported ones.
        """
        self.assertEqual(resolve_auto_target("fr", None), ("fr", False))
        self.assertEqual(resolve_auto_target("German", "fr"), ("de", False))
        self.assertEqual(resolve_auto_target("", "es"), ("es", False))
        self.assertEqual(resolve_auto_target(None, None), (None, False))
        self.assertEqual(resolve_auto_target("ja", "es"), ("es", True))
        self.assertEqual(resolve_auto_target("hindi", None), (None, True))
        self.assertEqual(resolve_auto_target("the target language", "it"), ("it", False))

    def test_parse_and_schema(self):
        """target_language is parsed and normalised, and is required only in the auto schema."""
        parsed = parse_lesson_text('{"intent":"teach_phrase","target_phrase":"Danke","target_language":" DE "}')
        self.assertEqual(parsed["target_language"], "de")
        self.assertEqual(parse_lesson_text('{"intent":"chat"}')["target_language"], "")
        props = AUTO_LESSON_SCHEMA["json_schema"]["schema"]
        self.assertIn("target_language", props["required"])
        self.assertNotIn("target_language", LESSON_SCHEMA["json_schema"]["schema"]["required"])

    def test_prompt_and_user_text(self):
        """The auto-target prompt block and "Previous target language" line appear only in auto mode.
        """
        prompt = system_prompt(DEFAULT_SYSTEM_PROMPT, "", "english", auto_target=True)
        self.assertIn("# Auto-target mode", prompt)
        self.assertNotIn("{target}", prompt)
        fixed = system_prompt(DEFAULT_SYSTEM_PROMPT, "spanish", "english")
        self.assertIn("speak Spanish out loud", fixed)
        self.assertNotIn("Auto-target", fixed)
        text = build_user_text("hola", "es", 0.9, "", "english", auto_target=True, previous_target="fr")
        self.assertTrue(text.startswith("Previous target language: fr."))
        self.assertIn("Previous target language: none.", build_user_text("hi", "en", 0.9, "", auto_target=True))


if __name__ == "__main__":
    unittest.main()
