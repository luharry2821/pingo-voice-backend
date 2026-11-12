"""Speech in a language Pingo cannot speak (Hindi, Japanese, Chinese...) is taught, not "didn't catch that".

Seen live with target Spanish: STT heard "मुझे पानी पीना है।" and "我想喝水。" correctly,
but the prompt only routed the learner's own language to teaching, so the model
answered "I'm sorry, I didn't quite catch that." STT's language_code was also
wrong on some turns (Japanese tagged "hi", Chinese tagged "ja").
"""

import unittest

from server.config import DEFAULT_SYSTEM_PROMPT
from server.llm import build_user_text
from server.practice import is_other_script, looks_like_attempt
from tests import test_tutor
from tests.test_tutor import _run


class OtherScriptTests(unittest.TestCase):
    def test_non_latin_scripts(self):
        for text in ("मुझे पानी पीना है।", "水が飲みたいです。", "我想喝水。", "Где вокзал?", "أين المحطة؟"):
            with self.subTest(text=text):
                self.assertTrue(is_other_script(text))
                self.assertFalse(looks_like_attempt(text, "Quiero agua", "es", "en"))

    def test_latin_scripts_and_empty(self):
        for text in ("Hola.", "Où est la gare ?", "Straße", "I'm hungry", "", "123 ?"):
            with self.subTest(text=text):
                self.assertFalse(is_other_script(text))


class OtherLanguageUserTextTests(unittest.TestCase):
    def test_fixed_target_routing_line(self):
        text = build_user_text("我想喝水。", "ja", 1.0, "spanish", "english", "", None, other_language="ja")
        self.assertIn("clear speech in another language", text)
        self.assertIn("Do not ask them to repeat it", text)
        self.assertIn("naturally say in spanish", text)
        self.assertIn("Use intent teach_phrase", text)
        self.assertTrue(text.endswith("我想喝水。"))
        self.assertNotIn("Routing", build_user_text("我想喝水。", "ja", 1.0, "spanish", "english", ""))

    def test_auto_target_routing_line(self):
        text = build_user_text("स्पेनिश में मैं कैसे कहूँ कि मुझे पानी चाहिए?", "hi", 0.76, "", "english", "",
                               None, auto_target=True, previous_target="", other_language="hi")
        self.assertIn("the language they ask for", text)
        self.assertIn("ask which one", text)
        text = build_user_text("我想喝水。", "ja", 1.0, "spanish", "english", "", None,
                               auto_target=True, previous_target="es", other_language="ja")
        self.assertIn("else the previous target language", text)
        self.assertNotIn("ask which one", text)

    def test_prompt_teaches_other_languages(self):
        self.assertIn("If they speak any other language", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("never a reason to ask them to repeat", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("neither is a clear sentence in any other language", DEFAULT_SYSTEM_PROMPT)


class OtherLanguageSessionTests(unittest.TestCase):
    _session = test_tutor.PracticeStateTests._session

    def test_other_language_turns_are_routed_to_teaching(self):
        async def scenario():
            session, llm, tts, sent = self._session(
                [
                    ("मुझे पानी पीना है।", "hi", 0.52),
                    # Live STT tagged this Japanese turn "hi".
                    ("水が飲みたいです。", "hi", 0.76),
                    ("我想喝水。", "en", 0.62),
                    ("Ik wil graag water drinken", "nl", 0.9),
                    # Too short to trust a Latin-script language code.
                    ("Hallo daar", "nl", 0.9),
                    ("I'm hungry.", "en", 0.99),
                ],
                [dict(intent="teach_phrase", phrase="Quiero agua", reply="In Spanish, that's",
                      next_prompt="Now you say it.")] * 6,
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            for _ in range(6):
                await session.stt.send_audio(b"\x00\x00")
                await session.job
            self.assertEqual([c["other_language"] for c in llm.calls], ["hi", "hi", "other", "nl", "", ""])
            self.assertFalse(llm.calls[0]["new_phrase"])
            self.assertTrue(llm.calls[5]["new_phrase"])
            # The explanation language stays one Pingo can speak.
            self.assertEqual({c["native_name"] for c in llm.calls}, {"english"})
            self.assertEqual(session.native_language, "en")

        _run(scenario())

    def test_latin_script_needs_words_and_confidence(self):
        session, _llm, _tts, _sent = self._session([], [])
        check = session._other_language
        self.assertEqual(check({"language_code": "nl", "language_confidence": 0.9}, "Ik wil graag water"), "nl")
        self.assertEqual(check({"language_code": "nl", "language_confidence": 0.4}, "Ik wil graag water"), "")
        self.assertEqual(check({"language_code": "nl", "language_confidence": None}, "Ik wil graag water"), "")
        self.assertEqual(check({"language_code": "fr", "language_confidence": 0.9}, "Je voudrais de l'eau"), "")
        self.assertEqual(check({"language_code": "es", "language_confidence": 0.3}, "我想喝水。"), "other")
        self.assertEqual(check({"language_code": None, "language_confidence": None}, "मुझे पानी पीना है।"), "other")

    def test_attempt_at_the_practice_phrase_is_still_checked(self):
        async def scenario():
            session, llm, tts, sent = self._session(
                [("मुझे पानी पीना है।", "hi", 0.8), ("quiero agua", "es", 0.9)],
                [dict(intent="teach_phrase", phrase="Quiero agua", reply="In Spanish, that's", next_prompt="Now you say it."),
                 dict(intent="practice_attempt", phrase="Quiero agua", verdict="correct", reply="Correct!",
                      next_prompt="Nice! What else do you want to say?")],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            for _ in range(2):
                await session.stt.send_audio(b"\x00\x00")
                await session.job
            self.assertEqual(llm.calls[0]["other_language"], "hi")
            self.assertEqual(llm.calls[1]["practice_check"]["verdict"], "correct")
            self.assertEqual(llm.calls[1]["other_language"], "")

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
