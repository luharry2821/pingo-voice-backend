"""Speech in a language Pingo cannot speak (Hindi, Japanese, Chinese...) is taught, not "didn't catch that".

Seen live with target Spanish: STT heard "मुझे पानी पीना है।" and "我想喝水。" correctly,
but the prompt only routed the learner's own language to teaching, so the model
answered "I'm sorry, I didn't quite catch that." STT's language_code was also
wrong on some turns (Japanese tagged "hi", Chinese tagged "ja").

These tests cover the routing between STT and the LLM tutor: the script
check and turn classification in server/session.py and the "other
language" hint in the user prompt (server/llm.py). Whether a turn is an
attempt at the practice phrase is the model's call.
"""

import unittest

from server.config import DEFAULT_SYSTEM_PROMPT
from server.llm import build_user_text
from server.session import is_other_script
from tests import test_tutor
from tests.test_tutor import _run


class OtherScriptTests(unittest.TestCase):
    """is_other_script(): detecting non-Latin writing."""
    def test_non_latin_scripts(self):
        """Devanagari, Japanese, Chinese, Cyrillic and Arabic text is another script."""
        for text in ("मुझे पानी पीना है।", "水が飲みたいです。", "我想喝水。", "Где вокзал?", "أين المحطة؟"):
            with self.subTest(text=text):
                self.assertTrue(is_other_script(text))

    def test_latin_scripts_and_empty(self):
        """Latin-script text (with accents) and empty text do not."""
        for text in ("Hola.", "Où est la gare ?", "Straße", "I'm hungry", "", "123 ?"):
            with self.subTest(text=text):
                self.assertFalse(is_other_script(text))


class OtherLanguageUserTextTests(unittest.TestCase):
    """The routing lines added to the per-turn user prompt."""
    def test_pending_phrase_lets_the_model_judge_a_mislabelled_attempt(self):
        """With a practice phrase pending, the other-language line says an attempt is still judged as one."""
        text = build_user_text("Ola", "pt", 0.5, "spanish", "english", "Hola", other_language="pt")
        self.assertIn("judge it as a practice_attempt instead", text)
        text = build_user_text("我想喝水。", "ja", 1.0, "spanish", "english", "", other_language="ja")
        self.assertNotIn("practice_attempt", text)

    def test_fixed_target_routing_line(self):
        """With a fixed target, the other-language line tells the model to teach it in that target.
        """
        text = build_user_text("我想喝水。", "ja", 1.0, "spanish", "english", "", other_language="ja")
        self.assertIn("clear speech in another language", text)
        self.assertIn("Do not ask them to repeat it", text)
        self.assertIn("naturally say in spanish", text)
        self.assertIn("Use intent teach_phrase", text)
        self.assertTrue(text.endswith("我想喝水。"))
        self.assertNotIn("Routing", build_user_text("我想喝水。", "ja", 1.0, "spanish", "english", ""))

    def test_prompt_teaches_other_languages(self):
        """The system prompt says speech in any other language is taught, never a reason to ask again.
        """
        self.assertIn("If they speak any other language", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("A sentence in another language is never a practice attempt", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("never a reason to ask them to repeat", DEFAULT_SYSTEM_PROMPT)
        self.assertIn("neither is a clear sentence in any other language", DEFAULT_SYSTEM_PROMPT)


class OtherLanguageSessionTests(unittest.TestCase):
    """How a Session routes turns in other languages."""
    _session = test_tutor.PracticeStateTests._session

    def test_other_language_turns_are_routed_to_teaching(self):
        """Hindi and Chinese turns reach the LLM with an other_language hint and get taught."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
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
            # Any STT code outside the learner's language and the target counts, even short turns.
            self.assertEqual([c["other_language"] for c in llm.calls], ["hi", "hi", "other", "nl", "nl", ""])
            # The explanation language stays one Pingo can speak.
            self.assertEqual({c["native_name"] for c in llm.calls}, {"english"})
            self.assertEqual(session.native_language, "en")

        _run(scenario())

    def test_code_outside_native_and_target_counts_above_the_floor(self):
        """A Latin-script STT language outside native and target counts as other at 0.5+; non-Latin script always does.
        """
        session, _llm, _tts, _sent = self._session([], [])
        session.target = "es"
        check = session._other_language
        self.assertEqual(check({"language_code": "nl", "language_confidence": 0.9}, "Ik wil graag water"), "nl")
        self.assertEqual(check({"language_code": "nl", "language_confidence": 0.5}, "Ik wil graag water"), "nl")
        # Below the floor, or with no score, there is no routing hint (the prompt still has the label and score).
        self.assertEqual(check({"language_code": "nl", "language_confidence": 0.4}, "Goedemorgen"), "")
        self.assertEqual(check({"language_code": "de", "language_confidence": 0.31}, "Wo ist der Bahnhof?"), "")
        self.assertEqual(check({"language_code": "fr", "language_confidence": None}, "Je voudrais de l'eau"), "")
        # Eval: "Yo es hambre." (Spanish) came back as French at 0.26.
        self.assertEqual(check({"language_code": "fr", "language_confidence": 0.255}, "Yo es hambre."), "")
        # Another language Pingo teaches is still translated into the target.
        self.assertEqual(check({"language_code": "fr", "language_confidence": 0.9}, "Je voudrais de l'eau"), "fr")
        self.assertEqual(check({"language_code": "es", "language_confidence": 0.3}, "Quiero agua"), "")
        self.assertEqual(check({"language_code": "en", "language_confidence": 0.9}, "I want water"), "")
        self.assertEqual(check({"language_code": None, "language_confidence": None}, "Hola"), "")
        self.assertEqual(check({"language_code": "es", "language_confidence": 0.3}, "我想喝水。"), "other")
        self.assertEqual(check({"language_code": None, "language_confidence": None}, "मुझे पानी पीना है।"), "other")

    def test_foreign_sentence_close_to_the_phrase_is_not_an_attempt(self):
        """Live: French/Italian/Portuguese coffee orders were judged "Almost!" against the Spanish one.

        One confident French or Italian sentence is flagged as another language to
        teach (not an attempt); two in a row make it the explanation language.
        """
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
            session, llm, tts, sent = self._session(
                [
                    ("Quisiera una taza de café, por favor.", "en", 0.9),
                    ("Je voudrais une tasse de café, s'il vous plaît.", "fr", 0.9),
                    ("Vorrei una tazza di caffè, per favore.", "it", 0.9),
                    ("Quisiera una tasa de cafe", "es", 0.9),
                ],
                [dict(intent="teach_phrase", phrase="Quisiera una taza de café, por favor", reply="In Spanish, that's",
                      next_prompt="Now you say it.")] * 3
                + [dict(intent="practice_attempt", phrase="Quisiera una taza de café, por favor", verdict="close",
                        reply="Almost!", next_prompt="Try it once more.")],
            )
            await session.handle_text({"type": "start", "target_language": "es"})
            for _ in range(4):
                await session.stt.send_audio(b"\x00\x00")
                await session.job
            # One French turn does not change the learner's language; it is flagged as
            # another language to translate and teach. Italian next is a different
            # language, so the count restarts and English stays.
            self.assertEqual(llm.calls[1]["native_name"], "english")
            self.assertEqual(llm.calls[1]["other_language"], "fr")
            self.assertEqual(llm.calls[2]["native_name"], "english")
            self.assertEqual(llm.calls[2]["other_language"], "it")
            # A near-miss in Spanish goes to the model with no other-language hint;
            # the model grades it.
            self.assertEqual(llm.calls[3]["other_language"], "")
            lessons = [p for p in sent if isinstance(p, dict) and p.get("type") == "lesson"]
            self.assertEqual([l["intent"] for l in lessons], ["teach_phrase", "teach_phrase", "teach_phrase", "practice_attempt"])
            self.assertEqual(session.misses, 1)
            self.assertEqual(session.native_language, "en")

        _run(scenario())

    def test_attempt_at_the_practice_phrase_is_still_checked(self):
        """After teaching from another language, a real attempt reaches the model and keeps its verdict."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
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
            self.assertEqual(llm.calls[1]["practice_target"], "Quiero agua")
            self.assertEqual(llm.calls[1]["other_language"], "")
            lessons = [p for p in sent if isinstance(p, dict) and p.get("type") == "lesson"]
            self.assertEqual(lessons[1]["verdict"], "correct")

        _run(scenario())


if __name__ == "__main__":
    unittest.main()


class NativeLanguageSwitchTests(unittest.TestCase):
    """_note_native: the learner's own language changes only on repeated, confident evidence."""

    def _session(self):
        session, _llm, _tts, _sent = test_tutor.PracticeStateTests._session(test_tutor.PracticeStateTests(), [], [])
        session.target = "es"
        return session

    def test_one_confident_turn_is_not_enough(self):
        """Eval: Hinglish hi01 came back as Italian at 0.81 with 5 words. One such turn must not switch."""
        session = self._session()
        session._note_native({"language_code": "it", "language_confidence": 0.81}, "mujhe ek coffee chahiye please")
        self.assertEqual(session.native_language, "en")

    def test_two_consecutive_confident_turns_switch(self):
        session = self._session()
        for _ in range(2):
            session._note_native({"language_code": "fr", "language_confidence": 0.9}, "Je voudrais un café s'il vous plaît")
        self.assertEqual(session.native_language, "fr")

    def test_an_interrupting_turn_resets_the_count(self):
        session = self._session()
        note = session._note_native
        note({"language_code": "fr", "language_confidence": 0.9}, "Je voudrais un café s'il vous plaît")
        note({"language_code": "es", "language_confidence": 0.9}, "Hola")  # a practice attempt in between
        note({"language_code": "fr", "language_confidence": 0.9}, "Comment dit-on merci")
        self.assertEqual(session.native_language, "en")
        note({"language_code": "fr", "language_confidence": 0.9}, "Et comment dit-on bonjour")
        self.assertEqual(session.native_language, "fr")

    def test_a_different_language_restarts_the_count(self):
        session = self._session()
        session._note_native({"language_code": "fr", "language_confidence": 0.9}, "Je voudrais un café")
        session._note_native({"language_code": "it", "language_confidence": 0.9}, "Vorrei un caffè per favore")
        self.assertEqual(session.native_language, "en")

    def test_missing_or_low_confidence_short_target_and_unteachable_do_not_count(self):
        session = self._session()
        note = session._note_native
        for turn, text in (
            ({"language_code": "fr", "language_confidence": None}, "Je voudrais un café"),
            ({"language_code": "fr", "language_confidence": 0.59}, "Je voudrais un café"),
            ({"language_code": "fr", "language_confidence": 0.95}, "Merci beaucoup"),
            ({"language_code": "es", "language_confidence": 0.95}, "Quiero un café por favor"),
            ({"language_code": "ko", "language_confidence": 1.0}, "¿Dónde está el baño?"),
        ):
            note(turn, text)
            note(turn, text)
            self.assertEqual(session.native_language, "en", (turn, text))
