"""Score raw_outputs.jsonl against cases.jsonl -> results.json + RESULTS.md.

Usage (repo root): .venv/bin/python test_cases/score.py [raw_outputs.jsonl] [--no-judge]
Needs: pip install jiwer whisper-normalizer requests. Semantic WER uses ASSEMBLYAI_API_KEY (LLM Gateway).

This is the end of the eval pipeline: make_cases.py builds the clips and
ground truth, run_render.py plays them through the live backend (mic audio ->
STT -> LLM tutor -> TTS) and saves what STT wrote, and this script compares
the two. KPIs it reports (error rates: lower is better):

  WER            word error rate, (S+D+I)/N after normalisation, where S/D/I
                 are substituted/deleted/inserted words and N reference words
  Semantic WER   only the errors an LLM judge says change the meaning
  FWER           "formatted" WER on the raw text (case and punctuation kept)
  match error rate (mer_rate, "MER (jiwer.mer)" in RESULTS.md)
                 (S+D+I)/(S+D+I+H), H = correct words; capped at 100%
  cpWER          WER for two-speaker clips, best assignment of turns to speakers
  CER            character error rate, for Japanese/Chinese/Korean
  Sentence error rate   share of cases with at least one error
  Code-switching WER    WER on English-Hindi and English-Spanish mixed clips
  Missed entity rate    share of names/places/numbers/dates not found in the
                 transcript (this is the "MER" shown on the Lovable evals page)
  Mistake-preservation rate   share of planted learner mistakes kept
                 verbatim (higher is better: STT must not silently fix them)
  Language-ID accuracy  share of final turns whose language_code matches
                 the case's expected language (higher is better). A
                 code-switching case lists two languages; either code
                 counts as correct, because a mixed utterance has no
                 single right label
  Confident-wrong rate  share of turns whose language label is wrong
                 with confidence >= 0.6 (Pingo's native-language switch
                 cutoff)

Helpers can be imported without scoring. Running the script writes
results.json and RESULTS.md: helpers first, then one row per case, then
the aggregate KPIs, then the two output files.
"""
import itertools, json, os, re, sys, unicodedata
import jiwer
from whisper_normalizer.english import EnglishTextNormalizer
# Command line: optional raw outputs path; --no-judge skips the LLM call (and Semantic
# WER).
D = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
RAW = args[0] if args else f"{D}/raw_outputs.jsonl"
JUDGE = "--no-judge" not in sys.argv
# Languages scored by characters (CER) instead of words.
CJK = {"ja", "zh", "ko"}
# Cutoff copied from server/session.py: 0.6 in _note_native before a turn
# can change the learner's own language.
LANG_ID_NATIVE_CUTOFF = 0.6
en_norm = EnglishTextNormalizer()

def basic(text):
    """Whisper-style basic normaliser: NFKC, lowercase, drop [tags]/(asides), punctuation and symbols -> spaces.

    Unlike Whisper's version it keeps combining marks, which Hindi (Devanagari)
    needs to keep words intact.
    """
    # Whisper's basic normalizer, minus its removal of combining marks (that splits Devanagari words).
    t = unicodedata.normalize("NFKC", text).lower()
    # Drop <tags>, [tags] and (parenthesised asides), as Whisper's normaliser does.
    t = re.sub(r"[<\[][^>\]]*[>\]]|\(([^)]+?)\)", "", t)
    return "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in t)

def norm(text, langs):
    """Normalise text for WER: Whisper's English normaliser for English-only cases, basic() otherwise.

    Normalisation makes "Don't" vs "do not", "21" vs "twenty-one" or a missing
    comma not count as errors in plain WER (FWER keeps them).
    """
    # English-only text: Whisper English normalizer (AssemblyAI's convention for normalized WER).
    # Everything else: Whisper-style basic normalizer (NFKC, lowercase, strip punctuation/symbols).
    t = en_norm(text) if langs == ["en"] else basic(text)
    return re.sub(r"\s+", " ", t).strip()

def chars(text):
    """Character sequence for CER: normalised text with spaces removed, one "word" per character.

    Japanese, Chinese and Korean do not separate words with spaces, so errors are
    counted per character instead.
    """
    return " ".join(re.sub(r"\s+", "", basic(text)))

def counts(ref, hyp):
    """Align reference vs hypothesis with jiwer and count errors.

    Returns S (substitutions), D (deletions), I (insertions), H (hits), N
    (reference length = S+D+H) and a readable list like "S: pero→perro", or
    None when the reference is empty. An empty hypothesis counts every word deleted.
    """
    if not ref.strip():
        return None
    o = jiwer.process_words(ref, hyp if hyp.strip() else "")
    errs = []
    # Walk the alignment and list each error for RESULTS.md and the judge.
    for chunk in o.alignments[0]:
        r = o.references[0][chunk.ref_start_idx:chunk.ref_end_idx]
        h = o.hypotheses[0][chunk.hyp_start_idx:chunk.hyp_end_idx]
        if chunk.type == "substitute": errs += [f"S: {a}→{b}" for a, b in zip(r, h)]
        elif chunk.type == "delete": errs += [f"D: {a}" for a in r]
        elif chunk.type == "insert": errs += [f"I: {b}" for b in h]
    return {"S": o.substitutions, "D": o.deletions, "I": o.insertions, "H": o.hits, "N": o.substitutions + o.deletions + o.hits, "errors": errs}

def rate(rows, key="E", n="N"):
    """Pooled error rate over rows: 100 * sum(rows[key]) / sum(rows[n]), plus case and unit counts.

    Pooled (total errors / total words), not an average of per-case rates, so
    long sentences weigh more, the standard way WER is reported.
    """
    N = sum(r[n] for r in rows)
    return {"value_pct": round(100 * sum(r[key] for r in rows) / N, 2) if N else None, "n_cases": len(rows), "n_units": N}

def mer_rate(rows):
    """Match error rate (jiwer.mer): errors / (errors + hits) over the same rows as WER.

    Unlike WER it cannot exceed 100% when STT inserts many extra words.
    """
    # jiwer.mer = (S+D+I)/(S+D+I+H) on the same alignment and clip set as WER
    den = sum(r["H"] + r["E"] for r in rows)
    return {"value_pct": round(100 * sum(r["E"] for r in rows) / den, 2) if den else None, "n_cases": len(rows), "n_units": den}

def entity_in_hyp(ent, hyp, langs):
    """True if an entity (name, place, number, date) appears in the transcript after normalisation.

    For CJK text, plain substring match with spaces removed; otherwise a
    whole-word match (padded with spaces) so "Ana" does not match "Anaheim".
    """
    if CJK & set(langs):
        return re.sub(r"\s+", "", basic(ent)) in re.sub(r"\s+", "", basic(hyp))
    ne, nh = norm(ent, langs), norm(hyp, langs)
    return bool(ne) and f" {ne} " in f" {nh} "

def cpwer(case, turns):
    """Concatenated minimum-permutation WER for a two-speaker case: (errors, reference words).

    Every way of assigning STT turns to the reference speakers is tried and the
    lowest total error count is kept, so the score is not hurt by turn order.
    No turns at all = every reference word missed.
    """
    refs = [norm(s["text"], ["en"]) for s in case["speakers"]]
    hyps = [norm(t["text"], ["en"]) for t in turns]
    best = None
    for assign in itertools.product(range(len(refs)), repeat=len(hyps)):
        e = 0
        for k, ref in enumerate(refs):
            hyp = " ".join(h for h, a in zip(hyps, assign) if a == k)
            c = counts(ref, hyp); e += c["S"] + c["D"] + c["I"]
        best = e if best is None else min(best, e)
    if not hyps:
        best = sum(len(r.split()) for r in refs)
    return best, sum(len(r.split()) for r in refs)

def judge(items):
    """One batched LLM-Gateway call: how many of each case's word errors change the meaning."""
    import requests
    # The judge prompt: what counts as a meaning-changing error vs a harmless variant.
    # Learner words silently replaced by a different real word count as meaning-
    # changing, because that hides the learner's mistake.
    prompt = ("You grade speech-to-text errors for a language-learning app. For each case you get the reference "
              "(what was said), the hypothesis (what STT wrote) and the list of word errors after normalization "
              "(S=substituted, D=deleted, I=inserted). Count how many of those errors change the meaning for the learner. "
              "Harmless: spelling or script variants (e.g. Hindi written in Roman vs Devanagari, same word), contractions, "
              "number written as digits vs words, fillers, compound-word splits. Meaning-changing: different word, missing "
              "content word, flipped polarity, wrong number. A learner's wrong word silently replaced by a different real word "
              "(pero->perro, caro->carro, tango->tengo) IS meaning-changing: the learner said something else. The count must be between 0 and the number of errors. "
              'Reply with JSON only: {"results":[{"id":"...","meaning_errors":0}]}.\n\n' + json.dumps(items, ensure_ascii=False))
    r = requests.post("https://llm-gateway.assemblyai.com/v1/chat/completions",
                      headers={"authorization": os.environ["ASSEMBLYAI_API_KEY"]}, timeout=120,
                      json={"model": "gemma-4-31b", "temperature": 0, "max_tokens": 3000,
                            "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    # Keep only the JSON object (the model may add text around it).
    text = text[text.find("{"): text.rfind("}") + 1]
    return {x["id"]: int(x["meaning_errors"]) for x in json.loads(text)["results"]}


def language_id_metrics(cases, raw, native_cutoff=LANG_ID_NATIVE_CUTOFF):
    """Language-ID accuracy and confident-wrong rates, scored per final turn.

    raw_outputs.jsonl stores only finished turns (AssemblyAI fills
    language_code and language_confidence only then). A case may have
    several turns — two-speaker clips or a pause-filler that split — and
    each turn is scored on its own. A label is correct when it matches the
    case's expected language. Code-switching cases list two languages
    (en+hi, en+es); either code counts as correct, because a mixed
    utterance has no single right label.

    language_id_accuracy is the share of scored turns that are correct
    (higher is better). confident_wrong_rate is the share whose label is
    wrong with confidence at or above native_cutoff (0.6), the threshold
    Pingo uses before changing the learner's own language — a wrong label
    this confident is the case those cutoffs cannot catch (e.g. es01
    tagged Korean at 1.0).

    Turns with no language_code are counted as wrong, but with no
    confidence they do not raise the confident-wrong rate. Cases with no
    turns contribute nothing: there is no label to score.
    """
    turns = []
    for cid, case in cases.items():
        expected = [str(lang).lower() for lang in case.get("languages") or []]
        for turn in (raw.get(cid) or {}).get("turns") or []:
            got = turn.get("language_code")
            got_norm = str(got).lower() if got else None
            conf = turn.get("language_confidence")
            try:
                conf = float(conf) if conf is not None and conf != "" else None
            except (TypeError, ValueError):
                conf = None
            # Code-switching: either expected language is accepted, because
            # AssemblyAI returns one label per turn and a mixed utterance
            # can reasonably be tagged as either side.
            correct = bool(got_norm) and got_norm in expected
            turns.append({
                "id": cid,
                "text": turn.get("text") or "",
                "expected": list(case.get("languages") or []),
                "got": got,
                "confidence": conf,
                "correct": correct,
            })

    def pack(sel):
        """Accuracy dict for a set of turns: percent correct, plus counts."""
        n = len(sel)
        n_ok = sum(1 for t in sel if t["correct"])
        return {"value_pct": round(100 * n_ok / n, 2) if n else None, "n_correct": n_ok,
                "n_turns": n, "higher_is_better": True}

    n = len(turns)
    confident_wrong = [t for t in turns if not t["correct"] and t["confidence"] is not None and t["confidence"] >= native_cutoff]
    mislabeled = [{"id": t["id"], "text": t["text"], "expected": t["expected"], "got": t["got"], "confidence": t["confidence"]}
                  for t in turns if not t["correct"]]

    # Per-language accuracy uses the case's expected language. Single-language
    # cases are grouped by that code; code-switching cases are grouped as
    # "en+hi" / "en+es" (either code still counts as correct, as above).
    grouped = {}
    for t in turns:
        langs = [str(lang).lower() for lang in t["expected"]]
        key = "+".join(langs) if langs else "unknown"
        grouped.setdefault(key, []).append(t)
    preferred = ["en", "de", "es", "fr", "it", "pt", "hi", "ja", "zh", "ko", "en+hi", "en+es"]
    keys = [k for k in preferred if k in grouped] + [k for k in grouped if k not in preferred]
    n_cases = len({t["id"] for t in turns})
    return {
        "language_id_accuracy": {**pack(turns), "n_cases": n_cases},
        "confident_wrong_rate": {"value_pct": round(100 * len(confident_wrong) / n, 2) if n else None,
                                 "n_confident_wrong": len(confident_wrong), "n_turns": n, "cutoff": native_cutoff},
        "language_id_accuracy_by_language": {k: pack(grouped[k]) for k in keys},
        "language_id_mislabeled": mislabeled,
    }

# Running the file scores raw_outputs.jsonl and writes the result files;
# importing it only loads the helpers (for tests).
if __name__ == "__main__":
    # Load ground truth and STT outputs, both keyed by case id.
    cases = {json.loads(l)["id"]: json.loads(l) for l in open(f"{D}/cases.jsonl")}
    raw = {json.loads(l)["id"]: json.loads(l) for l in open(RAW)}
    rows = []
    # One row per case: errors, pass/fail, entities and planted mistakes.
    for cid, c in cases.items():
        r = raw.get(cid, {}); hyp = r.get("hypothesis", ""); langs = c["languages"]
        cjk = bool(CJK & set(langs))
        row = {"id": cid, "category": c["category"], "languages": langs, "reference": c["reference"], "hypothesis": hyp,
               "turns": len(r.get("turns", [])), "detected_language": [t.get("language_code") for t in r.get("turns", [])]}
        # CJK cases are scored per character; everything else per word (normalised for WER,
        # raw for FWER).
        if cjk:
            k = counts(chars(c["reference"]), chars(hyp)); row["metric"] = "CER"
        else:
            k = counts(norm(c["reference"], langs), norm(hyp, langs)); row["metric"] = "WER"
            f = counts(c["reference"], hyp)  # raw: case + punctuation kept
            row.update(F_E=f["S"] + f["D"] + f["I"], F_N=f["N"])
        row.update(S=k["S"], D=k["D"], I=k["I"], H=k["H"], N=k["N"], E=k["S"] + k["D"] + k["I"], errors=k["errors"])
        # Per-case rate for the tables; a case passes only with zero errors.
        row["rate_pct"] = round(100 * row["E"] / row["N"], 2)
        row["pass"] = row["E"] == 0
        # Two-speaker clips also get cpWER.
        if c["category"] == "two_speaker":
            row["cp_E"], row["cp_N"] = cpwer(c, r.get("turns", []))
        # Hard-set flag (kept in results.json for the hard-only numbers).
        row["hard"] = bool(c.get("hard"))
        # Entities tagged in the case, and the ones not found in the transcript.
        ents = c.get("entities") or []
        row["entities"] = ents
        row["entities_missed"] = [e for e in ents if not entity_in_hyp(e, hyp, langs)]
        if c.get("planted_mistake"):
            # caught = the planted mistake appears verbatim (after the same normalization) in the transcript
            fold = (lambda t: re.sub(r"\s+", "", basic(t))) if cjk else (lambda t: " " + norm(t, langs) + " ")
            planted = c["planted_mistake"] if isinstance(c["planted_mistake"], list) else [c["planted_mistake"]]
            amb = set(c.get("audio_ambiguous_mistakes") or (planted if c.get("audio_ambiguous") else []))
            row.update(intended_correct=c["intended_correct"], planted_mistake=c["planted_mistake"],
                       # Each planted mistake is "preserved" if it appears in the transcript
                       # after the same normalisation; if STT corrected it, the learner's
                       # mistake was hidden.
                       mistakes=[{"mistake": m, "preserved": f" {fold(m).strip()} " in fold(hyp) if not cjk else fold(m) in fold(hyp),
                                  "audio_ambiguous": m in amb} for m in planted])
            row["mistake_preserved"] = all(x["preserved"] for x in row["mistakes"])
            if c.get("audio_ambiguous"): row["audio_ambiguous"] = c["audio_ambiguous"]
        rows.append(row)

    # Semantic WER: one batched LLM call over every case with errors. A failure only blanks
    # this KPI.
    word = [r for r in rows if r["metric"] == "WER"]
    semantic = {"value_pct": None, "note": "judge not run"}
    if JUDGE:
        try:
            m = judge([{"id": r["id"], "reference": r["reference"], "hypothesis": r["hypothesis"], "errors": r["errors"]} for r in word if r["E"]])
            # The judge cannot report more meaning errors than there are errors.
            for r in word: r["meaning_errors"] = min(r["E"], m.get(r["id"], r["E"])) if r["E"] else 0
            semantic = {**rate(word, "meaning_errors"), "judge": "AssemblyAI LLM Gateway gemma-4-31b, temperature 0, one batched call"}
        except Exception as exc:
            semantic = {"value_pct": None, "note": f"judge failed: {exc}"}

    # by(f): the rows matching a filter.
    by = lambda f: [r for r in rows if f(r)]

    def mpr(sel, exclude_ambiguous=False):
        """Mistake-preservation rate: share of planted learner mistakes STT kept verbatim (higher is better).

        exclude_ambiguous drops mistakes whose audio genuinely sounds like the
        correct word, so they do not count against STT.
        """
        ms = [m for r in sel if "mistakes" in r for m in r["mistakes"] if not (exclude_ambiguous and m["audio_ambiguous"])]
        return {"value_pct": round(100 * sum(m["preserved"] for m in ms) / len(ms), 2) if ms else None, "n_mistakes": len(ms),
                "n_preserved": sum(m["preserved"] for m in ms), "n_cases": len([r for r in sel if "mistakes" in r]), "higher_is_better": True}

    def ser(sel):
        """Sentence error rate: percent of cases with at least one word/character error."""
        return {"value_pct": round(100 * sum(not r["pass"] for r in sel) / len(sel), 2) if sel else None, "n_cases": len(sel)}

    def missed_entity_rate(sel):
        """Missed entity rate: percent of tagged entities not found in the transcript, with the list of misses.

        This is the KPI shown as "MER (missed entity rate)" on the Lovable evals page.
        """
        pairs = [(r, e) for r in sel for e in r["entities"]]
        missed = [(r, e) for r in sel for e in r["entities_missed"]]
        n = len(pairs)
        return {"value_pct": round(100 * len(missed) / n, 2) if n else None, "n_missed": len(missed), "n_entities": n,
                "n_cases": len([r for r in sel if r["entities"]]),
                "missed": [{"id": r["id"], "entity": e, "hypothesis": r["hypothesis"], "reference": r["reference"]} for r, e in missed]}

    # Aggregate KPIs. Per-language WER uses only the plain single-language categories
    # (english, german, other) and per-language CER skips planted-mistake cases, so each
    # language's score is not mixed with code-switching, entity or mistake clips.
    hard = by(lambda r: r["hard"]); hard_word = [r for r in hard if r["metric"] == "WER"]
    entity_cats = sorted({r["category"] for r in rows if r["entities"]})
    res = {
      "n_cases": len(rows),
      "wer_overall": rate(word),
      "semantic_wer": semantic,
      "fwer": rate(word, "F_E", "F_N"),
      "mer_overall": mer_rate(word),
      "cpwer": rate(by(lambda r: "cp_E" in r), "cp_E", "cp_N"),
      "wer_by_language": {l: rate(by(lambda r, l=l: r["languages"] == [l] and r["category"] in ("english", "german", "other")))
                          for l in ("en", "de", "es", "fr", "it")},
      "mer_by_language": {l: mer_rate(by(lambda r, l=l: r["languages"] == [l] and r["category"] in ("english", "german", "other")))
                          for l in ("en", "de", "es", "fr", "it")},
      "cer_by_language": {l: rate(by(lambda r, l=l: r["languages"] == [l] and r["category"] != "planted_mistake")) for l in ("ja", "zh", "ko")},
      "sentence_error_rate": {"value_pct": round(100 * sum(not r["pass"] for r in rows) / len(rows), 2), "n_cases": len(rows)},
      "codeswitch_wer": {"en_hi": rate(by(lambda r: r["category"] == "codeswitch_en_hi")),
                         "en_es": rate(by(lambda r: r["category"] == "codeswitch_en_es"))},
      "mistake_preservation_rate": mpr(rows),
      "mistake_preservation_rate_excl_ambiguous_audio": mpr(rows, True),
      "hard_only": {"n_cases": len(hard), "wer": rate(hard_word), "mer": mer_rate(hard_word), "fwer": rate(hard_word, "F_E", "F_N"),
                    "semantic_wer": rate(hard_word, "meaning_errors") if JUDGE and "value_pct" in semantic and semantic["value_pct"] is not None else None,
                    "cer": rate([r for r in hard if r["metric"] == "CER"]), "sentence_error_rate": ser(hard),
                    "mistake_preservation_rate": mpr(hard), "mistake_preservation_rate_excl_ambiguous_audio": mpr(hard, True)},
      "formatting_fwer": rate(by(lambda r: r["category"] == "formatting"), "F_E", "F_N"),
      "formatting_wer": rate(by(lambda r: r["category"] == "formatting")),
      "missed_entity_rate": missed_entity_rate(rows),
      "missed_entity_rate_entity_set": missed_entity_rate(by(lambda r: r["category"] == "entity")),
      "missed_entity_rate_by_category": {cat: missed_entity_rate(by(lambda r, cat=cat: r["category"] == cat)) for cat in entity_cats},
    }
    # Language-ID KPIs are per final turn, not per case. Code-switching
    # labels are scored against either expected language (see language_id_metrics).
    res.update(language_id_metrics(cases, raw))
    res["cases"] = rows
    # Machine-readable results (also used for the Notion/Lovable tables).
    json.dump(res, open(f"{D}/results.json", "w"), ensure_ascii=False, indent=1)
    # Human-readable RESULTS.md: KPI table, hard-set table, missed entities, planted
    # mistakes and failed cases.
    p = lambda x: "n/a" if x.get("value_pct") is None else f"{x['value_pct']}%"
    L = ["# STT results (live Pingo on Render)", "", f"{len(rows)} cases. Error rates: lower is better, except language-ID accuracy (higher is better). n = cases / reference units (words, or characters for CER); language-ID metrics are per final turn.", "",
         "| KPI | Value | n |", "|---|---|---|",
         f"| WER (normalized, all word-based cases) | {p(res['wer_overall'])} | {res['wer_overall']['n_cases']} / {res['wer_overall']['n_units']} |",
         f"| Semantic WER (LLM judge) | {p(semantic)} | {semantic.get('n_cases','-')} |",
         f"| FWER (raw, no normalization) | {p(res['fwer'])} | {res['fwer']['n_cases']} / {res['fwer']['n_units']} |",
         f"| MER (match error rate, jiwer.mer) | {p(res['mer_overall'])} | {res['mer_overall']['n_cases']} / {res['mer_overall']['n_units']} |",
         f"| cpWER (2-speaker) | {p(res['cpwer'])} | {res['cpwer']['n_cases']} / {res['cpwer']['n_units']} |",
         f"| Mistake-preservation rate (higher is better) | {p(res['mistake_preservation_rate'])} | {res['mistake_preservation_rate']['n_mistakes']} mistakes |",
         f"| Mistake-preservation, ambiguous audio excluded | {p(res['mistake_preservation_rate_excl_ambiguous_audio'])} | {res['mistake_preservation_rate_excl_ambiguous_audio']['n_mistakes']} mistakes |",
         f"| Sentence error rate | {p(res['sentence_error_rate'])} | {len(rows)} |",
         f"| Missed entity rate | {p(res['missed_entity_rate'])} ({res['missed_entity_rate']['n_missed']}/{res['missed_entity_rate']['n_entities']}) | {res['missed_entity_rate']['n_cases']} cases |",
         f"| Missed entity rate, entity set only | {p(res['missed_entity_rate_entity_set'])} ({res['missed_entity_rate_entity_set']['n_missed']}/{res['missed_entity_rate_entity_set']['n_entities']}) | {res['missed_entity_rate_entity_set']['n_cases']} cases |",
         f"| Language-ID accuracy (higher is better) | {p(res['language_id_accuracy'])} | {res['language_id_accuracy']['n_correct']} / {res['language_id_accuracy']['n_turns']} turns |",
         f"| Confident-wrong rate (wrong label, confidence ≥ {res['confident_wrong_rate']['cutoff']}) | {p(res['confident_wrong_rate'])} | {res['confident_wrong_rate']['n_confident_wrong']} / {res['confident_wrong_rate']['n_turns']} turns |"]
    for l, v in res["language_id_accuracy_by_language"].items():
        L.append(f"| Language-ID accuracy {l} | {p(v)} | {v['n_correct']} / {v['n_turns']} turns |")
    for l, v in res["wer_by_language"].items(): L.append(f"| WER {l} | {p(v)} | {v['n_cases']} / {v['n_units']} |")
    for l, v in res["mer_by_language"].items(): L.append(f"| MER {l} | {p(v)} | {v['n_cases']} / {v['n_units']} |")
    for l, v in res["cer_by_language"].items(): L.append(f"| CER {l} | {p(v)} | {v['n_cases']} / {v['n_units']} |")
    for l, v in res["codeswitch_wer"].items(): L.append(f"| Code-switching WER {l} | {p(v)} | {v['n_cases']} / {v['n_units']} |")
    L.append(f"| Formatting cases: FWER / WER | {p(res['formatting_fwer'])} / {p(res['formatting_wer'])} | {res['formatting_fwer']['n_cases']} |")
    for cat, v in res["missed_entity_rate_by_category"].items():
        L.append(f"| Missed entity rate ({cat}) | {p(v)} ({v['n_missed']}/{v['n_entities']}) | {v['n_cases']} cases |")
    h = res["hard_only"]
    L += ["", f"## Hard set only ({h['n_cases']} cases)", "", "| KPI | Value | n |", "|---|---|---|",
          f"| WER | {p(h['wer'])} | {h['wer']['n_cases']} / {h['wer']['n_units']} |",
          f"| MER (jiwer.mer) | {p(h['mer'])} | {h['mer']['n_cases']} / {h['mer']['n_units']} |",
          f"| Semantic WER | {p(h['semantic_wer'] or {})} | {h['wer']['n_cases']} |",
          f"| FWER | {p(h['fwer'])} | {h['fwer']['n_cases']} / {h['fwer']['n_units']} |",
          f"| CER | {p(h['cer'])} | {h['cer']['n_cases']} / {h['cer']['n_units']} |",
          f"| Sentence error rate | {p(h['sentence_error_rate'])} | {h['n_cases']} |",
          f"| Mistake preservation | {p(h['mistake_preservation_rate'])} | {h['mistake_preservation_rate']['n_mistakes']} |",
          f"| Mistake preservation, ambiguous excluded | {p(h['mistake_preservation_rate_excl_ambiguous_audio'])} | {h['mistake_preservation_rate_excl_ambiguous_audio']['n_mistakes']} |"]
    mer = res["missed_entity_rate"]
    L += ["", "## Missed entities", "",
          f"{mer['n_missed']} of {mer['n_entities']} entities missed after the same normalization as WER/CER.",
          "", "| id | entity (as spoken) | Pingo heard |", "|---|---|---|"]
    L += [f"| {m['id']} | {m['entity']} | {m['hypothesis']} |" for m in mer["missed"]] or ["| — | none | — |"]
    miss = res["language_id_mislabeled"]
    L += ["", "## Mislabeled language ID", "",
          "Final turns whose language_code did not match the case's expected language. "
          "Code-switching cases count either expected language as correct.",
          "", f"{len(miss)} of {res['language_id_accuracy']['n_turns']} turns.",
          "", "| id | text | expected | got | confidence |", "|---|---|---|---|---|"]
    # A missing label or confidence is shown as an em dash; expected languages are joined with a comma.
    L += [f"| {m['id']} | {str(m['text']).replace('|', '/')} | {', '.join(m['expected']) or '—'} | {m['got'] or '—'} | {m['confidence'] if m['confidence'] is not None else '—'} |" for m in miss] or ["| — | none | — | — | — |"]
    L += ["", "## Planted mistakes", "", "| id | said (with mistake) | intended | Pingo heard | caught? |", "|---|---|---|---|---|"]
    L += [f"| {r['id']} | {r['reference']} | {r['intended_correct']} | {r['hypothesis']} | {', '.join(('yes' if m['preserved'] else 'no') + (' (ambiguous audio)' if m['audio_ambiguous'] else '') + (f' [{m["mistake"]}]' if len(r['mistakes']) > 1 else '') for m in r['mistakes'])} |" for r in rows if "mistakes" in r]
    L += ["", "## Failed cases", "", "| id | reference | Pingo heard | errors |", "|---|---|---|---|"]
    L += [f"| {r['id']} | {r['reference']} | {r['hypothesis']} | {'; '.join(r['errors'])} |" for r in rows if not r["pass"]]
    open(f"{D}/RESULTS.md", "w").write("\n".join(L) + "\n")
    print("\n".join(L[:22]))
