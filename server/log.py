"""Append one JSONL line per final turn. Never overwrites the file."""

import json
import os
import threading

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
    # Auto-target mode only: what the model said, the previous and the resolved target.
    "auto_target",
)


def write_turn(path, record):
    if not path:
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    line = {key: record[key] for key in TURN_KEYS if key in record}
    with _lock:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
