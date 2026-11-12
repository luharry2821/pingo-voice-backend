# Pingo STT test set

68 short learner-style clips for scoring streaming STT. Synthetic speech (edge-tts neural voices), 16 kHz mono PCM16 WAV in `audio/`. No Pingo production data.

| Category | Cases | Scored with |
|---|---|---|
| english | 10 (US, UK, AU, CA, IN voices) | WER, FWER |
| german | 6 (DE, AT, CH voices) | WER |
| japanese / chinese / korean | 5 / 4 / 4 | CER |
| other (es 3, fr 2, it 1) | 6 | WER |
| codeswitch_en_hi | 5 (Hindi in Devanagari, English in Latin) | code-switching WER |
| codeswitch_en_es | 5 (multilingual voice) | code-switching WER |
| formatting (time, money, numbers, date) | 4 | FWER vs WER |
| two_speaker (learner + tutor, 2 voices, 0.9 s gap) | 4 | cpWER |
| planted_mistake (es 4, de 3, fr 2, it 1, en 2, ja 1, en-hi 1, en-es 1) | 15 | mistake-preservation rate |

`cases.jsonl`: id, category, languages, reference (exactly as spoken), speakers, audio, notes; planted cases add intended_correct and planted_mistake.

## Run
```
.venv/bin/pip install jiwer whisper-normalizer edge-tts
.venv/bin/python test_cases/make_cases.py        # rebuild audio (optional)
.venv/bin/python test_cases/run_render.py         # live Pingo /ws -> raw_outputs.jsonl
.venv/bin/python test_cases/score.py              # -> results.json + RESULTS.md
```
Any other STT: write `raw_outputs.jsonl` lines `{"id":..., "hypothesis":..., "turns":[{"text":...}]}` and run `score.py`.

## Metrics (AssemblyAI conventions)
- **WER** = (S+D+I)/N after Whisper English normalizer (English) or Whisper basic normalizer (other languages). Pooled over cases.
- **Semantic WER**: same alignment; one batched LLM-judge call (AssemblyAI LLM Gateway, gemma-4-31b, temp 0) counts only meaning-changing errors.
- **FWER** (AssemblyAI "raw" WER): no normalization, case and punctuation stay on the words.
- **cpWER**: Pingo streaming has no diarization, so each end-of-turn segment is a speaker segment; segments are assigned to reference speakers by the permutation with the fewest errors.
- **CER** (ja/zh/ko): basic-normalized, spaces removed, per character.
- **Mistake-preservation rate**: % of planted learner mistakes that appear verbatim (after normalization) in the transcript, i.e. not silently corrected. Higher is better.
- **Sentence error rate**: % of cases not an exact match after normalization.
- **Code-switching WER**: WER on the EN-HI and EN-ES cases. Script is not transliterated, so Hindi words written in a different script count as errors in WER (the semantic judge treats them as harmless).

Runs: 2 parallel sessions max (more came back empty), 15 clips per session, 4 s silence after each clip; all end-of-turn transcripts during a clip and its silence are joined as the hypothesis.
