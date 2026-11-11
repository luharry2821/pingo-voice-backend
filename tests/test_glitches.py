"""Model glitches seen on Render: junk after the phrase, control characters in it."""

import json
import unittest

import server.llm as llm
from server.llm import parse_lesson_text
from tests import test_tutor
from tests.test_tutor import _run


def _reply(**fields):
    base = {
        "intent": "teach_phrase", "reply": "In Spanish, that's", "target_phrase": "Hola",
        "pronunciation": "OH-lah", "syllables": "ho - la", "tip": "", "verdict": "none",
        "verdict_reason": "", "next_prompt": "Now you say it.", "detected_errors": [],
    }
    base.update(fields)
    return json.dumps(base, ensure_ascii=False)


class ControlCharacterTests(unittest.TestCase):
    def test_del_in_phrase_and_syllables_is_flagged_and_stripped(self):
        parsed = parse_lesson_text(_reply(target_phrase="Buenos d\x7fas", syllables="Bue - nos - d\x7fas"))
        self.assertTrue(parsed["corrupt"])
        self.assertNotIn("\x7f", parsed["translation"])
        self.assertNotIn("\x7f", parsed["syllables"])

    def test_escaped_control_and_replacement_characters(self):
        for bad in ("\\u007f", "\\u0000", "\\u001b", "\\ufffd"):
            with self.subTest(bad=bad):
                raw = '{"intent":"teach_phrase","target_phrase":"Buenos d' + bad + 'as","syllables":"bue - nos"}'
                parsed = parse_lesson_text(raw)
                self.assertTrue(parsed["corrupt"])
                self.assertEqual(parsed["translation"], "Buenos das")

    def test_control_characters_in_other_fields_are_stripped(self):
        parsed = parse_lesson_text(_reply(tip="The d is soft\x7f.", reply="In Spanish,\x00 that's"))
        self.assertFalse(parsed["corrupt"])
        self.assertEqual(parsed["tip"], "The d is soft.")
        self.assertEqual(parsed["reply"], "In Spanish, that's")

    def test_clean_accents_are_not_corrupt(self):
        parsed = parse_lesson_text(_reply(target_phrase="¿Cómo estás?", syllables="có - mo es - tás"))
        self.assertFalse(parsed["corrupt"])
        self.assertEqual(parsed["translation"], "¿Cómo estás?")


class _Response:
    status_code = 200
    text = ""

    def __init__(self, content):
        payload = {"choices": [{"message": {"content": content}}], "usage": {}}
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")


class RetryOnCorruptionTests(unittest.TestCase):
    def _complete(self, replies):
        calls = []

        def fake_post(url, headers=None, json=None, timeout=None):
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
        lesson, calls = self._complete([
            _reply(target_phrase="Buenos d\x7fas", syllables="bue - nos - d\x7fas"),
            _reply(target_phrase="Buenos días", syllables="bue - nos - dí - as"),
        ])
        self.assertEqual(len(calls), 2)
        self.assertTrue(lesson.retried)
        self.assertEqual(lesson.translation, "Buenos días")
        self.assertEqual(lesson.syllables, "bue - nos - dí - as")

    def test_corrupt_twice_falls_back_to_the_stripped_first_reply(self):
        lesson, calls = self._complete([
            _reply(target_phrase="Buenos d\x7fas", syllables="bue - nos"),
            _reply(target_phrase="Bue\ufffdnos", syllables="bue - nos"),
        ])
        self.assertEqual(len(calls), 2)
        self.assertEqual(lesson.translation, "Buenos das")

    def test_multibyte_utf8_body_decodes(self):
        lesson, calls = self._complete([_reply(target_phrase="Buenos días", syllables="bue - nos - dí - as")])
        self.assertEqual(len(calls), 1)
        self.assertEqual(lesson.translation, "Buenos días")
        self.assertFalse(lesson.corrupt)


class JunkTailTests(unittest.TestCase):
    def test_tail_not_in_the_syllables_is_dropped(self):
        parsed = parse_lesson_text(_reply(target_phrase="Hola. corticosteroids", syllables="ho - la"))
        self.assertEqual(parsed["translation"], "Hola")

    def test_two_sentence_phrase_covered_by_syllables_is_kept(self):
        parsed = parse_lesson_text(_reply(target_phrase="¿Cómo estás? ¿Y tú?", syllables="có - mo es - tás - y - tú"))
        self.assertEqual(parsed["translation"], "¿Cómo estás? ¿Y tú?")


class JunkWordTests(unittest.TestCase):
    def test_stray_word_after_the_last_sentence_is_dropped(self):
        parsed = parse_lesson_text(_reply(
            intent="practice_attempt", verdict="correct", reply="Correct! You nailed it. corticosteroids",
            next_prompt="Nice! What else do you want to say? corticosteroids", tip="The h is silent. corticosteroids",
        ))
        self.assertEqual(parsed["reply"], "Correct! You nailed it.")
        self.assertEqual(parsed["next_prompt"], "Nice! What else do you want to say?")
        self.assertEqual(parsed["tip"], "The h is silent.")

    def test_real_sentences_are_kept(self):
        for text in ("Nice! What else do you want to say?", "Almost! Try it once more", "Say it like OH-lah.", "In Spanish, that's"):
            with self.subTest(text=text):
                self.assertEqual(parse_lesson_text(_reply(next_prompt=text))["next_prompt"], text)


class PinnedPracticePhraseTests(unittest.TestCase):
    _session = test_tutor.PracticeStateTests._session

    def test_attempts_and_breakdowns_use_the_stored_phrase(self):
        async def scenario():
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
                    dict(intent="pronunciation", phrase="Holaa", reply="Say it like OH-lah.", syllables="o - l - a",
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


if __name__ == "__main__":
    unittest.main()
