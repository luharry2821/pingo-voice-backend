# Pingo STT test set

109 short learner-style clips, synthetic speech (edge-tts neural voices), 16 kHz mono PCM16 WAV in `audio/`. No production data.

| Category | Cases | Scored with |
|---|---|---|
| english | 10 (US, UK, AU, CA, IN voices) | WER, FWER |
| german | 6 (DE, AT, CH voices) | WER |
| japanese / chinese / korean | 5 / 4 / 4 | CER |
| other (es 3, fr 2, it 1) | 6 | WER |
| codeswitch_en_hi | 5 (Hindi in Devanagari, English in Latin) | code-switching WER |
| codeswitch_en_es | 5 | code-switching WER |
| formatting (time, money, numbers, dates) | 6 | FWER vs WER |
| two_speaker (2 voices, 0.9 s gap) | 4 | cpWER |
| planted_mistake (grammar, gender, minimal pairs) | 22 | WER |
| pause_filler | 1 (plus 2 in the hard set) | WER |
| hard set h01–h21 (real-word swaps, particles, measure words, honorifics, mid-word switches, fast speech, one-word turns, accents, multi-pause, run-on) | 21 | all |
| entity (names, places, dates, brands) | 10 | missed entity rate |

`cases.jsonl`: id, category, languages, reference (exactly as spoken), speakers, audio, entities; planted cases add `intended_correct` and `planted_mistake`; `hard: true` marks the 31-case hard set; `audio_ambiguous` marks planted mistakes the synthetic voice does not clearly say.

## Scripts
- `make_cases.py` rebuilds the clips (edge-tts + ffmpeg).
- `stt_bench.py assemblyai|deepgram` streams every clip in real time to one provider and writes `bench/<provider>_raw.jsonl` (final turns, language labels, TTFT / TTCT / end-of-turn latency).
- `score.py bench/<provider>_raw.jsonl --out=bench/<provider>` writes `results.json` and `RESULTS.md`, with the same code and normalization for every provider.
- `latency.py` plays clips through the deployed Pingo backend: end of speech → first reply audio, per stage.

## Metrics (AssemblyAI conventions)
- **WER** = (S+D+I)/N after the Whisper English normalizer (English) or Whisper basic normalizer (other languages), pooled over cases.
- **Semantic WER**: same alignment; one batched LLM-judge call (AssemblyAI LLM Gateway `gemma-4-31b`, temperature 0, same for every provider) counts only meaning-changing errors.
- **FWER**: no normalization; case and punctuation count.
- **cpWER**: each end-of-turn segment is a speaker segment, assigned to reference speakers by the permutation with the fewest errors.
- **CER** (ja/zh/ko): normalized, spaces removed, per character.
- **Sentence error rate**: % of cases that are not an exact match after normalization.
- **Missed entity rate**: % of marked entities not found in the hypothesis after the same normalization.
- **Code-switching WER**: WER on the EN-HI and EN-ES cases. Script is not transliterated, so an English word written in Devanagari counts as an error.
- **Language-ID accuracy**: % of final turns whose language matches the case's language; either language counts on code-switching cases. A provider that returns no labels is "not comparable".
- **Confident-wrong rate**: % of turns labelled wrong with confidence ≥ 0.6 (Pingo's native-language cutoff). "Not comparable" for a provider with no confidence.
