# Pingo

Pingo is a real-time voice tutor for language learners, built only on AssemblyAI. The learner says a word or phrase. Pingo replies with a translation, a pronunciation guide and one short tip, then speaks the phrase. This repo is the backend for an AssemblyAI Applied AI Engineer take-home. The tutor is the demo, and the evaluation below is the pitch.

Live app: [pingo-ai.lovable.app](https://pingo-ai.lovable.app) (UI built in Lovable). Backend: [pingo-voice-backend.onrender.com](https://pingo-voice-backend.onrender.com) (this repo, a WebSocket server). The API key stays on the server.

## Evaluation: AssemblyAI vs Deepgram on the same audio

**Verdict: AssemblyAI is more accurate than both Deepgram models (WER, entities, sentence errors, English and Spanish). Deepgram ends turns sooner.**

The same 105 clips were streamed live to each provider from one machine and scored with the same `score.py`. The runs took place on 13 November 2026, between about 4:45 and 5:25 AM PT. Results: [`assemblyai`](test_cases/bench/assemblyai/RESULTS.md), [`deepgram`](test_cases/bench/deepgram/RESULTS.md), [`flux`](test_cases/bench/flux/RESULTS.md). Raw output, with each provider's session or request id per clip, is in `test_cases/bench/*_raw.jsonl`.

### Scorecard

Error rates and latency: lower is better. The winner of each pair is in bold; no bold means there's no clear winner. Nova-3 is compared with AssemblyAI on all 105 clips. Flux returned results on 93 of 105 clips; it does not support Chinese or Korean.

| Metric | AssemblyAI U3.6 Pro (n=105) | Deepgram Nova-3 (n=105) | Deepgram Flux (n=93) |
|---|---|---|---|
| WER, normalized (84 clips / 553 words) | **6.33%** | 9.76% | 9.22% |
| Semantic WER, LLM judge (84 clips) | **2.71%** | 4.52% | 4.52% |
| Missed entity rate | **5.88%** (2/34) | 20.59% (7/34) | 19.35% (6/31) |
| Sentence error rate | **23.81%** | 28.57% | 31.18% |
| Hard-set WER (26 clips / 157 words) | **10.83%** | 19.11% | 15.92% |
| English WER (22 clips / 183 words) | **0.55%** | 4.37% | 4.37% |
| Spanish WER (24 clips / 138 words) | **7.97%** | 15.22% | 11.59% |
| Hindi WER (1 clip / 12 words) | 0.0% | 0.0% | 0.0% |
| Confident-wrong language rate (confidence ≥ 0.6) | 1.80% (2/111) | not comparable¹ | not comparable¹ |
| Hesitation clips: turns per clip, ps01 / h13 / h14 (1 is ideal) | 3 / 3 / 3 | **2 / 1 / 2** | **3 / 2 / 2** |
| Hesitation clips: WER (3 clips / 25 words) | **12.0%** | 24.0% | **8.0%** |
| TTFT from speech start², p50 / p90 (from stream start) | 368 / 462 ms (1,055 / 1,181) | 378 / 473 ms (1,067 / 1,170) | 356 / 577 ms (1,058 / 1,294) |
| TTCT³, p50 / p90 | 1,124 / 2,802 ms (n=101) | **936 / 1,020 ms** (n=89) | **982 / 1,993 ms** (n=90) |
| End-of-turn latency³, p50 / p90 | 1,124 / 2,802 ms (n=101) | **939 / 1,024 ms** (n=89) | **982 / 1,993 ms** (n=90) |

1. Deepgram returns no language confidence, so the confident-wrong rate can't be computed for it. That's different from 0%.
2. **TTFT** is the time from the first audio frame sent to the first result with words. AssemblyAI's published definition counts from the start of the stream, which is the figure in parentheses. Every clip opens with silence, measured per clip from the WAV as the first sample above −40 dBFS: 601–890 ms, median 704 ms. The main figure subtracts that clip's lead silence, so it counts from the start of speech.
3. **TTCT** is the time from speech end to the final transcript. **End-of-turn latency** is the time from speech end to the turn-boundary signal. Speech end is the time the first frame was sent, minus one frame, plus the clip's last sample above −40 dBFS. All times are stamped client-side, following AssemblyAI's [voice-agent evaluation guide](https://www.assemblyai.com/docs/streaming/evaluations/voice-agents.md). Latency covers single-utterance clips that produced one turn with an end-of-turn signal, which leaves out the hesitation clips.
   - For AssemblyAI the two rows are equal by design: U3.6 Pro sends the final transcript only in the `end_of_turn` Turn. The same holds for Flux and its `EndOfTurn`.
   - Nova-3's end of turn is the first `speech_final`, or an `UtteranceEnd` if that comes first. AssemblyAI's p90 is the `max_turn_silence` 2,400 ms fallback.
4. **Settings, matched as closely as Deepgram allows.** No keyterms were sent on either side: Pingo's baseline sends AssemblyAI none, so Deepgram got none either.

   | AssemblyAI (Pingo baseline) | Deepgram Nova-3 (`/v1/listen`) | Deepgram Flux (`/v2/listen`) |
   |---|---|---|
   | `universal-3-6-pro`, `language_detection` | `nova-3`, `language=multi` | `flux-general-multi` |
   | `min_turn_silence` 600 | `endpointing=600` | no equivalent (`eot_threshold` 0.7, default) |
   | `max_turn_silence` 2400 | `utterance_end_ms=2400` | `eot_timeout_ms=2400` |
   | semantic end-of-turn check | no equivalent; ends on silence | semantic end of turn |
   | learner-context `prompt` | no equivalent | no equivalent |
   | `voice_focus` far-field, `vad_threshold` 0.2 | no equivalent (`vad_events` on) | no equivalent |
   | keyterms: none | keyterms: none | keyterms: none |
   | `filter_profanity` off | `profanity_filter=false` | n/a |
   | formatting built in | `smart_format`, `punctuate`, `interim_results` | built in |
5. Semantic WER uses the same judge for every arm: AssemblyAI LLM Gateway `gemma-4-31b` at temperature 0. That's the vendor's own model, so the plain WER rows are the ones that rely on no judge.
6. A stream that returned no turns was retried, up to 4 attempts. AssemblyAI streams were spaced at least 12.5 s apart, to stay within the free plan's new-sessions-per-minute limit of 5 (paid plans start at 200, per AssemblyAI's [rate limits](https://www.assemblyai.com/docs/streaming/rate-limits)). One clip was retried on each of AssemblyAI and Nova-3. All 303 streams returned transcripts.

### Test set

105 synthetic edge-tts clips, listed in `test_cases/cases.jsonl`. Endpointing is matched on both providers (600 / 2,400 ms). By design there's no 1,280 ms arm.

| Language | Clips |
|---|---|
| en | 22 |
| es | 24 |
| de | 12 |
| ja | 9 |
| zh | 7 |
| fr | 7 |
| ko | 5 |
| it | 3 |
| hi | 1 |
| en+es mixed | 8 |
| en+hi mixed | 7 |

### Worked examples

These come from the runs above. End of turn is measured from speech end.

| Clip | Spoken | AssemblyAI | Deepgram Nova-3 | Deepgram Flux |
|---|---|---|---|---|
| `ent09` entities | Dr. Nguyen booked a flight to São Paulo on November 3rd. | Dr. Nguyen booked a flight to São Paulo on November 3rd. (0 errors, 2,835 ms) | Dr. Ngwin booked a flight to Sao Paulo on November 3. (2 errors, 892 ms) | Doctor. Nguyen booked a flight to Sao Paulo on November third. (0 errors after normalization, 2,423 ms) |
| `h20` accented es | Quiero un café, por favor. | Quiero un café, por favor. (es, 0 errors) | Quero Uncafé or favor. (labelled en, 4 errors) | Quero un cafe or favor. (labelled en, 3 errors) |
| `cs04` en+es | I think the answer is muy bien. | I think the answer is muy bien. (0 errors) | I think the answer is Muibian. (2 errors) | I think the answer is Muibian. (2 errors) |
| `hi02` en+hi (AssemblyAI loses) | Aaj मेरी Spanish class है। | आज मेरी स्पैनिश क्लास है। (3 errors: English words written in Devanagari) | आज मेरी Spanish class है. (1 error) | आज मेरी Spanish class है. (1 error) |
| `h13` hesitation | Yo... eh... yo fui, no, yo voy al mercado. | 就。 / 哎。 / Yo fui, no, yo voy al mercado. (3 turns; fillers labelled zh at confidence 1.0) | Jo E. Yo fui, no, yo voy al mercado. (1 turn) | Yo. / Yo fui, no, yo voy al mercado. (2 turns) |
| `zh02` Chinese | 请问洗手间在哪里？ | 请问洗手间在哪里？ (0 errors, 1,067 ms) | 请问洗手间在哪里 (0 errors) | not supported |

Rerun everything with `python test_cases/stt_bench.py assemblyai|deepgram|flux`, then `python test_cases/score.py test_cases/bench/<arm>_raw.jsonl --out=test_cases/bench/<arm>`. For Flux, add `--ids-from=test_cases/bench/flux_raw.jsonl`. Keys are read from `ASSEMBLYAI_API_KEY` and `DEEPGRAM_API_KEY` and are never written to disk.

## Architecture

Browser mic (16 kHz `pcm_s16le`) → `/ws` on this server → AssemblyAI streaming STT (`universal-3-6-pro`) → AssemblyAI LLM Gateway (`gemma-4-31b`) → AssemblyAI TTS (24 kHz PCM) → browser. It uses one vendor and one key (`ASSEMBLYAI_API_KEY`).

On each finished turn (`end_of_turn` with words), the server asks the tutor model for a structured lesson and streams the TTS back. The model also decides whether the turn is an attempt at the pending phrase and grades it. Partials only update the on-screen transcript. STT detects 32 languages, and TTS and tutoring cover six: English, Spanish, French, German, Italian and Portuguese.

## Run it

Python 3.12+ (Render uses 3.12.11).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ASSEMBLYAI_API_KEY=your_key      # or cp .env.example .env
python -m server                        # 127.0.0.1:8000, GET /health, WS /ws
```

Flags: `--config` (default `configs/baseline.yaml`, or `PINGO_CONFIG`), `--host`, `--port` (or `PORT`), `--target-language`, `--log`, `--set stt.max_turn_silence=1800` (repeatable).

Tests and eval: `pip install -r requirements-dev.txt`, then `python -m unittest discover -s tests -t .`. The eval also needs `DEEPGRAM_API_KEY` for the Deepgram run. Rebuilding the clips (`python test_cases/make_cases.py`) needs ffmpeg.

Deploy: `render.yaml` is a Blueprint for a free Python service (`python -m server --host 0.0.0.0 --port $PORT`), with `ASSEMBLYAI_API_KEY` set in the dashboard. The free plan sleeps after 15 minutes, so `.github/workflows/keep-warm.yml` pings `/health` every 10 minutes.

## Configuration

Later sources win: defaults in `server/config.py` → YAML → `PINGO_*` env vars → `--set`. Each browser `start` / `update_config` message is merged per session, so one learner's changes don't affect another. An empty client `prompt` does not clear the server prompt.

| Setting | Baseline | Why |
|---|---|---|
| `stt.speech_model` | `universal-3-6-pro` | 32 languages, native code-switching |
| `stt.language_detection` | `true` | `language_code` and `language_confidence` on each turn |
| `stt.language_codes` | `[]` | Open to all 32 languages |
| `stt.prompt` | short learner-context string | Context about the audio, not instructions |
| `stt.keyterms_prompt` | `[]` | Off. Boosting the target phrase can make STT "hear" the correct form and hide a learner's mistake. |
| `stt.agent_context_enabled` | `false` | Off, for the same reason |
| `stt.mode` | `balanced` | Turn-taking preset |
| `stt.min_turn_silence` | `600` ms | Up from 128 ms so a short thinking pause doesn't end the turn (400 cut learners off in live use) |
| `stt.max_turn_silence` | `2400` ms | Up from 1,280 ms. This drives the p90 latency loss above, so it's the next thing to tune. |
| `stt.interruption_delay` | `250` ms | Down from 500 ms so Pingo stops sooner when talked over |
| `stt.vad_threshold` | `0.2` | Documented default, sent explicitly |
| `stt.voice_focus` | `far-field` | Laptop mics, threshold left at the default |
| `stt.filter_profanity` | `false` | Masking would rewrite what the learner said |
| `llm.model` / `temperature` / `max_tokens` | `gemma-4-31b` / `0.2` / `450` | Strict JSON via `response_format`, consistent lessons |
| `tts.voices` | one per language | Catalog voices, plus a voice id for Spanish |

Access control: `PINGO_ALLOWED_ORIGINS`, `PINGO_ALLOWED_ORIGIN_REGEX`, `PINGO_MAX_CONNECTIONS_PER_IP` (default 3), `PINGO_MAX_NEW_CONNECTIONS_PER_IP_PER_MINUTE` (default 10) and `PINGO_TRUST_PROXY_HEADERS` (on by default under Render).

Protocol: the client sends `start` (`target_language`, optional `native_language` and `config`), binary PCM16 frames, `update_config`, `set_target`, `replay` and `stop`. The server sends `status`, `transcript` (partial or final, with language), `lesson`, `agent_text`, `tts_start` + PCM + `tts_end`, `barge_in` and `error`.

## Key decisions

- **One vendor, one key.** STT, LLM Gateway and TTS are all AssemblyAI, server-side.
- **Act on finished turns only.** Partials are displayed, never acted on.
- **Language labels are hints.** Wrong labels at high confidence do happen: in the benchmark run, the hesitation sounds in a Spanish clip (`h13`, "eh…") came back as Chinese at confidence 1.0. So the rules rely on persistence rather than a single score:
  - Changing the learner's own language takes **two consecutive** turns in the same language, each with ≥ 3 words and confidence ≥ 0.6, in a TTS language that isn't the target.
  - A turn is treated as a request about a third language if it's in a non-Latin script, or if it's Latin-script with confidence ≥ 0.5. Either way, the tutor prompt always carries the label and its score.
- **Keyterms and agent context off,** so STT doesn't normalize away the learner's mistakes.
- **The LLM grades practice attempts** (`correct` / `close` / `try_again`). The server pins the stored phrase on attempts, so the lesson card shows the phrase being practised. A longer phrase that contains the stored one ("Hola, ¿cómo estás?" after "Hola") counts as a new phrase. After two misses, Pingo reads the phrase slowly, syllable by syllable.
- **Who may connect.** CORS and a WebSocket Origin check allow only the Lovable app, its previews and localhost. Each IP gets at most 3 open sockets and 10 new ones a minute. There are no accounts; this is a demo endpoint.
- **TTS sessions are reused.** On the free plan the account can open 5 new TTS sessions a minute (paid plans start at 200, per AssemblyAI's [rate limits](https://www.assemblyai.com/docs/streaming/rate-limits)), so each connection warms one session per voice on `start`.

Pricing, from [AssemblyAI pricing](https://www.assemblyai.com/pricing) as of 12 November 2026: streaming is $0.45/hr of open session time, idle included. `gemma-4-31b` costs $0.14 / 1M input tokens and $0.40 / 1M output tokens. TTS costs $15 / 1M characters. Each turn's log line records its usage.

## Layout

| Path | What it does |
|---|---|
| `server/app.py` | FastAPI app, `/health`, `/ws`, origin and per-IP limits |
| `server/session.py` | One learner session: STT events, language rules, practice state, barge-in, TTS plan, log |
| `server/stt.py` / `llm.py` / `tts.py` | AssemblyAI streaming STT, LLM Gateway, TTS clients |
| `server/config.py` | Defaults, YAML, env, `--set`, per-session merge |
| `server/audio.py` / `log.py` | WAV → 16 kHz PCM16, JSONL turn log |
| `configs/baseline.yaml` | Shipped defaults |
| `tests/` | Unit tests with fake STT, LLM and TTS |
| `test_cases/` | 105 clips, `stt_bench.py` (AssemblyAI, Deepgram Nova-3, Flux), `score.py`, `latency.py`, results in `bench/` |

## Limitations

- Synthetic audio only, small per-language samples, and one run per provider.
- Turn latency p90 loses to Deepgram at the current `max_turn_silence`, and the tutor LLM call, which isn't streamed, dominates end-to-end latency.
- Concurrency hasn't been load-tested. On the free plan, 5 new TTS sessions a minute limit the account to about 2 new learners a minute; the per-IP limits live in one process's memory. Render's free plan is demo-only.
- TTS speaks six languages, and nothing checks the accuracy of the LLM's grading. There's no phoneme-level pronunciation score and no TTS speed control.
- Browser echo can trigger a false barge-in. Headphones or a higher `vad_threshold` help.
