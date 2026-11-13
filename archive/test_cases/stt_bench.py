"""Head-to-head streaming STT: AssemblyAI vs Deepgram on the same clips, same machine, real time.

Usage (repo root): python archive/test_cases/stt_bench.py ARM [out.jsonl]
ARM is one of ARMS below (default output archive/test_cases/bench/<ARM>_raw.jsonl).
Keys come from the environment (ASSEMBLYAI_API_KEY, DEEPGRAM_API_KEY); nothing is logged.
Every row carries the provider's own id for that stream (AssemblyAI Begin `id`,
Deepgram `request_id`), so each call can be traced on the provider's side.

Each clip gets a fresh stream. Audio is the clip's 16 kHz PCM16, sent in 50 ms
frames, each frame sent when its 50 ms have elapsed (like a mic), then 4 s of
silence. Output lines are what score.py reads (id, turns, hypothesis), so
score.py scores both providers with the same normalization, plus latency fields.

Arms:
  assemblyai        universal-3-6-pro through the server's own StreamingSTT (official SDK),
                    Pingo's baseline config (configs/baseline.yaml): language_detection,
                    the learner-context prompt, min_turn_silence 600, max_turn_silence 2400.
                    AssemblyAI turn = a Turn event with end_of_turn true (transcript +
                    language_code). Streams are opened at most 5 a minute (the free plan's
                    new-sessions-per-minute limit, docs/streaming/rate-limits).
  deepgram          wss://api.deepgram.com/v1/listen, nova-3, language=multi
                    (code-switching), smart_format, punctuate, interim_results, vad_events,
                    profanity_filter=false, endpointing=600 (= min_turn_silence),
                    utterance_end_ms=2400 (= max_turn_silence); no keyterms (Pingo sends none).
                    Deepgram turn = the is_final results up to and including one with
                    speech_final true (or an UtteranceEnd); its language is the most
                    common word-level `language` in that turn.
  flux              wss://api.deepgram.com/v2/listen, flux-general-multi (Deepgram's
                    voice-agent model, native EndOfTurn), eot_threshold 0.7 (default),
                    eot_timeout_ms 2400 (= max_turn_silence), 80 ms frames as Deepgram
                    recommends. Only clips whose languages Flux supports (en es fr de hi
                    ru pt ja it nl), so no Chinese or Korean. Turn = an EndOfTurn TurnInfo;
                    language = its `languages[0]`.

Latency, AssemblyAI's definitions (docs/streaming/evaluations/voice-agents.md), all
stamped client-side when the message reaches this process's event loop:
  ttft_ms         first result with words - first audio frame sent
  ttct_ms         last final result of the utterance - speech end
  eot_latency_ms  end-of-turn signal - speech end (AssemblyAI: the end_of_turn Turn;
                  Deepgram: speech_final, or UtteranceEnd if that comes first)
  speech end      first frame send time - one frame + the clip's last sample above
                  -40 dBFS (from the WAV)
TTCT and end-of-turn are only reported for single-utterance clips that produced
exactly one turn (pause_filler clips are skipped for latency).
A clip whose turn only ended when the stream closed (no end-of-turn signal in
the 4 s of trailing silence) gets no_eot_in_4s instead of a latency.
"""
import asyncio, json, os, sys, time, urllib.parse
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from server.audio import read_pcm16_mono

D = os.path.dirname(os.path.abspath(__file__))
ARM = sys.argv[1] if len(sys.argv) > 1 else "assemblyai"
ARMS = {"assemblyai": {}, "deepgram": {}, "flux": {}}
PROVIDER = "deepgram" if ARM == "flux" else ARM
OUT = sys.argv[2] if len(sys.argv) > 2 else f"{D}/bench/{ARM}_raw.jsonl"
FRAME = 1600; SIL = b"\x00" * FRAME; RATE = 16000; TAIL_FRAMES = 80
DG_PARAMS = {"model": "nova-3", "language": "multi", "encoding": "linear16", "sample_rate": RATE, "channels": 1,
             "smart_format": "true", "punctuate": "true", "interim_results": "true",
             "endpointing": 600, "utterance_end_ms": 2400, "profanity_filter": "false", "vad_events": "true"}
AAI_MIN_GAP_S = 12.5  # 5 new streams a minute on the free plan
NO_LATENCY = ("pause_filler",)


def speech_end_s(pcm, threshold=328):
    """Seconds from clip start to the last sample above -40 dBFS (328 / 32768)."""
    samples = memoryview(pcm).cast("h")
    for i in range(len(samples) - 1, -1, -1):
        if abs(samples[i]) > threshold:
            return (i + 1) / RATE
    return 0.0


async def play(send, pcm):
    """Send clip + tail silence in real time. Returns the monotonic time of the first frame."""
    frames = [pcm[i:i + FRAME] for i in range(0, len(pcm), FRAME)] + [SIL] * TAIL_FRAMES
    t0 = time.monotonic(); first = None
    for k, frame in enumerate(frames):
        await asyncio.sleep(max(0, t0 + (k + 1) * 0.05 - time.monotonic()))
        await send(frame)
        if first is None:
            first = time.monotonic()
    return first


async def run_assemblyai(pcm):
    from server.config import load_settings
    from server.stt import StreamingSTT, stt_query_params
    stt_settings = load_settings(f"{os.path.dirname(D)}/configs/baseline.yaml")["stt"]
    params = stt_query_params(stt_settings)
    events = []
    async def on_event(event):
        events.append((time.monotonic(), event))
    stt = StreamingSTT(os.environ["ASSEMBLYAI_API_KEY"], params, on_event)
    await stt.connect()
    t_start = await play(stt.send_audio, pcm)
    t_close = time.monotonic()
    await asyncio.sleep(0.5); await stt.close()
    session_id = next((e.get("id") for _, e in events if e.get("type") == "Begin"), None)
    first = next((t for t, e in events if e.get("type") == "Turn" and (e.get("transcript") or "").strip()), None)
    turns = []
    for t, e in events:
        if e.get("type") == "Turn" and e.get("end_of_turn") and (e.get("transcript") or "").strip():
            turns.append({"text": e["transcript"], "language_code": e.get("language_code"),
                          "language_confidence": e.get("language_confidence"), "t_final": t, "t_boundary": t})
    return t_start, first, turns, t_close, session_id


async def run_deepgram(pcm):
    import websockets
    url = "wss://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(DG_PARAMS)
    events = []
    async with websockets.connect(url, additional_headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"},
                                  max_size=None, open_timeout=30) as ws:
        async def rx():
            async for m in ws:
                if isinstance(m, str):
                    events.append((time.monotonic(), json.loads(m)))
        task = asyncio.create_task(rx())
        t_start = await play(ws.send, pcm)
        t_close = time.monotonic()
        await ws.send(json.dumps({"type": "CloseStream"}))
        try:
            await asyncio.wait_for(task, 5)
        except (asyncio.TimeoutError, Exception):
            task.cancel()
    def words(e):
        alt = ((e.get("channel") or {}).get("alternatives") or [{}])[0]
        return alt
    session_id = next((e.get("request_id") or (e.get("metadata") or {}).get("request_id") for _, e in events
                       if e.get("request_id") or (e.get("metadata") or {}).get("request_id")), None)
    first = next((t for t, e in events if e.get("type") == "Results" and words(e).get("transcript", "").strip()), None)
    turns, cur = [], None
    for t, e in events:
        if e.get("type") == "Results" and e.get("is_final"):
            alt = words(e)
            if alt.get("transcript", "").strip():
                cur = cur or {"parts": [], "langs": Counter(), "t_final": None}
                cur["parts"].append(alt["transcript"]); cur["t_final"] = t
                cur["langs"].update(w.get("language") for w in alt.get("words") or [] if w.get("language"))
            if e.get("speech_final") and cur:
                cur["t_boundary"] = t; turns.append(cur); cur = None
        elif e.get("type") == "UtteranceEnd" and cur:
            cur["t_boundary"] = t; turns.append(cur); cur = None
    if cur:
        cur["t_boundary"] = None; turns.append(cur)
    out = []
    for c in turns:
        lang = c["langs"].most_common(1)[0][0] if c["langs"] else None
        out.append({"text": " ".join(c["parts"]), "language_code": lang.split("-")[0].lower() if lang else None,
                    "language_confidence": None, "t_final": c["t_final"], "t_boundary": c["t_boundary"]})
    return t_start, first, out, t_close, session_id


async def run_flux(pcm):
    """Flux multilingual, native EndOfTurn. 80 ms frames per Deepgram's guide."""
    import websockets
    params = {"model": "flux-general-multi", "encoding": "linear16", "sample_rate": RATE,
              "eot_threshold": .7, "eot_timeout_ms": 2400}
    events = []
    async with websockets.connect("wss://api.deepgram.com/v2/listen?" + urllib.parse.urlencode(params),
                                  additional_headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"},
                                  max_size=None, open_timeout=30) as ws:
        async def rx():
            async for msg in ws:
                if isinstance(msg, str):
                    events.append((time.monotonic(), json.loads(msg)))
        task = asyncio.create_task(rx())
        size = 2560  # 80 ms, 16 kHz PCM16
        frames = [pcm[i:i+size] for i in range(0, len(pcm), size)] + [b"\x00" * size] * 50
        origin = time.monotonic(); t_start = None
        for k, frame in enumerate(frames):
            await asyncio.sleep(max(0, origin + (k+1) * .08 - time.monotonic()))
            await ws.send(frame)
            if t_start is None:
                t_start = time.monotonic()
        t_close = time.monotonic()
        await ws.send(json.dumps({"type": "CloseStream"}))
        try:
            await asyncio.wait_for(task, 5)
        except Exception:
            task.cancel()
    session_id = next((e.get("request_id") for _, e in events if e.get("request_id")), None)
    first = next((t for t, e in events if e.get("type") == "TurnInfo" and e.get("transcript", "").strip()), None)
    turns = []
    pending = None
    for t,e in events:
        if e.get("type") != "TurnInfo" or not e.get("transcript", "").strip():
            continue
        pending = {"text": e["transcript"], "language_code": (e.get("languages") or [None])[0],
                   "language_confidence": None, "t_final": t, "t_boundary": None}
        if e.get("event") == "EndOfTurn":
            pending["t_boundary"] = t
            turns.append(pending); pending = None
    if pending:
        turns.append(pending)
    return t_start, first, turns, t_close, session_id


async def one(case):
    pcm = read_pcm16_mono(f"{D}/{case['audio']}"); pcm += SIL[: (-len(pcm)) % FRAME]
    runner = run_flux if ARM == "flux" else run_assemblyai if PROVIDER == "assemblyai" else run_deepgram
    t_start, first, turns, t_close, session_id = await runner(pcm)
    t_end = t_start - (.08 if ARM == "flux" else .05) + speech_end_s(pcm)  # frame 0 holds 0-50 ms and leaves at 50 ms
    row = {"id": case["id"], "arm": ARM, "provider": PROVIDER, "session_id": session_id,
           "turns": [{k: v for k, v in t.items() if not k.startswith("t_")} for t in turns],
           "hypothesis": " ".join(t["text"] for t in turns)}
    if first is not None:
        row["ttft_ms"] = round((first - t_start) * 1000)
    if case["category"] not in NO_LATENCY and len(turns) == 1 and (turns[0]["t_boundary"] or t_close + 1) > t_close:
        # No end-of-turn during 4 s of trailing silence: the turn was only flushed when
        # the stream closed. Counted, not given a latency.
        row["no_eot_in_4s"] = True
    elif case["category"] not in NO_LATENCY and len(turns) == 1:
        row["ttct_ms"] = round((turns[0]["t_final"] - t_end) * 1000)
        if turns[0]["t_boundary"] is not None:
            row["eot_latency_ms"] = round((turns[0]["t_boundary"] - t_end) * 1000)
    return row


def pct(values, p):
    values = sorted(values); k = (len(values) - 1) * p; lo = int(k); hi = min(lo + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo))


def latency_summary(rows):
    out = {}
    for key in ("ttft_ms", "ttct_ms", "eot_latency_ms"):
        v = [r[key] for r in rows if key in r]
        if v:
            out[key] = {"n": len(v), "p50": pct(v, .5), "p90": pct(v, .9), "p95": pct(v, .95)}
    return out


_last_open = 0.0


async def main():
    cases = [json.loads(l) for l in open(f"{D}/cases.jsonl")]
    if ARM == "flux":
        supported = {"en", "es", "fr", "de", "hi", "ru", "pt", "ja", "it", "nl"}
        cases = [c for c in cases if set(c["languages"]) <= supported]
    if os.environ.get("ONLY"):
        cases = [c for c in cases if c["id"].startswith(tuple(os.environ["ONLY"].split(",")))]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # RESUME=1 keeps the rows already in OUT and runs only the missing clips.
    rows = [json.loads(l) for l in open(OUT)] if os.environ.get("RESUME") and os.path.exists(OUT) else []
    done = {r["id"] for r in rows}
    with open(OUT, "a" if rows else "w") as f:
        for c in cases:
            if c["id"] in done:
                continue
            # Retry a dropped or refused stream (e.g. "Too many concurrent sessions",
            # which comes back as a stream with no turns). Every clip has speech.
            for attempt in range(4):
                if PROVIDER == "assemblyai":
                    global _last_open
                    await asyncio.sleep(max(0, _last_open + AAI_MIN_GAP_S - time.monotonic()))
                    _last_open = time.monotonic()
                try:
                    row = await asyncio.wait_for(one(c), 90)
                    row["attempts"] = attempt + 1
                    if row["turns"]:
                        break
                    print(f"{c['id']}: no turns", file=sys.stderr)
                except Exception as exc:
                    print(f"{c['id']}: {type(exc).__name__}", file=sys.stderr)
                await asyncio.sleep(10)
            else:
                row = {"id": c["id"], "provider": PROVIDER, "turns": [], "hypothesis": "", "error": "failed 4 times", "attempts": 4}
            rows.append(row); f.write(json.dumps(row, ensure_ascii=False) + "\n"); f.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)
    print(json.dumps({"arm": ARM, **latency_summary(rows),
                      "no_eot_in_4s": sum(1 for r in rows if r.get("no_eot_in_4s"))}))

if __name__ == "__main__":
    asyncio.run(main())
