"""AssemblyAI Voice Focus: on by default (far-field), configurable, validated, logged."""

import os
import tempfile
import unittest

from server.config import (
    apply_env,
    default_settings,
    load_settings,
    log_config,
    merge_session_config,
    normalize_voice_focus,
    normalize_voice_focus_threshold,
)
from server.session import Session
from server.stt import build_parameters, stt_query_params
from tests import test_tutor
from tests.test_tutor import _run


class VoiceFocusConfigTests(unittest.TestCase):
    def test_default_is_far_field_without_threshold(self):
        for settings in (load_settings(None), load_settings("configs/baseline.yaml"),
                         load_settings("configs/keyterms_on.yaml")):
            self.assertEqual(settings["stt"]["voice_focus"], "far-field")
            self.assertIsNone(settings["stt"]["voice_focus_threshold"])

    def test_env_vars(self):
        settings = default_settings()
        apply_env(settings, {"PINGO_STT_VOICE_FOCUS": "near-field", "PINGO_STT_VOICE_FOCUS_THRESHOLD": "0.7"})
        self.assertEqual(settings["stt"]["voice_focus"], "near-field")
        self.assertEqual(settings["stt"]["voice_focus_threshold"], 0.7)
        old = dict(os.environ)
        try:
            os.environ["PINGO_STT_VOICE_FOCUS"] = "off"
            os.environ.pop("PINGO_STT_VOICE_FOCUS_THRESHOLD", None)
            self.assertEqual(load_settings(None)["stt"]["voice_focus"], "")
            os.environ["PINGO_STT_VOICE_FOCUS"] = "loud-room"
            with self.assertRaises(ValueError):
                load_settings(None)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_normalize(self):
        for value, expected in (("far-field", "far-field"), ("Near_Field", "near-field"), (" FAR-FIELD ", "far-field"),
                                ("off", ""), ("", ""), (None, ""), (False, ""), ("none", "")):
            with self.subTest(value=value):
                self.assertEqual(normalize_voice_focus(value), expected)
        for bad in ("on", "nearfield", "loud", 1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_voice_focus(bad)
        for value, expected in ((None, None), ("", None), ("null", None), (0, 0.0), (1, 1.0), ("0.35", 0.35), (0.5, 0.5)):
            with self.subTest(value=value):
                self.assertEqual(normalize_voice_focus_threshold(value), expected)
        for bad in (-0.1, 1.01, "high", True, [0.5]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_voice_focus_threshold(bad)

    def test_session_config_override(self):
        base = load_settings("configs/baseline.yaml")
        self.assertEqual(merge_session_config(base, {"voice_focus": "off"})["stt"]["voice_focus"], "")
        merged = merge_session_config(base, {"stt": {"voice_focus": "near-field", "voice_focus_threshold": 0.8}})
        self.assertEqual(merged["stt"]["voice_focus"], "near-field")
        self.assertEqual(merged["stt"]["voice_focus_threshold"], 0.8)
        # The client's empty start config keeps the server default on.
        self.assertEqual(merge_session_config(base, {"prompt": ""})["stt"]["voice_focus"], "far-field")
        with self.assertRaises(ValueError):
            merge_session_config(base, {"voice_focus_threshold": 2})

    def test_log_config(self):
        settings = load_settings("configs/baseline.yaml")
        logged = log_config(settings)
        self.assertEqual(logged["voice_focus"], "far-field")
        self.assertIsNone(logged["voice_focus_threshold"])
        logged = log_config(merge_session_config(settings, {"voice_focus": "off"}))
        self.assertEqual(logged["voice_focus"], "off")


class VoiceFocusParamsTests(unittest.TestCase):
    def _params(self, **stt):
        settings = load_settings("configs/baseline.yaml")
        return stt_query_params(merge_session_config(settings, {"stt": stt} if stt else {})["stt"])

    def test_on_by_default_threshold_omitted(self):
        params = self._params()
        self.assertEqual(params["voice_focus"], "far-field")
        self.assertNotIn("voice_focus_threshold", params)
        sdk = build_parameters(params)
        self.assertEqual(str(getattr(sdk.voice_focus, "value", sdk.voice_focus)), "far-field")

    def test_off_omits_both(self):
        for off in ("off", "", None):
            with self.subTest(off=off):
                params = self._params(voice_focus=off, voice_focus_threshold=0.7)
                self.assertNotIn("voice_focus", params)
                self.assertNotIn("voice_focus_threshold", params)

    def test_threshold_sent_when_set(self):
        params = self._params(voice_focus="near-field", voice_focus_threshold=0.7)
        self.assertEqual(params["voice_focus"], "near-field")
        self.assertEqual(params["voice_focus_threshold"], 0.7)
        self.assertEqual(build_parameters(params).voice_focus_threshold, 0.7)


class ProfanityFilterTests(unittest.TestCase):
    def test_on_by_default_and_sent(self):
        for path in (None, "configs/baseline.yaml", "configs/keyterms_on.yaml"):
            settings = load_settings(path)
            self.assertIs(settings["stt"]["filter_profanity"], True)
            params = stt_query_params(settings["stt"])
            self.assertIs(params["filter_profanity"], True)
            self.assertIs(build_parameters(params).filter_profanity, True)
            self.assertIs(log_config(settings)["filter_profanity"], True)

    def test_off_is_omitted(self):
        settings = merge_session_config(load_settings("configs/baseline.yaml"), {"stt": {"filter_profanity": False}})
        self.assertIs(settings["stt"]["filter_profanity"], False)
        self.assertNotIn("filter_profanity", stt_query_params(settings["stt"]))
        self.assertIs(log_config(settings)["filter_profanity"], False)

    def test_env_override_is_coerced_to_bool(self):
        old = dict(os.environ)
        try:
            for raw, expected in (("false", False), ("0", False), ("off", False), ("no", False),
                                  ("true", True), ("1", True), ("yes", True)):
                with self.subTest(raw=raw):
                    os.environ["PINGO_STT_FILTER_PROFANITY"] = raw
                    self.assertIs(load_settings(None)["stt"]["filter_profanity"], expected)
        finally:
            os.environ.clear()
            os.environ.update(old)


class VoiceFocusSessionTests(unittest.TestCase):
    _session = test_tutor.PracticeStateTests._session

    def test_start_sends_it_and_rejects_bad_values(self):
        async def scenario():
            session, _llm, _tts, sent = self._session([], [])
            await session.handle_text({"type": "start", "target_language": "es"})
            self.assertEqual(session.stt_query["voice_focus"], "far-field")

            session, _llm, _tts, sent = self._session([], [])
            await session.handle_text({"type": "start", "target_language": "es",
                                       "config": {"voice_focus": "off"}})
            self.assertNotIn("voice_focus", session.stt_query)

            session, _llm, _tts, sent = self._session([], [])
            await session.handle_text({"type": "start", "target_language": "es",
                                       "config": {"voice_focus": "studio"}})
            errors = [p for p in sent if isinstance(p, dict) and p.get("type") == "error"]
            self.assertEqual(errors[-1]["message"], "voice_focus must be near-field, far-field or off")
            self.assertIsNone(session.stt)

        _run(scenario())

    def test_bad_update_keeps_settings(self):
        async def scenario():
            session, _llm, _tts, sent = self._session([], [])
            await session.handle_text({"type": "start", "target_language": "es"})
            await session.handle_text({"type": "update_config", "config": {"voice_focus_threshold": 5}})
            errors = [p for p in sent if isinstance(p, dict) and p.get("type") == "error"]
            self.assertIn("voice_focus_threshold", errors[-1]["message"])
            self.assertIsNone(session.settings["stt"]["voice_focus_threshold"])
            # A valid change is stored for the next start (it is a connection parameter).
            await session.handle_text({"type": "update_config", "config": {"voice_focus": "near-field"}})
            self.assertEqual(session.stt_query["voice_focus"], "near-field")

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
