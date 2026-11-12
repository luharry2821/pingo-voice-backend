"""A tutor reply the model cut off mid-loop still teaches its finished fields.

Seen live: the model sometimes repeats inside one JSON string until it hits
max_tokens, so the JSON never closes. server/llm.py salvage_lesson() keeps
the string fields that were complete and drops the unfinished one, but only
if there is a target phrase to teach. These tests use two real looped replies.
"""

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
    # Positional args: translation, pronunciation, tip, detected_errors, model,
    # tokens_in, tokens_out (450 = hit max_tokens), first_token_at, done_at, streamed.
    """An unparsed Lesson carrying `raw` as its text, like a reply cut off at max_tokens."""
    lesson = Lesson("", "", "", [], "gemma-4-31b", 1, 450, None, time.time(), False)
    lesson.unparsed = True
    lesson.raw_text = raw
    return lesson


class SalvageTests(unittest.TestCase):
    """salvage_lesson() and where it sits in the retry flow."""
    def test_finished_fields_are_kept(self):
        """Complete fields are kept, the looping field is dropped, and token counts carry over."""
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
        """No complete target phrase (or no JSON at all) means nothing is salvaged."""
        self.assertIsNone(salvage_lesson(_cut('{"intent":"teach_phrase","reply":"In Spanish","target_phrase":"Gra')))
        self.assertIsNone(salvage_lesson(_cut("not json at all")))
        self.assertIsNone(salvage_lesson(_cut("")))

    def test_post_retries_first_then_salvages(self):
        """A cut-off reply is retried once first; when the retry is cut off too, it is salvaged."""
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
