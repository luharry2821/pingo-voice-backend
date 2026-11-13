"""Model glitches seen on Render: junk after the phrase, control characters in it.

Each test reproduces a real bad reply from the tutor model and checks that
server/llm.py cleans it before anything is shown or spoken: control
characters are stripped (and trigger one retry when they hit the phrase),
stray words after the last sentence are dropped, and practice attempts keep
the stored phrase instead of whatever the model echoed. No network.
"""

import json
import unittest

import server.llm as llm
from server.llm import parse_lesson_text
from archive.tests import test_tutor
from archive.tests.test_tutor import _run


def _reply(**fields):
    """Model reply JSON for a teach turn of "Hola", with any fields overridden."""
    base = {
        "intent": "teach_phrase", "reply": "In Spanish, that's", "target_phrase": "Hola",
        "pronunciation": "OH-lah", "syllables": "ho - la", "tip": "", "verdict": "none",
        "verdict_reason": "", "next_prompt": "Now you say it.", "detected_errors": [],
    }
    base.update(fields)
    return json.dumps(base, ensure_ascii=False)


class ControlCharacterTests(unittest.TestCase):
    """Control and replacement characters in the model's JSON."""
    def test_del_in_phrase_and_syllables_is_flagged_and_stripped(self):
        """A DEL character in the phrase or syllables is removed and the reply flagged corrupt."""
        parsed = parse_lesson_text(_reply(target_phrase="Buenos d\x7fas", syllables="Bue - nos - d\x7fas"))
        self.assertTrue(parsed["corrupt"])
        self.assertNotIn("\x7f", parsed["translation"])
        self.assertNotIn("\x7f", parsed["syllables"])

    def test_escaped_control_and_replacement_characters(self):
        """JSON-escaped control/replacement characters in the phrase are also caught and stripped.
        """
        for bad in ("\\u007f", "\\u0000", "\\u001b", "\\ufffd"):
            with self.subTest(bad=bad):
                raw = '{"intent":"teach_phrase","target_phrase":"Buenos d' + bad + 'as","syllables":"bue - nos"}'
                parsed = parse_lesson_text(raw)
                self.assertTrue(parsed["corrupt"])
                self.assertEqual(parsed["translation"], "Buenos das")

    def test_control_characters_in_other_fields_are_stripped(self):
        """Control characters in other fields are stripped without flagging the reply corrupt."""
        parsed = parse_lesson_text(_reply(tip="The d is soft\x7f.", reply="In Spanish,\x00 that's"))
        self.assertFalse(parsed["corrupt"])
        self.assertEqual(parsed["tip"], "The d is soft.")
        self.assertEqual(parsed["reply"], "In Spanish, that's")

    def test_clean_accents_are_not_corrupt(self):
        """Ordinary accents and ¿ ? are not mistaken for corruption."""
        parsed = parse_lesson_text(_reply(target_phrase="¿Cómo estás?", syllables="có - mo es - tás"))
        self.assertFalse(parsed["corrupt"])
        self.assertEqual(parsed["translation"], "¿Cómo estás?")


class _Response:
    """Fake gateway response with a raw UTF-8 body (like the real one)."""
    status_code = 200
    text = ""

    def __init__(self, content):
        """Wrap the model text in a gateway-shaped JSON body, encoded as UTF-8 bytes."""
        payload = {"choices": [{"message": {"content": content}}], "usage": {}}
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")


class RetryOnCorruptionTests(unittest.TestCase):
    """The single retry when the phrase comes back corrupt."""
    def _complete(self, replies):
        """Call GatewayLLM.complete with requests.post faked to return `replies` in order; return (lesson, calls).
        """
        calls = []

        def fake_post(url, headers=None, json=None, timeout=None):
            """Record the request body and return the next scripted reply."""
            calls.append(json)
            return _Response(replies[len(calls) - 1])

        original = llm.requests.post
        llm.requests.post = fake_post
        try:
            lesson = _run(llm.GatewayLLM("k", {"model": "gemma-4-31b", "system_prompt": "x", "timeout_s": 9}).complete("Good morning", "en", 0.9, "spanish"))
        finally:
            llm.requests.post = original
        return lesson, calls

    def test_corrupt_phrase_is_retried_once(self):
        """A corrupt phrase triggers one retry and the clean retry is used."""
        lesson, calls = self._complete([
            _reply(target_phrase="Buenos d\x7fas", syllables="bue - nos - d\x7fas"),
            _reply(target_phrase="Buenos días", syllables="bue - nos - dí - as"),
        ])
        self.assertEqual(len(calls), 2)
        self.assertTrue(lesson.retried)
        self.assertEqual(lesson.translation, "Buenos días")
        self.assertEqual(lesson.syllables, "bue - nos - dí - as")

    def test_corrupt_twice_falls_back_to_the_stripped_first_reply(self):
        """If the retry is corrupt too, the cleaned first reply is used."""
        lesson, calls = self._complete([
            _reply(target_phrase="Buenos d\x7fas", syllables="bue - nos"),
            _reply(target_phrase="Bue\ufffdnos", syllables="bue - nos"),
        ])
        self.assertEqual(len(calls), 2)
        self.assertEqual(lesson.translation, "Buenos das")

    def test_multibyte_utf8_body_decodes(self):
        """A clean UTF-8 body with accents decodes correctly without a retry."""
        lesson, calls = self._complete([_reply(target_phrase="Buenos días", syllables="bue - nos - dí - as")])
        self.assertEqual(len(calls), 1)
        self.assertEqual(lesson.translation, "Buenos días")
        self.assertFalse(lesson.corrupt)


class JunkTailTests(unittest.TestCase):
    """Junk after the phrase that the syllables do not cover."""
    def test_tail_not_in_the_syllables_is_dropped(self):
        """"Hola. corticosteroids" with syllables "ho - la" becomes "Hola"."""
        parsed = parse_lesson_text(_reply(target_phrase="Hola. corticosteroids", syllables="ho - la"))
        self.assertEqual(parsed["translation"], "Hola")

    def test_two_sentence_phrase_covered_by_syllables_is_kept(self):
        """A real two-sentence phrase that the syllables cover is kept whole."""
        parsed = parse_lesson_text(_reply(target_phrase="¿Cómo estás? ¿Y tú?", syllables="có - mo es - tás - y - tú"))
        self.assertEqual(parsed["translation"], "¿Cómo estás? ¿Y tú?")


class JunkWordTests(unittest.TestCase):
    """A stray word after the last sentence in spoken fields."""
    def test_stray_word_after_the_last_sentence_is_dropped(self):
        """A stray word after the last sentence is removed from reply, next_prompt and tip."""
        parsed = parse_lesson_text(_reply(
            intent="practice_attempt", verdict="correct", reply="Correct! You nailed it. corticosteroids",
            next_prompt="Nice! What else do you want to say? corticosteroids", tip="The h is silent. corticosteroids",
        ))
        self.assertEqual(parsed["reply"], "Correct! You nailed it.")
        self.assertEqual(parsed["next_prompt"], "Nice! What else do you want to say?")
        self.assertEqual(parsed["tip"], "The h is silent.")

    def test_real_sentences_are_kept(self):
        """Normal sentences (including ones without final punctuation) are left alone."""
        for text in ("Nice! What else do you want to say?", "Almost! Try it once more", "Say it like OH-lah.", "In Spanish, that's"):
            with self.subTest(text=text):
                self.assertEqual(parse_lesson_text(_reply(next_prompt=text))["next_prompt"], text)


class PinnedPracticePhraseTests(unittest.TestCase):
    """The stored practice phrase wins over what the model echoes."""
    _session = test_tutor.PracticeStateTests._session

    def test_attempts_and_breakdowns_use_the_stored_phrase(self):
        """Attempts and pronunciation breakdowns use the stored phrase; a clearly new phrase replaces it.
        """
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            session, llm_, tts, sent = self._session(
                [
                    ("Hello.", "en", 0.99),
                    ("Hola.", "es", 0.9),
                    ("How do I pronounce it?", "en", 0.99),
                    ("How do I pronounce good night?", "en", 0.99),
                ],
                [
                    dict(intent="teach_phrase", phrase="Hola", reply="In Spanish, that's", next_prompt="Now you say it."),
                    dict(intent="practice_attempt", phrase="Hola corticosteroids", verdict="correct", reply="Correct!",
                         next_prompt="Nice! What else do you want to say?", syllables="x - y"),
                    dict(intent="pronunciation", phrase="¡hola!", reply="Say it like OH-lah.", syllables="o - l - a",
                         next_prompt="Now you say it."),
                    dict(intent="pronunciation", phrase="Buenas noches", reply="Say it like BWEH-nahs NOH-chehs.",
                         syllables="bue - nas - no - ches", next_prompt="Now you say it."),
                ],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            for _ in range(4):
                await session.stt.send_audio(b"\x00\x00")
                await session.job
            lessons = [p for p in sent if isinstance(p, dict) and p.get("type") == "lesson"]
            self.assertEqual([l["target_phrase"] for l in lessons], ["Hola", "Hola", "Hola", "Buenas noches"])
            self.assertEqual([l["syllables"] for l in lessons], ["ho - la", "ho - la", "ho - la", "bue - nas - no - ches"])
            self.assertEqual(lessons[1]["verdict"], "correct")
            self.assertEqual(session.practice_target, "Buenas noches")
            self.assertIn(("spanish", "ho... la."), tts.spoken)

        _run(scenario())


class PinPracticePhraseTests(unittest.TestCase):
    """_pin_practice_phrase: echoes are pinned to the stored phrase, a new longer phrase is not."""

    def _session(self):
        from archive.tests.test_tutor import PracticeStateTests

        session, _llm, _tts, _sent = PracticeStateTests._session(PracticeStateTests(), [], [])
        session.practice_target = "Hola"
        session.practice_card = {"pronunciation": "OH-lah", "syllables": "ho - la"}
        return session

    def _pin(self, intent, phrase):
        from types import SimpleNamespace

        lesson = SimpleNamespace(intent=intent, translation=phrase, pronunciation="x", syllables="y")
        self._session()._pin_practice_phrase(lesson)
        return lesson

    def test_pronunciation_of_a_longer_phrase_is_a_new_phrase(self):
        lesson = self._pin("pronunciation", "Hola, ¿cómo estás?")
        self.assertEqual(lesson.translation, "Hola, ¿cómo estás?")
        self.assertEqual(lesson.syllables, "y")

    def test_pronunciation_of_the_same_or_no_phrase_is_pinned(self):
        for phrase in ("", "hola", "¡Hola!"):
            with self.subTest(phrase=phrase):
                lesson = self._pin("pronunciation", phrase)
                self.assertEqual(lesson.translation, "Hola")
                self.assertEqual(lesson.syllables, "ho - la")

    def test_attempt_echo_is_still_pinned(self):
        self.assertEqual(self._pin("practice_attempt", "Hola. corticosteroids").translation, "Hola")

    def test_teach_turn_is_never_pinned(self):
        self.assertEqual(self._pin("teach_phrase", "Hola, ¿cómo estás?").translation, "Hola, ¿cómo estás?")

    def test_new_longer_phrase_becomes_the_practice_target(self):
        from types import SimpleNamespace

        session = self._session()
        session.misses = 2
        lesson = SimpleNamespace(intent="pronunciation", translation="Hola, ¿cómo estás?", pronunciation="OH-lah KOH-moh",
                                 syllables="ho - la, co - mo es - tas", reply="", verdict="none", detected_errors=[])
        session._apply_practice(lesson)
        self.assertEqual(session.practice_target, "Hola, ¿cómo estás?")
        self.assertEqual(session.misses, 0)


if __name__ == "__main__":
    unittest.main()
