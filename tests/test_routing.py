"""Native-language turns are new phrases to teach, not attempts or small talk.

When a practice phrase is pending, the LLM decides whether the next turn is
an attempt at it or something new, and grades attempts itself. These tests
cover the prompt rules for that (server/config.py, server/llm.py), that the
session keeps the model's intent and verdict, cleanup of brace fragments in
model text, and the slow read after two misses (server/session.py).
"""

import unittest

from server.config import DEFAULT_SYSTEM_PROMPT
from server.llm import build_user_text, parse_lesson_text
from server.session import speech_plans
from tests import test_tutor
from tests.test_tutor import _run, _turn


class UserTextTests(unittest.TestCase):
    """The per-turn prompt leaves the attempt decision to the model."""
    def test_no_rule_based_routing_line(self):
        """A pending phrase adds no routing or verdict line; the model decides and grades."""
        text = build_user_text("Good morning", "en", 0.9, "spanish", "english", "Hola")
        self.assertNotIn("Routing", text)
        self.assertNotIn("verdict", text)
        self.assertTrue(text.endswith("Good morning"))


class PromptTests(unittest.TestCase):
    """The system prompt's rules for native-language turns."""
    def test_prompt_teaches_everything_said_in_the_native_language(self):
        """The prompt has its sections, treats native-language speech as "how do I say this?", and lists every JSON field.
        """
        for text in (
            "# Personality", "# Environment", "# Tone", "# Goal", "# Guardrails",
            "If they say anything in {native} (a word, a greeting, a question, a feeling, a full",
            "Never answer the learner's {native} sentence as small talk",
            "Nice! What else do you want to say?",
            "Now you say it.",
            "never write the {target} phrase inside reply",
            "no placeholders, no curly braces, no markdown",
            "A greeting or any other phrase in {native} is never chat",
        ):
            self.assertIn(text, DEFAULT_SYSTEM_PROMPT)
        for field in ("intent", "reply", "target_phrase", "pronunciation", "syllables", "tip",
                      "verdict", "verdict_reason", "next_prompt", "detected_errors"):
            self.assertIn(f"- {field}:", DEFAULT_SYSTEM_PROMPT)


class BraceFragmentTests(unittest.TestCase):
    """Cleanup of JSON debris the model leaves inside text fields."""
    def test_unclosed_brace_fragment_is_dropped(self):
        """Unclosed { fragments, stray closing braces and a trailing dash at the end of a field are removed.
        """
        parsed = parse_lesson_text(
            '{"intent":"teach_phrase","reply":"In Spanish, hello is","target_phrase":"Hola",'
            '"tip":"The H is always silent in Spanish. {C","next_prompt":"Now you say it. {",'
            '"verdict_reason":"","pronunciation":"OH-lah}","syllables":"ho - la\\"}]"}'
        )
        self.assertEqual(parsed["tip"], "The H is always silent in Spanish.")
        self.assertEqual(parsed["next_prompt"], "Now you say it.")
        self.assertEqual(parsed["pronunciation"], "OH-lah")
        self.assertEqual(parsed["syllables"], "ho - la")
        self.assertEqual(parse_lesson_text('{"verdict_reason":"sound is off.-"}')["verdict_reason"], "sound is off.")

    def test_clean_text_is_untouched(self):
        """Normal text is not changed by the cleanup."""
        parsed = parse_lesson_text('{"intent":"teach_phrase","target_phrase":"¿Cómo estás?","tip":"Rising tone, like a question."}')
        self.assertEqual(parsed["translation"], "¿Cómo estás?")
        self.assertEqual(parsed["tip"], "Rising tone, like a question.")


class SlowAfterTwoMissesTests(unittest.TestCase):
    """Speech plan after repeated misses."""
    def test_slow_read_after_misses(self):
        """With slow_read, a missed attempt reads syllables slowly, then the phrase."""
        plan = speech_plans(_turn("practice_attempt", reply="Not quite.", verdict="try_again", next_prompt="Try it once more."),
                            "english", "spanish", slow_read=True)
        self.assertEqual([kind for _t, _l, kind in plan["all"]], ["reply", "slow", "phrase", "next"])


class NativeTurnSessionTests(unittest.TestCase):
    """Native-language turns through a real Session."""
    _session = test_tutor.PracticeStateTests._session

    def test_model_intent_and_verdict_are_kept(self):
        """The session keeps what the model says: a new phrase is taught, an attempt keeps its verdict."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            session, llm, tts, sent = self._session(
                [
                    ("How do I say hello in Spanish?", "en", 0.99),
                    ("Good morning.", "en", 0.99),
                    ("buenos dias", "en", 0.9),
                    ("buenos dias", "es", 0.9),
                ],
                [
                    dict(intent="teach_phrase", reply="In Spanish, hello is", next_prompt="Now you say it."),
                    dict(intent="teach_phrase", phrase="Buenos días", reply="In Spanish, that's",
                         next_prompt="Now you say it.", syllables="bue - nos - dí - as"),
                    # The model grades this one close; nothing overrides it.
                    dict(intent="practice_attempt", phrase="", verdict="close", reply="Almost!",
                         next_prompt="Try it once more.", syllables="bue - nos - dí - as"),
                    dict(intent="practice_attempt", phrase="Buenos días", verdict="correct", reply="Correct!",
                         next_prompt="Nice! What else do you want to say?", syllables="bue - nos - dí - as"),
                ],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            for _ in range(4):
                await session.stt.send_audio(b"\x00\x00")
                await session.job
            self.assertEqual(llm.calls[1]["practice_target"], "Hola")
            self.assertEqual(llm.calls[2]["practice_target"], "Buenos días")
            self.assertEqual(llm.calls[3]["misses"], 1)
            lessons = [p for p in sent if isinstance(p, dict) and p.get("type") == "lesson"]
            self.assertEqual([l["intent"] for l in lessons], ["teach_phrase", "teach_phrase", "practice_attempt", "practice_attempt"])
            self.assertEqual([l["verdict"] for l in lessons], ["none", "none", "close", "correct"])
            # An empty phrase on an attempt is filled with the stored phrase.
            self.assertEqual(lessons[2]["target_phrase"], "Buenos días")
            self.assertEqual(session.practice_target, "Buenos días")
            self.assertEqual(session.misses, 0)
            self.assertNotIn("practice", lessons[2])

        _run(scenario())

    def test_two_misses_read_the_phrase_slowly(self):
        """After two misses on the same phrase, the reply includes the slow syllable read."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            session, llm, tts, sent = self._session(
                [("How do I say hello?", "en", 0.99), ("hora", "es", 0.9), ("hora", "es", 0.9)],
                [
                    dict(intent="teach_phrase", reply="Hello is", next_prompt="Now you say it."),
                    dict(intent="practice_attempt", verdict="close", reply="Close!", next_prompt="Try it once more."),
                    dict(intent="practice_attempt", verdict="close", reply="Almost! Let's break it down slowly.",
                         next_prompt="Now you say it."),
                ],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            for _ in range(3):
                await session.stt.send_audio(b"\x00\x00")
                await session.job
            self.assertEqual(llm.calls[1]["misses"], 0)
            self.assertEqual(llm.calls[2]["misses"], 1)
            starts = [p for p in sent if isinstance(p, dict) and p.get("type") == "tts_start"]
            self.assertEqual([s["kind"] for s in starts[1]["segments"]], ["reply", "phrase", "next"])
            self.assertEqual([s["kind"] for s in starts[2]["segments"]], ["reply", "slow", "phrase", "next"])
            lessons = [p for p in sent if isinstance(p, dict) and p.get("type") == "lesson"]
            self.assertEqual([(l["intent"], l["verdict"]) for l in lessons[1:]],
                             [("practice_attempt", "close"), ("practice_attempt", "close")])
            self.assertEqual(session.misses, 2)

        _run(scenario())



if __name__ == "__main__":
    unittest.main()
