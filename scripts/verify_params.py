"""Check whether universal-3-6-pro applies prompt, mode, and interruption_delay.

The streaming API reference still says those three are Universal-3.5 Pro only.
The turn-detection guide and the mid-stream configuration page describe them
for Universal-3.6 Pro, and unrecognized query parameters are ignored rather
than rejected. This script sends unusual values and prints the SessionBegins
echo plus any Error frame.

  export ASSEMBLYAI_API_KEY=...
  python scripts/verify_params.py

Exit 0 after a successful connection (read the ACCEPT / IGNORE / UNKNOWN lines).
Exit 2 when the key is missing. Exit 1 when the socket never opens.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.config import load_dotenv
from server.stt import API_VERSION, build_url, stt_query_params


async def _listen(ws, seconds):
    frames = []
    deadline = asyncio.get_event_loop().time() + seconds
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return frames
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return frames
        except Exception as exc:
            frames.append({"type": "Error", "error": str(exc)})
            return frames
        if isinstance(raw, bytes):
            continue
        frames.append(json.loads(raw))
        if frames[-1].get("type") == "Error":
            return frames


async def probe(api_key, params, update=None):
    import websockets

    async with websockets.connect(
        build_url(params),
        additional_headers={
            "Authorization": api_key,
            "AssemblyAI-Version": API_VERSION,
        },
        open_timeout=15,
    ) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        if update:
            await ws.send(json.dumps(update))
        rest = await _listen(ws, 1.5)
        try:
            await ws.send(json.dumps({"type": "Terminate"}))
        except Exception:
            pass
        return [first, *rest]


def _judge(name, sent, echoed):
    if echoed is None:
        return f"{name}: UNKNOWN (not in the SessionBegins echo; check Error frames)"
    if echoed == sent:
        return f"{name}: ACCEPT (echo {echoed!r} matches what we sent)"
    return f"{name}: IGNORE (sent {sent!r}, echo {echoed!r})"


async def main():
    load_dotenv()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if not api_key:
        print("ASSEMBLYAI_API_KEY is not set.", file=sys.stderr)
        return 2

    unusual = {
        "speech_model": "universal-3-6-pro",
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "language_detection": True,
        "mode": "min_latency",
        "interruption_delay": 123,
        "prompt": "A language learner practicing short phrases.",
        "language_codes": [],
        "keyterms_prompt": [],
        "agent_context_enabled": False,
    }
    params = stt_query_params(unusual)
    print("connecting with", json.dumps(params))
    try:
        frames = await probe(
            api_key,
            params,
            update={
                "type": "UpdateConfiguration",
                "mode": "balanced",
                "interruption_delay": 250,
                "prompt": "A language learner asking for a translation.",
            },
        )
    except Exception as exc:
        print(f"connection failed: {exc}", file=sys.stderr)
        return 1

    begin = next((frame for frame in frames if frame.get("type") == "Begin"), None)
    errors = [frame for frame in frames if frame.get("type") == "Error" or frame.get("error")]
    print("frames:")
    for frame in frames:
        print(json.dumps(frame, ensure_ascii=False))
    if begin is None:
        print("no SessionBegins message")
        return 1
    config = begin.get("configuration") or {}
    print(_judge("mode", "min_latency", config.get("mode")))
    print(_judge("interruption_delay", 123, config.get("interruption_delay")))
    print(_judge("prompt", unusual["prompt"], config.get("prompt")))
    if errors:
        print("error frames (a validated parameter often shows up here when the value is rejected):")
        for frame in errors:
            print(json.dumps(frame, ensure_ascii=False))
    else:
        print("no Error frame on the valid unusual values, nor on the mid-stream update")

    print("\nprobing an invalid mode (not-a-mode) and interruption_delay 99999")
    invalid = dict(unusual, mode="not-a-mode", interruption_delay=99999)
    try:
        bad_frames = await probe(api_key, stt_query_params(invalid))
    except Exception as exc:
        print(f"invalid-params connection failed: {exc}")
        bad_frames = [{"type": "Error", "error": str(exc)}]
    for frame in bad_frames:
        print(json.dumps(frame, ensure_ascii=False))
    if any(frame.get("type") == "Error" or frame.get("error") for frame in bad_frames):
        print("invalid values produced an error, so the server validates these fields")
    else:
        echoed = (bad_frames[0].get("configuration") or {}) if bad_frames else {}
        print(
            "invalid values did not error. "
            f"echoed mode={echoed.get('mode')!r}. "
            "If that is null or still the default, the server ignored them."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
