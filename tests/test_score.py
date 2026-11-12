"""Language-ID accuracy and confident-wrong rate in test_cases/score.py.

These KPIs score AssemblyAI's language_code against each case's expected
language, per final turn. The cutoffs (0.6 / 0.5) are the ones Pingo uses
before changing the learner's own language or treating a practice turn as
another language. Tests use small synthetic cases so they do not depend on
the live Render run, plus one regression check on the committed raw outputs.
"""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("score", ROOT / "test_cases" / "score.py")
score = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(score)


def _metrics(cases, raw, **kwargs):
    """Call language_id_metrics with optional cutoff overrides."""
    return score.language_id_metrics(cases, raw, **kwargs)


def _one(cid, langs, text="hola", code="es", conf=0.9):
    """A one-turn case and matching raw output, for compact test setup."""
    cases = {cid: {"id": cid, "languages": langs}}
    raw = {cid: {"id": cid, "turns": [{"text": text, "language_code": code, "language_confidence": conf}]}}
    return cases, raw


class LanguageIdMetricsTests(unittest.TestCase):
    """Scoring rules for language_id_metrics, including code-switching and cutoffs."""

    def test_correct_label_counts_as_accurate(self):
        """A turn whose language_code matches the expected language is correct."""
        m = _metrics(*_one("es02", ["es"], "Quiero aprender a pedir comida en un restaurante.", "es", 0.72))
        self.assertEqual(m["language_id_accuracy"]["n_correct"], 1)
        self.assertEqual(m["language_id_accuracy"]["n_turns"], 1)
        self.assertEqual(m["language_id_accuracy"]["value_pct"], 100.0)
        self.assertEqual(m["language_id_mislabeled"], [])
        self.assertEqual(m["confident_wrong_rate"]["n_confident_wrong"], 0)

    def test_wrong_label_is_listed(self):
        """A wrong label lowers accuracy and is listed with text, expected, got and confidence."""
        m = _metrics(*_one("es01", ["es"], "¿Dónde está el baño?", "ko", 1.0))
        self.assertEqual(m["language_id_accuracy"]["value_pct"], 0.0)
        self.assertEqual(m["language_id_mislabeled"], [
            {"id": "es01", "text": "¿Dónde está el baño?", "expected": ["es"], "got": "ko", "confidence": 1.0},
        ])

    def test_codeswitch_either_language_is_correct(self):
        """Code-switching cases list two languages; either code counts as correct, because a mixed utterance has no single right label."""
        cases = {"hi01": {"id": "hi01", "languages": ["en", "hi"]}}
        raw = {"hi01": {"turns": [{"text": "a", "language_code": "en", "language_confidence": 0.8}]}}
        self.assertEqual(_metrics(cases, raw)["language_id_accuracy"]["n_correct"], 1)
        raw["hi01"]["turns"][0]["language_code"] = "hi"
        self.assertEqual(_metrics(cases, raw)["language_id_accuracy"]["n_correct"], 1)
        # A third language is a miss, even on a mixed clip.
        raw["hi01"]["turns"][0]["language_code"] = "it"
        m = _metrics(cases, raw)
        self.assertEqual(m["language_id_accuracy"]["n_correct"], 0)
        self.assertEqual(m["language_id_mislabeled"][0]["got"], "it")
        self.assertIn("en+hi", m["language_id_accuracy_by_language"])

    def test_confident_wrong_uses_native_cutoff(self):
        """A wrong label at confidence 0.6 or above is confident-wrong; 0.59 is not, because 0.6 is the native-language switch cutoff."""
        cases = {"a": {"languages": ["es"]}, "b": {"languages": ["es"]}}
        raw = {
            "a": {"turns": [{"text": "x", "language_code": "ko", "language_confidence": 0.6}]},
            "b": {"turns": [{"text": "y", "language_code": "ko", "language_confidence": 0.59}]},
        }
        m = _metrics(cases, raw)
        self.assertEqual(m["confident_wrong_rate"]["n_confident_wrong"], 1)
        self.assertEqual(m["confident_wrong_rate"]["cutoff"], 0.6)
        self.assertEqual(m["confident_wrong_rate"]["value_pct"], 50.0)
        # Both scores are at or above the practice cutoff of 0.5.
        self.assertEqual(m["n_wrong_at_practice_cutoff"]["n_wrong"], 2)
        self.assertEqual(score.LANG_ID_NATIVE_CUTOFF, 0.6)

    def test_practice_cutoff_count_includes_the_gap_between_cutoffs(self):
        """A wrong label at 0.5 is counted at the practice cutoff but is not confident-wrong (0.6)."""
        m = _metrics(*_one("pm18", ["es"], "Ella son muy simpática.", "fr", 0.53))
        self.assertEqual(m["confident_wrong_rate"]["n_confident_wrong"], 0)
        self.assertEqual(m["n_wrong_at_practice_cutoff"]["n_wrong"], 1)
        self.assertEqual(m["n_wrong_at_practice_cutoff"]["cutoff"], 0.5)
        self.assertEqual(score.LANG_ID_PRACTICE_CUTOFF, 0.5)

    def test_each_turn_is_scored_separately(self):
        """Multi-turn cases (two-speaker or pause-filler splits) score each final turn on its own."""
        cases = {"h14": {"languages": ["en"]}}
        raw = {"h14": {"turns": [
            {"text": "I want to.", "language_code": "es", "language_confidence": 0.44},
            {"text": "Um.", "language_code": "es", "language_confidence": 0.35},
            {"text": "I mean, I need to practice the past tense.", "language_code": "en", "language_confidence": 0.73},
        ]}}
        m = _metrics(cases, raw)
        self.assertEqual(m["language_id_accuracy"]["n_turns"], 3)
        self.assertEqual(m["language_id_accuracy"]["n_correct"], 1)
        self.assertEqual(m["language_id_accuracy"]["n_cases"], 1)
        self.assertEqual(len(m["language_id_mislabeled"]), 2)
        # Both misses are below 0.6, so they do not raise the confident-wrong rate.
        self.assertEqual(m["confident_wrong_rate"]["n_confident_wrong"], 0)

    def test_missing_language_code_is_wrong_but_not_confident(self):
        """A turn with no language_code is wrong, but it does not inflate the confident-wrong rate."""
        cases = {"x": {"languages": ["en"]}}
        raw = {"x": {"turns": [{"text": "hello"}]}}
        m = _metrics(cases, raw)
        self.assertEqual(m["language_id_accuracy"]["n_correct"], 0)
        self.assertEqual(m["confident_wrong_rate"]["n_confident_wrong"], 0)
        self.assertEqual(m["n_wrong_at_practice_cutoff"]["n_wrong"], 0)
        self.assertIsNone(m["language_id_mislabeled"][0]["got"])

    def test_empty_turns_and_missing_raw_contribute_nothing(self):
        """Cases with no turns, or no raw row, add nothing: there is no label to score."""
        cases = {"a": {"languages": ["en"]}, "b": {"languages": ["es"]}}
        raw = {"a": {"turns": []}}
        m = _metrics(cases, raw)
        self.assertEqual(m["language_id_accuracy"]["n_turns"], 0)
        self.assertIsNone(m["language_id_accuracy"]["value_pct"])
        self.assertEqual(m["language_id_mislabeled"], [])

    def test_per_language_groups_codeswitch_separately(self):
        """Single-language turns are grouped by expected code; code-switching turns use an en+es (or en+hi) key."""
        cases = {"es01": {"languages": ["es"]}, "cs01": {"languages": ["en", "es"]}}
        raw = {
            "es01": {"turns": [{"text": "a", "language_code": "es", "language_confidence": 0.9}]},
            "cs01": {"turns": [{"text": "b", "language_code": "en", "language_confidence": 0.9}]},
        }
        m = _metrics(cases, raw)
        self.assertEqual(m["language_id_accuracy_by_language"]["es"]["n_turns"], 1)
        self.assertEqual(m["language_id_accuracy_by_language"]["en+es"]["n_turns"], 1)
        self.assertEqual(m["language_id_accuracy_by_language"]["en+es"]["n_correct"], 1)
        self.assertNotIn("en", m["language_id_accuracy_by_language"])

    def test_render_run_regression(self):
        """The committed 109-case Render run stays at the recorded language-ID numbers, so a definition change is visible."""
        with open(ROOT / "test_cases" / "cases.jsonl") as fh:
            cases = {json.loads(l)["id"]: json.loads(l) for l in fh}
        with open(ROOT / "test_cases" / "raw_outputs.jsonl") as fh:
            raw = {json.loads(l)["id"]: json.loads(l) for l in fh}
        m = _metrics(cases, raw)
        self.assertEqual(m["language_id_accuracy"]["n_correct"], 94)
        self.assertEqual(m["language_id_accuracy"]["n_turns"], 117)
        self.assertEqual(m["language_id_accuracy"]["value_pct"], 80.34)
        self.assertEqual(m["confident_wrong_rate"]["n_confident_wrong"], 16)
        self.assertEqual(m["confident_wrong_rate"]["value_pct"], 13.68)
        self.assertEqual(m["n_wrong_at_practice_cutoff"]["n_wrong"], 17)
        self.assertEqual(len(m["language_id_mislabeled"]), 23)
        # The known confidently-wrong case: Spanish tagged Korean at 1.0.
        es01 = [x for x in m["language_id_mislabeled"] if x["id"] == "es01"]
        self.assertEqual(len(es01), 1)
        self.assertEqual(es01[0]["got"], "ko")
        self.assertEqual(es01[0]["confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()
