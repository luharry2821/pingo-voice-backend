"""Barge-in must work while the browser is still playing audio the server already sent.

TTS arrives faster than real time, so the server can finish sending a reply
(and go back to "listening") seconds before the learner stops hearing it.
server/session.py keeps `playback_until`, an estimate of when browser
playback ends; speech from the learner before then must still interrupt the
tutor (send `barge_in` so the browser flushes its audio queue).
"""

import asyncio
import unittest

from server.config import load_settings
from server.session import Session, now_ms


def _session(sent):
    """A Session with no STT/LLM/TTS, whose messages go into `sent`."""
    async def send(payload):
        """Fake browser socket: receives every message the session sends."""
        sent.append(payload)

    return Session(load_settings("configs/baseline.yaml"), "key", send)


def _barged(sent):
    """True if a barge_in message was sent to the browser."""
    return any(isinstance(item, dict) and item.get("type") == "barge_in" for item in sent)


class PlaybackBargeInTests(unittest.TestCase):
    """When learner speech does and does not interrupt the tutor."""
    def test_speech_during_playback_barges_in_after_server_sent_everything(self):
        """SpeechStarted while the browser is still playing (state already listening) barges in and clears playback_until.
        """
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
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
        """A partial transcript with words during playback also barges in (backup for SpeechStarted).
        """
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
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
        """Speech after playback has ended does not send barge_in."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
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
        """An empty partial during playback does not barge in."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
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
        """A repeat of the turn the tutor is answering is not an interruption."""
        async def scenario():
            """Async body of this test (run with asyncio.run)."""
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
