"""Barge-in must work while the browser is still playing audio the server already sent."""

import asyncio
import unittest

from server.config import load_settings
from server.session import Session, now_ms


def _session(sent):
    async def send(payload):
        sent.append(payload)

    return Session(load_settings("configs/baseline.yaml"), "key", send)


def _barged(sent):
    return any(isinstance(item, dict) and item.get("type") == "barge_in" for item in sent)


class PlaybackBargeInTests(unittest.TestCase):
    def test_speech_during_playback_barges_in_after_server_sent_everything(self):
        async def scenario():
            sent = []
            session = _session(sent)
            # The server already sent the whole reply and switched to listening,
            # but the browser has seconds of audio left.
            session.state = "listening"
            session.playback_until = now_ms() + 3000
            await session._on_stt({"type": "SpeechStarted", "timestamp": 10})
            self.assertTrue(_barged(sent))
            self.assertEqual(session.playback_until, 0)

        asyncio.run(scenario())

    def test_partial_words_during_playback_barge_in(self):
        async def scenario():
            sent = []
            session = _session(sent)
            session.state = "listening"
            session.playback_until = now_ms() + 3000
            await session._on_stt(
                {"type": "Turn", "turn_order": 0, "end_of_turn": False, "transcript": "wrong", "words": []}
            )
            self.assertTrue(_barged(sent))

        asyncio.run(scenario())

    def test_no_barge_in_once_playback_is_over(self):
        async def scenario():
            sent = []
            session = _session(sent)
            session.state = "listening"
            session.playback_until = now_ms() - 10
            await session._on_stt({"type": "SpeechStarted", "timestamp": 10})
            await session._on_stt(
                {"type": "Turn", "turn_order": 0, "end_of_turn": False, "transcript": "wrong", "words": []}
            )
            self.assertFalse(_barged(sent))

        asyncio.run(scenario())

    def test_empty_partial_does_not_barge_in(self):
        async def scenario():
            sent = []
            session = _session(sent)
            session.state = "listening"
            session.playback_until = now_ms() + 3000
            await session._on_stt(
                {"type": "Turn", "turn_order": 0, "end_of_turn": False, "transcript": "", "words": []}
            )
            self.assertFalse(_barged(sent))

        asyncio.run(scenario())

    def test_repeat_of_the_turn_being_answered_does_not_barge_in(self):
        async def scenario():
            sent = []
            session = _session(sent)
            session.state = "thinking"
            session.answering_order = 3
            await session._on_stt(
                {"type": "Turn", "turn_order": 3, "end_of_turn": False, "transcript": "wrong", "words": []}
            )
            self.assertFalse(_barged(sent))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
