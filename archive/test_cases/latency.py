"""Pipeline latency: end of speech -> first reply audio through Pingo's /ws, client side.

This is a whole-pipeline number (STT + tutor LLM + TTS + network), not one of
AssemblyAI's STT metrics; archive/test_cases/stt_bench.py measures those (TTFT, TTCT,
end-of-turn finalization latency) on the STT stream alone.

Usage (repo root): python archive/test_cases/latency.py [wss://pingo-voice-backend.onrender.com/ws] [out.jsonl]

Acts like the browser: one WebSocket session, a mic stream that never stops
(clip audio, then silence), and the same turn settings the Lovable app sends
(min_turn_silence 600 ms, max_turn_silence 2400 ms). For each clip it waits for
the whole spoken reply before playing the next one, so replies never overlap.

Per clip it records, in ms after the end of speech (the last 50 ms frame whose
peak is above -40 dBFS, so the resolution is one frame):
  final       the end_of_turn transcript arrived (turn silence + STT)
  lesson      the lesson JSON arrived (+ LLM)
  first_audio the first binary TTS frame arrived (+ TTS)  <- the headline number
Server timings are not used, so nothing depends on STT word timestamps.

Environment knobs: ONLY (comma-separated case id prefixes), LIMIT (max clips),
MIN_SIL / MAX_SIL (turn settings), PER (clips per session, default 20). Each
session warms 2 TTS voices; sessions are spaced 30 s apart to stay well under
the account's 5 TTS sessions a minute.
"""
import asyncio, json, os, statistics, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import websockets
from server.audio import read_pcm16_mono

D = os.path.dirname(os.path.abspath(__file__))
URL = sys.argv[1] if len(sys.argv) > 1 else "wss://pingo-voice-backend.onrender.com/ws"
OUT = sys.argv[2] if len(sys.argv) > 2 else f"{D}/latency_raw.jsonl"
FRAME = 1600; SIL = b"\x00" * FRAME
PER = int(os.environ.get("PER", 20))
CONFIG = {"min_turn_silence": int(os.environ.get("MIN_SIL", 600)),
          "max_turn_silence": int(os.environ.get("MAX_SIL", 2400))}
# Single-speaker clips with no planned pauses: one clip should be one turn.
SKIP = ("pause_filler",)


def last_voiced_frame(pcm, threshold=328):  # 328/32768 = -40 dBFS
    """Index of the last 50 ms frame whose peak sample is above the threshold."""
    last = 0
    for k in range(0, len(pcm) // FRAME):
        chunk = memoryview(pcm)[k * FRAME:(k + 1) * FRAME].cast("h")
        if max(abs(min(chunk)), abs(max(chunk))) > threshold:
            last = k
    return last


async def session(cases):
    rows = []
    async with websockets.connect(URL, max_size=None, open_timeout=120) as ws:
        await ws.send(json.dumps({"type": "start", "target_language": "es", "config": CONFIG}))
        state = {"mark": None, "row": None, "last_audio": 0.0, "ended": 0}
        stop = asyncio.Event()

        async def mic():
            """Real-time mic: one frame every 50 ms, silence when no clip is queued."""
            nxt = time.monotonic()
            while not stop.is_set():
                frame = state["queue"].pop(0) if state.get("queue") else SIL
                await ws.send(frame)
                if state.get("mark_at") is not None and state["sent"] == state["mark_at"]:
                    state["mark"] = time.monotonic()  # this frame held the last speech
                state["sent"] = state.get("sent", 0) + 1
                nxt += 0.05
                await asyncio.sleep(max(0, nxt - time.monotonic()))

        async def rx():
            async for m in ws:
                row, now = state["row"], time.monotonic()
                if row is None or state["mark"] is None:
                    continue
                ms = round((now - state["mark"]) * 1000)
                if isinstance(m, bytes):
                    row.setdefault("first_audio", ms)
                    state["last_audio"] = now
                    continue
                d = json.loads(m)
                t = d.get("type")
                if t == "transcript" and d.get("end_of_turn") and d.get("transcript", "").strip():
                    row.setdefault("final", ms); row.setdefault("text", d["transcript"])
                    row["turns"] = row.get("turns", 0) + 1
                elif t == "lesson":
                    row.setdefault("lesson", ms)
                elif t == "tts_end":
                    state["ended"] += 1
                elif t == "error":
                    row.setdefault("errors", []).append(d.get("message"))

        state["sent"] = 0
        tasks = [asyncio.create_task(mic()), asyncio.create_task(rx())]
        await asyncio.sleep(2.0)  # STT connect + voice warm-up, as after pressing the mic
        for c in cases:
            pcm = read_pcm16_mono(f"{D}/{c['audio']}"); pcm += SIL[: (-len(pcm)) % FRAME]
            frames = [pcm[i:i + FRAME] for i in range(0, len(pcm), FRAME)]
            row = {"id": c["id"], "category": c["category"]}
            state.update(row=row, mark=None, ended=0, last_audio=0.0)
            state["mark_at"] = state["sent"] + len(state.get("queue") or []) + last_voiced_frame(pcm)
            state["queue"] = frames
            # Wait for the reply to finish: audio seen, then 1.5 s with no new audio.
            t0 = time.monotonic()
            while time.monotonic() - t0 < 30:
                await asyncio.sleep(0.1)
                if state["last_audio"] and state["ended"] and time.monotonic() - state["last_audio"] > 1.5:
                    break
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            await asyncio.sleep(1.0)
        stop.set(); await ws.send(json.dumps({"type": "stop"})); await asyncio.sleep(0.5)
        for t in tasks:
            t.cancel()
    return rows


def pct(values, p):
    values = sorted(values)
    k = (len(values) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo))


async def main():
    cases = [json.loads(l) for l in open(f"{D}/cases.jsonl")]
    cases = [c for c in cases if c["category"] not in SKIP]
    if os.environ.get("ONLY"):
        cases = [c for c in cases if c["id"].startswith(tuple(os.environ["ONLY"].split(",")))]
    if os.environ.get("LIMIT"):
        cases = cases[: int(os.environ["LIMIT"])]
    rows = []
    for k in range(0, len(cases), PER):
        if k:
            await asyncio.sleep(30)
        rows += await session(cases[k:k + PER])
    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ok = [r for r in rows if r.get("turns") == 1 and "lesson" in r and "first_audio" in r]
    stages = {"end of speech -> final transcript (as the browser sees it)": [r["final"] for r in ok],
              "final transcript -> lesson (tutor LLM)": [r["lesson"] - r["final"] for r in ok],
              "lesson -> first audio (TTS)": [r["first_audio"] - r["lesson"] for r in ok],
              "total: end of speech -> first reply audio": [r["first_audio"] for r in ok]}
    for name, v in stages.items():
        if v:
            print(f"{name}: n={len(v)} p50={pct(v, .5)} p90={pct(v, .9)} p95={pct(v, .95)} mean={round(statistics.mean(v))}")
    print("clips", len(rows), "single-turn with audio", sum(1 for r in rows if r.get("turns") == 1 and "first_audio" in r))

asyncio.run(main())
