"""Stream every case through the live Pingo backend (/ws) and save what STT returned.
Usage (repo root): .venv/bin/python test_cases/run_render.py [wss://pingo-voice-backend.onrender.com/ws] [out.jsonl]
Clips go 15 per session with 4 s of silence after each; final (end_of_turn) transcripts
are attributed to the clip being played or its trailing silence. Sessions run 2 at a time."""
import asyncio, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import websockets
from server.audio import read_pcm16_mono
D = os.path.dirname(os.path.abspath(__file__))
URL = sys.argv[1] if len(sys.argv) > 1 else "wss://pingo-voice-backend.onrender.com/ws"
OUT = sys.argv[2] if len(sys.argv) > 2 else f"{D}/raw_outputs.jsonl"
CASES = [json.loads(l) for l in open(f"{D}/cases.jsonl")]
ONLY = os.environ.get("ONLY")  # optional comma-separated id prefixes
if ONLY: CASES = [c for c in CASES if c["id"].startswith(tuple(ONLY.split(",")))]
FRAME = 1600; SIL = b"\x00" * FRAME; PER = int(os.environ.get("PER", 15))

async def session(cases):
    res = {c["id"]: {"id": c["id"], "turns": []} for c in cases}
    state = {"cur": None}
    async with websockets.connect(URL, max_size=None, open_timeout=120) as ws:
        await ws.send(json.dumps({"type": "start", "target_language": "es", "config": {}}))
        async def rx():
            async for m in ws:
                if isinstance(m, bytes): continue
                d = json.loads(m)
                if d.get("type") == "transcript" and d.get("end_of_turn") and d.get("transcript", "").strip() and state["cur"]:
                    res[state["cur"]]["turns"].append({"text": d["transcript"], "language_code": d.get("language_code"), "language_confidence": d.get("language_confidence")})
                elif d.get("type") == "error" and state["cur"]:
                    res[state["cur"]].setdefault("errors", []).append(d.get("message"))
        task = asyncio.create_task(rx()); await asyncio.sleep(1.5)
        for c in cases:
            state["cur"] = c["id"]
            pcm = read_pcm16_mono(f"{D}/{c['audio']}"); pcm += SIL[: (-len(pcm)) % FRAME]
            for i in range(0, len(pcm), FRAME):
                await ws.send(pcm[i:i+FRAME]); await asyncio.sleep(0.05)
            for _ in range(80): await ws.send(SIL); await asyncio.sleep(0.05)
        await ws.send(json.dumps({"type": "stop"})); await asyncio.sleep(0.5); task.cancel()
    return list(res.values())

async def main():
    chunks = [CASES[i:i+PER] for i in range(0, len(CASES), PER)]
    sem = asyncio.Semaphore(int(os.environ.get("CONC", 2)))  # more than 2 parallel sessions came back empty
    async def run(ch, k):
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
