"""Native-language turns are new phrases to teach, not attempts or small talk."""

import unittest

from server.config import DEFAULT_SYSTEM_PROMPT
from server.llm import build_user_text, parse_lesson_text
from server.practice import is_native_phrase, looks_like_attempt
from server.session import speech_plans
from tests import test_tutor
from tests.test_tutor import _run, _turn


class NativePhraseRoutingTests(unittest.TestCase):
    def test_english_phrases_are_new_phrases_not_attempts(self):
        cases = [
            ("Hello", "Hola"),
            ("Hello.", "Hola"),
            ("Good morning", "Hola"),
            ("Night", "Hola"),
            ("How are you doing?", "Buenos días"),
            ("I'm hungry", "Hola"),
            ("coffee", "Café"),
            ("My name is", "Mi nombre es"),
            ("Good night", "Buenas noches"),
            ("Thank you", "Gracias"),
        ]
        for heard, target in cases:
            with self.subTest(heard=heard, target=target):
                self.assertTrue(is_native_phrase(heard, target, "en"))
                self.assertFalse(looks_like_attempt(heard, target, "es", "en"))

    def test_attempts_at_the_practice_phrase_still_route_to_the_check(self):
        cases = [
            ("hola", "Hola"),
            ("Ola.", "Hola"),
            ("oh la", "Hola"),
            ("buenos dias", "Buenos días"),
            ("cafe", "Café"),
            ("no", "No"),
            ("pizza", "Pizza"),
            ("mi nombre es", "Mi nombre es"),
        ]
        for heard, target in cases:
            with self.subTest(heard=heard, target=target):
                self.assertFalse(is_native_phrase(heard, target, "en"))
                self.assertTrue(looks_like_attempt(heard, target, "es", "en"))

    def test_only_english_has_a_word_list(self):
        self.assertFalse(is_native_phrase("Guten Morgen", "Hola", "de"))
        self.assertFalse(is_native_phrase("hello", "Hola", "de"))

    def test_user_text_routes_a_new_phrase(self):
        text = build_user_text("Good morning", "en", 0.9, "spanish", "english", "Hola", None, True)
        self.assertIn("Use intent teach_phrase", text)
        self.assertTrue(text.endswith("Good morning"))
        self.assertNotIn("Routing", build_user_text("Good morning", "en", 0.9, "spanish", "english", "Hola", None))


class PromptTests(unittest.TestCase):
    def test_prompt_teaches_everything_said_in_the_native_language(self):
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
    def test_unclosed_brace_fragment_is_dropped(self):
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
        parsed = parse_lesson_text('{"intent":"teach_phrase","target_phrase":"¿Cómo estás?","tip":"Rising tone, like a question."}')
        self.assertEqual(parsed["translation"], "¿Cómo estás?")
        self.assertEqual(parsed["tip"], "Rising tone, like a question.")


class SlowAfterTwoMissesTests(unittest.TestCase):
    def test_slow_read_after_misses(self):
        plan = speech_plans(_turn("practice_attempt", reply="Not quite.", verdict="try_again", next_prompt="Try it once more."),
                            "english", "spanish", slow_read=True)
        self.assertEqual([kind for _t, _l, kind in plan["all"]], ["reply", "slow", "phrase", "next"])


class NativeTurnSessionTests(unittest.TestCase):
    _session = test_tutor.PracticeStateTests._session

    def test_new_native_phrase_while_practising_is_taught(self):
        async def scenario():
            session, llm, tts, sent = self._session(
                [
                    ("How do I say hello in Spanish?", "en", 0.99),
                    ("Good morning.", "en", 0.99),
                    ("buenos dias", "en", 0.9),
                    ("Hello.", "en", 0.99),
                ],
                [
                    dict(intent="teach_phrase", reply="In Spanish, hello is", next_prompt="Now you say it."),
                    # The model wrongly judges it; routing makes it a new phrase.
                    dict(intent="practice_attempt", phrase="Buenos días", verdict="try_again", reply="In Spanish, that's",
                         next_prompt="Now you say it.", syllables="bue - nos - dí - as"),
                    dict(intent="practice_attempt", phrase="Buenos días", verdict="correct", reply="Correct!",
                         next_prompt="Nice! What else do you want to say?", syllables="bue - nos - dí - as"),
                    # Small talk for a greeting is turned into teaching.
                    dict(intent="chat", phrase="Hola", reply="In Spanish, that's", next_prompt="Now you say it."),
                ],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            for _ in range(4):
                await session.stt.send_audio(b"\x00\x00")
                await session.job
            self.assertEqual(llm.calls[1]["practice_target"], "Hola")
            self.assertIsNone(llm.calls[1]["practice_check"])
            self.assertTrue(llm.calls[1]["new_phrase"])
            self.assertEqual(llm.calls[2]["practice_target"], "Buenos días")
            self.assertEqual(llm.calls[2]["practice_check"]["verdict"], "correct")
            self.assertFalse(llm.calls[2]["new_phrase"])
            self.assertIsNone(llm.calls[3]["practice_check"])
            self.assertTrue(llm.calls[3]["new_phrase"])
            lessons = [p for p in sent if isinstance(p, dict) and p.get("type") == "lesson"]
            self.assertEqual([l["intent"] for l in lessons], ["teach_phrase", "teach_phrase", "practice_attempt", "teach_phrase"])
            self.assertEqual([l["verdict"] for l in lessons], ["none", "none", "correct", "none"])
            self.assertEqual(lessons[1]["target_phrase"], "Buenos días")
            self.assertEqual(session.practice_target, "Hola")
            self.assertIn(("spanish", "Buenos días"), tts.spoken)

        _run(scenario())

    def test_two_misses_read_the_phrase_slowly(self):
        async def scenario():
            session, llm, tts, sent = self._session(
                [("How do I say hello?", "en", 0.99), ("hora", "es", 0.9), ("hora", "es", 0.9)],
                [
                    dict(intent="teach_phrase", reply="Hello is", next_prompt="Now you say it."),
                    dict(intent="practice_attempt", verdict="close", reply="Close!", next_prompt="Try it once more."),
                    # Step 4 after two misses: the model says pronunciation; the verdict stays.
                    dict(intent="pronunciation", reply="Almost! Let's break it down slowly.", next_prompt="Now you say it."),
                ],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            for _ in range(3):
                await session.stt.send_audio(b"\x00\x00")
                await session.job
            self.assertEqual(llm.calls[1]["practice_check"]["misses_before"], 0)
            self.assertEqual(llm.calls[2]["practice_check"]["misses_before"], 1)
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
