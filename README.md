# Pingo

Pingo is a real-time voice tutor for language learners, built only on AssemblyAI. The learner says a word or phrase. Pingo replies with a translation, a pronunciation guide and one short tip, then speaks the phrase. This repo is the backend for an AssemblyAI Applied AI Engineer take-home. The tutor is the demo, and the evaluation below is the pitch.

Live app: [pingo-ai.lovable.app](https://pingo-ai.lovable.app) (UI built in Lovable). Backend: [pingo-voice-backend.onrender.com](https://pingo-voice-backend.onrender.com) (this repo, a WebSocket server). The API key stays on the server.

## Evaluation: AssemblyAI vs Deepgram on the same audio

The customer is replacing an incumbent provider, so the question is "did AssemblyAI clear the bar we agreed on, and does it beat what they run today?". I streamed the same 109 clips live to AssemblyAI and Deepgram from one machine and scored both with the same `score.py`. Results: [`assemblyai/RESULTS.md`](test_cases/bench/assemblyai/RESULTS.md), [`deepgram/RESULTS.md`](test_cases/bench/deepgram/RESULTS.md), raw output in `test_cases/bench/*_raw.jsonl`. The runs took place on 13 November 2026, between about 3:00 and 3:50 AM PT.

### Scorecard (pass marks are proposals, not agreed with any customer)

Metric definitions follow AssemblyAI's [voice-agent evaluation guide](https://www.assemblyai.com/docs/streaming/evaluations/voice-agents.md). Error rates: lower is better.

| Criterion | Proposed pass mark | AssemblyAI | Deepgram | n | Result |
|---|---|---|---|---|---|
| WER (normalized) | ≤ incumbent and ≤ 8% | **5.85%** | 9.70% | 88 clips / 598 words | AssemblyAI passes and wins |
| Missed entity rate | ≤ incumbent and ≤ 5% | **5.88%** (2/34) | 20.59% (7/34) | 18 clips / 34 entities | Wins; misses 5% by 1 entity |
| Sentence error rate | ≤ incumbent | **22.94%** | 28.44% | 109 clips | Wins |
| Language-ID accuracy (per final turn) | ≥ 90% | 94.92% (112/118) | **96.52%** (111/115) | turns shown | Both pass; Deepgram slightly ahead (6 vs 4 wrong turns, within noise) |
| TTCT, p90 | ≤ 1,000 ms | 2,787 ms | **1,016 ms** | 89 clips both finalized | **AssemblyAI loses** |
| End-of-turn finalization latency, p90 | ≤ 1,000 ms | 2,787 ms | **1,017 ms** | 89 | **AssemblyAI loses** |
| Turns with no end-of-turn in 4 s of silence | 0 | **0 / 102** | 13 / 102 | 102 single-utterance clips | AssemblyAI wins |
| TTFT, p90 (reported only; the guide says it says little for real-time STT) | none | 1,184 ms | 1,165 ms | 109 | Tie |
| Pingo pipeline: end of speech → first reply audio, p90 (not an STT metric) | ≤ 2,500 ms | 6,386 ms | not run | 100 turns | Fails |

**Where AssemblyAI wins:** accuracy. WER, missed entities and sentence errors are all clearly lower, and it gets entities and formatting right ("Dr. Nguyen … São Paulo … November 3rd", "March 5th, 2027") where Deepgram writes "Ngwin", "03/05/2027" and "einundzwanzigsten April". It also always ended the turn on its own. Deepgram never sent an end-of-turn on 13 clips, 8 of them Chinese or Korean, and only returned their transcripts when the stream closed.

**Where AssemblyAI loses:**
- **Turn latency.** On the 89 clips where both providers ended the turn, Deepgram's p50 is 947 ms against 1,128 ms, and its p90 is 1,017 ms against 2,787 ms. 17 of AssemblyAI's 102 turns waited for Pingo's 2,400 ms `max_turn_silence` fallback. That limit is my own setting, chosen so a hesitating learner isn't cut off, and lowering it is the first thing I'd test. Deepgram's `endpointing=600` ends every turn after 600 ms of silence, which would also cut off a learner who pauses to think.
- **Hinglish code-switching.** WER is 32.14% for AssemblyAI against 10.71% for Deepgram (5 clips). AssemblyAI writes the English words in Devanagari ("कॉफ़ी", "स्पैनिश क्लास"), and plain WER counts that as an error. The semantic judge treats it as harmless.
- **Two-speaker cpWER:** 17.78% against 8.89% (4 clips).
- **Italian:** 1 clip, "Dove è" instead of "Dov'è".

**Per language** (clips / reference units; one or two clips is a smoke test, not a result):

| Language | Metric | AssemblyAI | Deepgram | n |
|---|---|---|---|---|
| English | WER | **0.0%** | 2.38% | 10 clips / 84 words |
| German | WER | **2.86%** | 8.57% | 6 / 35 |
| Spanish | WER | 0.0% | 0.0% | 3 / 18 |
| French | WER | 0.0% | 0.0% | 2 / 14 |
| Italian | WER | 16.67% | **0.0%** | 1 / 6 |
| Japanese | CER | **0.0%** | 5.08% | 6 / 59 chars |
| Chinese | CER | 0.0% | 0.0% | 5 / 35 |
| Korean | CER | 0.0% | 0.0% | 4 / 32 |
| English + Spanish | WER | **13.33%** | 26.67% | 7 / 45 |
| English + Hindi | WER | 32.14% | **10.71%** | 5 / 28 |
| Hard set (31 clips) | WER | **10.83%** | 19.11% | 26 / 157 |
| Spanish | language ID | 82.14% (23/28) | **92.0%** (23/25) | turns |
| German | language ID | **100%** (12/12) | 91.67% (11/12) | turns |

Semantic WER (meaning-changing errors only): AssemblyAI 2.51%, Deepgram 4.01%. Both providers were judged by the same model, AssemblyAI LLM Gateway `gemma-4-31b` at temperature 0. That is the vendor's own model, so the plain-WER rows above, which are judge-free, are the ones to rely on.

### Worked examples

Numbers are per clip, AssemblyAI / Deepgram. TTCT and end-of-turn latency are equal for AssemblyAI because its final transcript and end-of-turn signal arrive in the same message.

| Clip | Spoken (= expected) | AssemblyAI | Deepgram | WER | Missed entities | Language | TTFT | TTCT | End of turn |
|---|---|---|---|---|---|---|---|---|---|
| `ent09` entities | Dr. Nguyen booked a flight to São Paulo on November 3rd. | Dr. Nguyen booked a flight to São Paulo on November 3rd. | Dr. Ngwin booked a flight to Sao Paulo on November 3. | 0% / 18.2% | 0 / 2 | en / en | 1,141 / 1,053 ms | 2,808 / 883 ms | 2,808 / 883 ms |
| `cs04` en+es | I think the answer is muy bien. | I think the answer is muy bien. | I think the answer is Muibian. | 0% / 28.6% | – | en / en | 999 / 1,139 ms | 1,187 / 953 ms | 1,187 / 953 ms |
| `h20` accented es | Quiero un café, por favor. | Quiero un café, por favor. | Quero Uncafé or favor. | 0% / 80% | – | es / **en** | 1,033 / 2,061 ms | 1,174 / 982 ms | 1,174 / 982 ms |
| `hi02` en+hi (AssemblyAI loses) | Aaj मेरी Spanish class है। | आज मेरी स्पैनिश क्लास है। | आज मेरी Spanish class है. | 60% / 20% | – | hi / hi | 1,064 / 1,137 ms | 1,099 / 1,019 ms | 1,099 / 1,019 ms |
| `zh02` Chinese | 请问洗手间在哪里 | 请问洗手间在哪里 | 请问洗手间在哪里 | 0% / 0% (CER) | – | zh / zh | 1,131 / 1,087 ms | 1,148 ms / no end of turn | 1,148 ms / no end of turn |

### How the comparison was run

`test_cases/stt_bench.py assemblyai|deepgram` opens a fresh stream for each clip. It sends the clip's 16 kHz PCM16 in 50 ms frames, each frame when its 50 ms have elapsed, as a microphone would, then 4 s of silence. Both providers ran from the same machine, one stream per provider at a time, and the two runs overlapped for most clips. Keys are read from `ASSEMBLYAI_API_KEY` and `DEEPGRAM_API_KEY` and are never written to disk.

- **AssemblyAI:** `universal-3-6-pro` through the server's own `StreamingSTT` class (official SDK) with Pingo's baseline config: `language_detection`, the learner-context prompt, `min_turn_silence` 600, `max_turn_silence` 2400, Voice Focus far-field. A turn is a `Turn` message with `end_of_turn: true`, and its language is that message's `language_code`.
- **Deepgram:** `wss://api.deepgram.com/v1/listen` with `model=nova-3&language=multi` (Deepgram's code-switching mode, 55 languages), `smart_format`, `punctuate`, `interim_results`, `endpointing=600` (to match our 600 ms), `utterance_end_ms=1000` and `vad_events`. Deepgram suggests `endpointing=100` for code-switching; I used 600 to keep turn length comparable. Its end of turn is the first `is_final` result with `speech_final: true`, or an `UtteranceEnd` if that arrives first. The turn's text is the `is_final` results up to that point. Deepgram streaming gives word-level `language` but no turn label, so a turn's language is the most common word language. It returns no language confidence, so the confident-wrong rate is marked "not comparable" rather than 0. `score.py` would mark language ID "not comparable" too if a provider sent no labels at all.
- **Latency,** stamped client-side at the moment each message reaches the benchmark process, the same way for both. **TTFT** = first result with words − first audio frame sent. Every clip starts with about 0.5 s of silence, and that is included. **TTCT** = last final result of the utterance − speech end. **End-of-turn finalization latency** = end-of-turn signal − speech end. Speech end = first frame time + the clip's last sample above −40 dBFS. Latency covers only single-utterance clips that produced one turn (102 for each provider, without the two-speaker and pause clips). Clips with no end-of-turn during the 4 s of silence are counted separately, not given a latency.
- **Retries:** a stream that returned no turns was retried, up to 4 attempts, for both providers. 21 AssemblyAI streams were refused with "Too many concurrent sessions", because the account's concurrency limit is shared with the deployed app. 1 Deepgram stream (`h18`) came back empty. All 109 clips ended with transcripts from both.

### What a real customer evaluation would add

- **Their audio, not mine.** All 109 clips are synthetic edge-tts voices, with no real learners or accents, and a few planted mistakes the voice did not clearly say (flagged in `cases.jsonl`). The customer eval would use consented learner recordings, transcribed verbatim by people, keeping every mistake.
- **Enough clips to separate the providers.** Roughly 1,000 reference words (about 100–150 short clips) per language puts a 95% interval of about ±2 points around a 5% WER. About 150 turns per language gives about ±5 points around 90% language-ID accuracy. Today, Italian and Hindi rest on 1 clip each, and the language-ID gap between the two providers is 2 turns.
- **Each provider on the customer's real settings,** from the customer's production region, with the pass marks agreed in writing before anything runs. A turn-silence sweep (for example `max_turn_silence` 1,280 vs 2,400) would be scored against both latency and WER, because ending turns sooner trades against cutting learners off.
- **A neutral semantic judge,** meaning another vendor's model or human review of disputed errors.

Rerun everything with: `python test_cases/stt_bench.py assemblyai` and `python test_cases/stt_bench.py deepgram`, then `python test_cases/score.py test_cases/bench/<provider>_raw.jsonl --out=test_cases/bench/<provider>`. Pipeline latency comes from `python test_cases/latency.py`, which plays clips through the deployed backend and writes `test_cases/latency_raw.jsonl`. That run was on Render's free plan between about 2:20 and 2:40 AM PT on 13 November 2026, and 100 of 102 clips produced a spoken reply. The p50 / p90 per stage: final transcript 1,093 / 2,740 ms, tutor LLM 1,700 / 4,200 ms, first TTS audio 104 / 130 ms. The LLM call, which isn't streamed, is the largest stage.

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
| `stt.max_turn_silence` | `2400` ms | Up from 1,280 ms for long hesitations. This drives the p90 latency loss above, so it's the next thing to tune. |
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
- **TTS sessions are reused.** The account allows 5 TTS sessions a minute, so each connection warms one session per voice on `start`.

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
| `test_cases/` | 109 clips, `stt_bench.py` (both providers), `score.py`, `latency.py`, results in `bench/` |

## Limitations

- Synthetic audio only, small per-language samples, and one run per provider.
- Turn latency p90 loses to Deepgram at the current `max_turn_silence`, and the tutor LLM call, which isn't streamed, dominates end-to-end latency.
- Concurrency hasn't been load-tested. TTS sessions (5 a minute) limit the account to about 2 new learners a minute, and the per-IP limits live in one process's memory. Render's free plan is demo-only.
- TTS speaks six languages, and nothing checks the accuracy of the LLM's grading. There's no phoneme-level pronunciation score and no TTS speed control.
- Browser echo can trigger a false barge-in. Headphones or a higher `vad_threshold` help.
