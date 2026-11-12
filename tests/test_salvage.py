"""A tutor reply the model cut off mid-loop still teaches its finished fields."""

import time
import unittest

from server import llm
from server.llm import Lesson, salvage_lesson
from tests.test_tutor import _run

# Live, "Kiitos." with target Spanish: the reply looped until max_tokens.
LOOPED_REPLY = (
    '{"target_phrase":"Gracias","verdict":"none","detected_errors":[],"intent":"teach_phrase",'
    '"pronunciation":"GRA-syas","syllables":"gra - cias","tip":"The \'ci\' sounds like \'sy\' in English.",'
    '"verdict_reason":"","next_prompt":"Now you say it.","reply":"In Spanish, that\'s... Now you say it. '
    '{target_phrase} is not in the reply. Let me restart. In Spanish, tha'
)
# Live, "Terima kasih.": the loop was inside syllables.
LOOPED_SYLLABLES = (
    '{"tip":"The c sounds like an s.","verdict":"none","detected_errors":[],"intent":"teach_phrase",'
    '"verdict_reason":"","next_prompt":"Now you say it.","reply":"In Spanish, that\'s...",'
    '"target_phrase":"Gracias","pronunciation":"GRA-syas","syllables":"gra - cias-t-i-a-s-t-i-a-s-t-i-'
)


def _cut(raw):
    lesson = Lesson("", "", "", [], "gemma-4-31b", 1, 450, None, time.time(), False)
    lesson.unparsed = True
    lesson.raw_text = raw
    return lesson


class SalvageTests(unittest.TestCase):
    def test_finished_fields_are_kept(self):
        lesson = salvage_lesson(_cut(LOOPED_REPLY))
        self.assertFalse(lesson.unparsed)
        self.assertTrue(lesson.salvaged)
        self.assertEqual(lesson.translation, "Gracias")
        self.assertEqual(lesson.intent, "teach_phrase")
        self.assertEqual(lesson.syllables, "gra - cias")
        self.assertEqual(lesson.reply, "")
        self.assertEqual(lesson.next_prompt, "Now you say it.")
        self.assertEqual(lesson.tokens_out, 450)

        lesson = salvage_lesson(_cut(LOOPED_SYLLABLES))
        self.assertEqual(lesson.translation, "Gracias")
        self.assertEqual(lesson.syllables, "")
        self.assertEqual(lesson.reply, "In Spanish, that's")

    def test_nothing_to_teach_is_not_salvaged(self):
        self.assertIsNone(salvage_lesson(_cut('{"intent":"teach_phrase","reply":"In Spanish","target_phrase":"Gra')))
        self.assertIsNone(salvage_lesson(_cut("not json at all")))
        self.assertIsNone(salvage_lesson(_cut("")))

    def test_post_retries_first_then_salvages(self):
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
            return Response([LOOPED_REPLY, LOOPED_SYLLABLES][len(calls) - 1])

        original = llm.requests.post
        llm.requests.post = fake_post
        try:
            lesson = _run(llm.GatewayLLM("k", {"model": "gemma-4-31b", "system_prompt": "x", "timeout_s": 9})
                          .complete("Kiitos.", "fi", 0.9, "spanish"))
        finally:
            llm.requests.post = original
        self.assertEqual(len(calls), 2)
        self.assertFalse(lesson.unparsed)
        self.assertTrue(lesson.salvaged)
        self.assertEqual(lesson.translation, "Gracias")


if __name__ == "__main__":
    unittest.main()
