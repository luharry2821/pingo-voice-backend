"""Stream every benchmark case through the live Pingo backend (/ws) and save what STT returned.

Usage (repo root): .venv/bin/python test_cases/run_render.py [wss://pingo-voice-backend.onrender.com/ws] [out.jsonl]

This acts like the browser: it opens the WebSocket, sends `start`, then plays
each case's WAV as 50 ms PCM16 frames in real time, exactly as a microphone
would (mic audio -> STT -> LLM tutor -> TTS). Only the final STT transcripts
are kept; test_cases/score.py compares them with the ground truth.

Clips go 15 per session with 4 s of silence after each; final (end_of_turn) transcripts
are attributed to the clip being played or its trailing silence. Sessions run 2 at a time.

Environment knobs: ONLY (comma-separated case id prefixes), PER (clips per
session, default 15), CONC (parallel sessions, default 2).
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import websockets
from server.audio import read_pcm16_mono
# D: this folder. URL/OUT: optional command-line arguments. CASES: one JSON object per
# line of cases.jsonl (id, audio path, ground truth...).
D = os.path.dirname(os.path.abspath(__file__))
URL = sys.argv[1] if len(sys.argv) > 1 else "wss://pingo-voice-backend.onrender.com/ws"
OUT = sys.argv[2] if len(sys.argv) > 2 else f"{D}/raw_outputs.jsonl"
CASES = [json.loads(l) for l in open(f"{D}/cases.jsonl")]
ONLY = os.environ.get("ONLY")  # optional comma-separated id prefixes
if ONLY: CASES = [c for c in CASES if c["id"].startswith(tuple(ONLY.split(",")))]
# FRAME = 1600 bytes = 50 ms of 16 kHz PCM16. SIL is one frame of silence. PER = clips
# per WebSocket session.
FRAME = 1600; SIL = b"\x00" * FRAME; PER = int(os.environ.get("PER", 15))

async def session(cases):
    """Play `cases` on one WebSocket session; return one result dict per case.

    Each result has the case id, the final turns STT produced while that clip
    (or its trailing silence) was playing, and any error messages.
    """
    res = {c["id"]: {"id": c["id"], "turns": []} for c in cases}
    # Which case is playing right now, so incoming transcripts are filed under it.
    state = {"cur": None}
    async with websockets.connect(URL, max_size=None, open_timeout=120) as ws:
        # Same start message the browser sends; empty config = server defaults.
        await ws.send(json.dumps({"type": "start", "target_language": "es", "config": {}}))
        async def rx():
            """Read server messages; file each final, non-empty transcript under the clip now playing.

            Binary frames (the tutor's TTS audio) are ignored.
            """
            async for m in ws:
                if isinstance(m, bytes): continue
                d = json.loads(m)
                if d.get("type") == "transcript" and d.get("end_of_turn") and d.get("transcript", "").strip() and state["cur"]:
                    res[state["cur"]]["turns"].append({"text": d["transcript"], "language_code": d.get("language_code"), "language_confidence": d.get("language_confidence")})
                elif d.get("type") == "error" and state["cur"]:
                    res[state["cur"]].setdefault("errors", []).append(d.get("message"))
        # Give STT a moment to connect before audio starts.
        task = asyncio.create_task(rx()); await asyncio.sleep(1.5)
        for c in cases:
            state["cur"] = c["id"]
            # Pad the clip to a whole number of frames.
            pcm = read_pcm16_mono(f"{D}/{c['audio']}"); pcm += SIL[: (-len(pcm)) % FRAME]
            # Real-time pace: one 50 ms frame every 50 ms.
            for i in range(0, len(pcm), FRAME):
                await ws.send(pcm[i:i+FRAME]); await asyncio.sleep(0.05)
            # 80 frames x 50 ms = 4 s of silence, so STT ends the turn before the next
            # clip.
            for _ in range(80): await ws.send(SIL); await asyncio.sleep(0.05)
        await ws.send(json.dumps({"type": "stop"})); await asyncio.sleep(0.5); task.cancel()
    return list(res.values())

async def main():
    """Split the cases into sessions, run them with limited parallelism, write OUT.

    Each output line gets a "hypothesis": all final turns of that case joined.
    """
    chunks = [CASES[i:i+PER] for i in range(0, len(CASES), PER)]
    sem = asyncio.Semaphore(int(os.environ.get("CONC", 2)))  # more than 2 parallel sessions came back empty
    async def run(ch, k):
        """Run one chunk of cases, staggering session starts by 5 s each."""
        await asyncio.sleep(k * 5)
        async with sem: return await session(ch)
    allres = await asyncio.gather(*[run(ch, k) for k, ch in enumerate(chunks)])
    with open(OUT, "w") as f:
        for rows in allres:
            for r in rows:
                r["hypothesis"] = " ".join(t["text"] for t in r["turns"])
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("done", sum(map(len, allres)))
asyncio.run(main())
