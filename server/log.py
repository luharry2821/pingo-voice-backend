"""The eval log: append one JSONL line per final learner turn. Never overwrites the file.

Pipeline position: the last step of every turn. After STT -> LLM tutor -> TTS,
server/session.py hands a finished record to write_turn(). Each line holds the
transcript, words and language STT returned, what the tutor said, timings and
usage (audio seconds, tokens, TTS characters) and the config used. The eval
scorer (archive/test_cases/score.py) reads these lines to compute WER and the other
KPIs. No API key is ever written.
"""

import json
import os
import threading

# One lock for the whole process: several WebSocket sessions may finish turns at the
# same time.
_lock = threading.Lock()

# Eval contract (uploads schema). Extra keys are allowed; these are the ones
# score.py reads, plus the partials and the STT session id we keep for debugging.
TURN_KEYS = (
    "case_id",
    "session_id",
    "turn_order",
    "config",
    "stt",
    "stt_partial_first",
    "stt_partials",
    "stt_session_id",
    "end_of_turn_reason",
    "llm",
    "barge_in",
    "timings_ms",
    "usage",
    "tts_audio_ms",
)


def write_turn(path, record):
    """Append one turn record to the JSONL file at `path`.

    Only the keys in TURN_KEYS are written (internal bookkeeping keys are
    dropped). The directory is created if needed. A lock keeps lines from two
    sessions in the same process from interleaving. path "" or None = no logging.
    """
    if not path:
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    line = {key: record[key] for key in TURN_KEYS if key in record}
    # ensure_ascii=False keeps accents and non-Latin scripts readable in the file.
    with _lock:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
