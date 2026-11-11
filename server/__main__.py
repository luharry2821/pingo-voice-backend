"""WebSocket server, or one WAV through the same pipeline.

  python -m server
  python -m server --input clip.wav --case-id pm-es-01 --session-id baseline-pm-es-01-1 --log logs/turns.jsonl
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time

import uvicorn

from server.audio import iter_chunks, read_pcm16_mono, silence
from server.config import bind_address, load_dotenv, load_settings, normalize_target
from server.llm import GatewayLLM
from server.app import create_app
from server.session import Session
from server.tts import GatewayTTS


def main(argv=None):
    load_dotenv()
    parser = argparse.ArgumentParser(description="Pingo voice tutor backend")
    parser.add_argument("--config", default=os.environ.get("PINGO_CONFIG", "configs/baseline.yaml"))
    parser.add_argument("--input", help="WAV file. Streamed at real-time pace through the live pipeline.")
    parser.add_argument("--case-id", default="", help="Copied onto every JSONL line. Empty for a live session.")
    parser.add_argument("--session-id", default="", help="Unique per run so repeated cases are not merged.")
    parser.add_argument("--log", default=None, help="JSONL path. Appended to, never overwritten.")
    parser.add_argument("--target-language", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-pace", action="store_true", help="File mode only: send audio as fast as possible.")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings(args.config, args.set)
    if args.log:
        settings["log"]["path"] = args.log
    if args.target_language:
        settings["target_language"] = normalize_target(args.target_language)
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if args.input:
        code = asyncio.run(
            run_file(
                args.input,
                settings,
                api_key,
                case_id=args.case_id,
                session_id=args.session_id,
                pace=not args.no_pace,
            )
        )
        raise SystemExit(code)
    host, port = bind_address(settings, host=args.host, port=args.port)
    uvicorn.run(create_app(settings, api_key), host=host, port=port)


async def run_file(
    path,
    settings,
    api_key,
    *,
    case_id="",
    session_id="",
    pace=True,
    stt_factory=None,
    llm=None,
    tts=None,
    chunk_ms=50,
    quiet_s=1.5,
    timeout_s=90,
):
    """Stream a WAV at real-time pace, then wait until the tutor reply is logged."""
    if not api_key and stt_factory is None:
        print("ASSEMBLYAI_API_KEY is not set.", file=sys.stderr)
        return 2
    pcm = read_pcm16_mono(path)
    audio = bytearray()
    saw_lesson = False

    async def send(payload):
        nonlocal saw_lesson
        if isinstance(payload, (bytes, bytearray)):
            audio.extend(payload)
            return
        if payload.get("type") == "lesson":
            saw_lesson = True
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        elif payload.get("type") == "error":
            print(json.dumps(payload, ensure_ascii=False), flush=True, file=sys.stderr)

    session = Session(
        settings,
        api_key,
        send,
        case_id=case_id,
        session_id=session_id,
        stt_factory=stt_factory,
        llm=llm or GatewayLLM(api_key, settings["llm"]),
        tts=tts or GatewayTTS(api_key, settings["tts"]),
        log_path=settings["log"]["path"],
    )
    await session.handle_text(
        {
            "type": "start",
            "target_language": settings.get("target_language") or "es",
            "case_id": case_id,
            "session_id": session.session_id,
        }
    )
    if session.stt is None:
        await session.close()
        return 1

    await _stream_pcm(session, pcm, chunk_ms, pace)
    # Silence after the clip lets endpointing emit the final turn. Cover the
    # configured max_turn_silence when a run raised it past the balanced default.
    tail_ms = 2000
    if settings["stt"].get("max_turn_silence"):
        tail_ms = max(tail_ms, int(settings["stt"]["max_turn_silence"]) + 700)
    await _stream_pcm(session, silence(tail_ms), chunk_ms, pace)

    deadline = time.monotonic() + timeout_s
    quiet_since = None
    while time.monotonic() < deadline:
        busy = session.job is not None and not session.job.done()
        if session.ready and not busy:
            if quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= quiet_s:
                break
        else:
            quiet_since = None
        await asyncio.sleep(0.05)
    else:
        if not session.ready and not saw_lesson:
            print("Timed out waiting for a final turn and a tutor reply.", file=sys.stderr)

    await session.shutdown()
    if audio:
        directory = os.path.dirname(settings["log"]["path"]) or "."
        os.makedirs(directory, exist_ok=True)
        out_path = os.path.join(directory, "last_reply.pcm")
        with open(out_path, "wb") as handle:
            handle.write(audio)
        print(f"wrote {out_path} ({len(audio)} bytes, 24 kHz pcm16)", flush=True)
    return 0 if session.turns_logged else 1


async def _stream_pcm(session, pcm, chunk_ms, pace):
    started = time.monotonic()
    sent_ms = 0
    for chunk in iter_chunks(pcm, chunk_ms=chunk_ms):
        await session.handle_audio(chunk)
        sent_ms += chunk_ms
        if pace:
            delay = started + (sent_ms / 1000.0) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)


if __name__ == "__main__":
    main()
