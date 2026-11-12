"""Re-check mode, interruption_delay, and prompt on universal-3-6-pro (a one-off live probe).

Why it exists: server/stt.py sends `mode`, `interruption_delay` and `prompt`
when opening streaming STT. This script opens one real streaming session with
unusual values and prints what the server echoes back in its Begin message,
so we can tell whether a parameter is accepted, ignored or rejected. It sends
no audio and is not part of the live pipeline or the unit tests.

Uses the official streaming SDK. A live run on Nov 10, 2026 (PT) already
found: mode is echoed, interruption_delay is validated but not echoed, and
prompt is neither rejected nor echoed.

  export ASSEMBLYAI_API_KEY=...
  python scripts/verify_params.py

Exit 0 after a connection. Exit 2 when the key is missing. Exit 1 when the
socket never opens.
"""

import json
import os
import sys
import time

# Make the repo root importable so `server.config` works when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.config import load_dotenv


def main():
    """Connect once with test values, try a live set_params, print the echoed config.

    Returns the process exit code (0 connected, 1 no Begin message, 2 no key).
    """
    load_dotenv()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if not api_key:
        print("ASSEMBLYAI_API_KEY is not set.", file=sys.stderr)
        return 2

    # Imported here so a missing key exits before the SDK is even loaded.
    from assemblyai.streaming.v3 import (
        RealTimeEvents,
        RealTimeParameters,
        RealTimeSessionParameters,
        RealTimeTranscriber,
        RealTimeTranscriberOptions,
    )

    # Filled in by the SDK callbacks, which run on the SDK's own threads.
    box = {"begin": None, "errors": []}

    def on_begin(_client, event):
        """SDK thread: keep the Begin event (it carries the echoed configuration)."""
        box["begin"] = event

    def on_error(_client, error):
        """SDK thread: collect error text to print at the end."""
        box["errors"].append(str(error))

    client = RealTimeTranscriber(
        RealTimeTranscriberOptions(
            api_key=api_key,
            api_host="streaming.assemblyai.com",
            connect_timeout=15,
        )
    )
    client.on(RealTimeEvents.Begin, on_begin)
    client.on(RealTimeEvents.Error, on_error)
    # Deliberately unusual values (min_latency, 123 ms) so an echo proves they were
    # applied.
    client.connect(
        RealTimeParameters(
            sample_rate=16000,
            speech_model="universal-3-6-pro",
            language_detection=True,
            mode="min_latency",
            interruption_delay=123,
            prompt="A language learner practicing short phrases.",
        )
    )
    # Wait up to 8 s for Begin (or an error).
    deadline = time.time() + 8
    while box["begin"] is None and not box["errors"] and time.time() < deadline:
        time.sleep(0.05)
    # Also try a mid-session update, which is what update_config does in the server.
    try:
        client.set_params(
            RealTimeSessionParameters(
                prompt="A language learner asking for a translation.",
                interruption_delay=250,
            )
        )
    except Exception as exc:
        box["errors"].append(str(exc))
    time.sleep(1.0)
    client.disconnect(terminate=True)

    if box["begin"] is None:
        print("no SessionBegins message")
        for item in box["errors"]:
            print(item)
        return 1

    config = box["begin"].configuration
    dumped = config.model_dump() if config is not None and hasattr(config, "model_dump") else {}
    print(json.dumps(dumped, ensure_ascii=False))
    # Compare the echoed mode with what we sent.
    echoed_mode = dumped.get("mode")
    if echoed_mode == "min_latency":
        print("mode: ACCEPT (echo min_latency matches what we sent)")
    elif echoed_mode:
        print(f"mode: IGNORE (sent 'min_latency', echo {echoed_mode!r})")
    else:
        print("mode: UNKNOWN (not in the echo)")
    print("interruption_delay: not in the SDK Begin model. A value outside 0-1000 previously returned 3006.")
    print("prompt: not in the SDK Begin model. Previously it was not rejected and not echoed.")
    if box["errors"]:
        print("errors:")
        for item in box["errors"]:
            print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
