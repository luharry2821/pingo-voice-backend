"""Score raw_outputs.jsonl against cases.jsonl -> results.json + RESULTS.md.
Usage (repo root): .venv/bin/python test_cases/score.py [raw_outputs.jsonl] [--no-judge]
Needs: pip install jiwer whisper-normalizer requests. Semantic WER uses ASSEMBLYAI_API_KEY (LLM Gateway)."""
import itertools, json, os, re, sys, unicodedata
import jiwer
from whisper_normalizer.english import EnglishTextNormalizer
D = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
RAW = args[0] if args else f"{D}/raw_outputs.jsonl"
JUDGE = "--no-judge" not in sys.argv
CJK = {"ja", "zh", "ko"}
en_norm = EnglishTextNormalizer()

def basic(text):
    # Whisper's basic normalizer, minus its removal of combining marks (that splits Devanagari words).
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"[<\[][^>\]]*[>\]]|\(([^)]+?)\)", "", t)
    return "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in t)

def norm(text, langs):
    # English-only text: Whisper English normalizer (AssemblyAI's convention for normalized WER).
    # Everything else: Whisper-style basic normalizer (NFKC, lowercase, strip punctuation/symbols).
    t = en_norm(text) if langs == ["en"] else basic(text)
    return re.sub(r"\s+", " ", t).strip()

def chars(text):
    return " ".join(re.sub(r"\s+", "", basic(text)))

def counts(ref, hyp):
    if not ref.strip():
        return None
    o = jiwer.process_words(ref, hyp if hyp.strip() else "")
    errs = []
    for chunk in o.alignments[0]:
        r = o.references[0][chunk.ref_start_idx:chunk.ref_end_idx]
        h = o.hypotheses[0][chunk.hyp_start_idx:chunk.hyp_end_idx]
        if chunk.type == "substitute": errs += [f"S: {a}→{b}" for a, b in zip(r, h)]
        elif chunk.type == "delete": errs += [f"D: {a}" for a in r]
        elif chunk.type == "insert": errs += [f"I: {b}" for b in h]
    return {"S": o.substitutions, "D": o.deletions, "I": o.insertions, "H": o.hits, "N": o.substitutions + o.deletions + o.hits, "errors": errs}

def rate(rows, key="E", n="N"):
    N = sum(r[n] for r in rows)
    return {"value_pct": round(100 * sum(r[key] for r in rows) / N, 2) if N else None, "n_cases": len(rows), "n_units": N}

def mer_rate(rows):
    # jiwer.mer = (S+D+I)/(S+D+I+H) on the same alignment and clip set as WER
    den = sum(r["H"] + r["E"] for r in rows)
    return {"value_pct": round(100 * sum(r["E"] for r in rows) / den, 2) if den else None, "n_cases": len(rows), "n_units": den}

def entity_in_hyp(ent, hyp, langs):
    if CJK & set(langs):
        return re.sub(r"\s+", "", basic(ent)) in re.sub(r"\s+", "", basic(hyp))
    ne, nh = norm(ent, langs), norm(hyp, langs)
    return bool(ne) and f" {ne} " in f" {nh} "

def cpwer(case, turns):
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
    text = text[text.find("{"): text.rfind("}") + 1]
    return {x["id"]: int(x["meaning_errors"]) for x in json.loads(text)["results"]}

cases = {json.loads(l)["id"]: json.loads(l) for l in open(f"{D}/cases.jsonl")}
raw = {json.loads(l)["id"]: json.loads(l) for l in open(RAW)}
rows = []
for cid, c in cases.items():
    r = raw.get(cid, {}); hyp = r.get("hypothesis", ""); langs = c["languages"]
    cjk = bool(CJK & set(langs))
    row = {"id": cid, "category": c["category"], "languages": langs, "reference": c["reference"], "hypothesis": hyp,
           "turns": len(r.get("turns", [])), "detected_language": [t.get("language_code") for t in r.get("turns", [])]}
    if cjk:
        k = counts(chars(c["reference"]), chars(hyp)); row["metric"] = "CER"
    else:
        k = counts(norm(c["reference"], langs), norm(hyp, langs)); row["metric"] = "WER"
        f = counts(c["reference"], hyp)  # raw: case + punctuation kept
        row.update(F_E=f["S"] + f["D"] + f["I"], F_N=f["N"])
    row.update(S=k["S"], D=k["D"], I=k["I"], H=k["H"], N=k["N"], E=k["S"] + k["D"] + k["I"], errors=k["errors"])
    row["rate_pct"] = round(100 * row["E"] / row["N"], 2)
    row["pass"] = row["E"] == 0
    if c["category"] == "two_speaker":
        row["cp_E"], row["cp_N"] = cpwer(c, r.get("turns", []))
    row["hard"] = bool(c.get("hard"))
    ents = c.get("entities") or []
    row["entities"] = ents
    row["entities_missed"] = [e for e in ents if not entity_in_hyp(e, hyp, langs)]
    if c.get("planted_mistake"):
        # caught = the planted mistake appears verbatim (after the same normalization) in the transcript
        fold = (lambda t: re.sub(r"\s+", "", basic(t))) if cjk else (lambda t: " " + norm(t, langs) + " ")
        planted = c["planted_mistake"] if isinstance(c["planted_mistake"], list) else [c["planted_mistake"]]
        amb = set(c.get("audio_ambiguous_mistakes") or (planted if c.get("audio_ambiguous") else []))
        row.update(intended_correct=c["intended_correct"], planted_mistake=c["planted_mistake"],
                   mistakes=[{"mistake": m, "preserved": f" {fold(m).strip()} " in fold(hyp) if not cjk else fold(m) in fold(hyp),
                              "audio_ambiguous": m in amb} for m in planted])
        row["mistake_preserved"] = all(x["preserved"] for x in row["mistakes"])
        if c.get("audio_ambiguous"): row["audio_ambiguous"] = c["audio_ambiguous"]
    rows.append(row)

word = [r for r in rows if r["metric"] == "WER"]
semantic = {"value_pct": None, "note": "judge not run"}
if JUDGE:
    try:
        m = judge([{"id": r["id"], "reference": r["reference"], "hypothesis": r["hypothesis"], "errors": r["errors"]} for r in word if r["E"]])
        for r in word: r["meaning_errors"] = min(r["E"], m.get(r["id"], r["E"])) if r["E"] else 0
        semantic = {**rate(word, "meaning_errors"), "judge": "AssemblyAI LLM Gateway gemma-4-31b, temperature 0, one batched call"}
    except Exception as exc:
        semantic = {"value_pct": None, "note": f"judge failed: {exc}"}

by = lambda f: [r for r in rows if f(r)]

def mpr(sel, exclude_ambiguous=False):
    ms = [m for r in sel if "mistakes" in r for m in r["mistakes"] if not (exclude_ambiguous and m["audio_ambiguous"])]
    return {"value_pct": round(100 * sum(m["preserved"] for m in ms) / len(ms), 2) if ms else None, "n_mistakes": len(ms),
            "n_preserved": sum(m["preserved"] for m in ms), "n_cases": len([r for r in sel if "mistakes" in r]), "higher_is_better": True}

def ser(sel):
    return {"value_pct": round(100 * sum(not r["pass"] for r in sel) / len(sel), 2) if sel else None, "n_cases": len(sel)}

def missed_entity_rate(sel):
    pairs = [(r, e) for r in sel for e in r["entities"]]
    missed = [(r, e) for r in sel for e in r["entities_missed"]]
    n = len(pairs)
    return {"value_pct": round(100 * len(missed) / n, 2) if n else None, "n_missed": len(missed), "n_entities": n,
            "n_cases": len([r for r in sel if r["entities"]]),
            "missed": [{"id": r["id"], "entity": e, "hypothesis": r["hypothesis"], "reference": r["reference"]} for r, e in missed]}

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
  "cases": rows,
}
json.dump(res, open(f"{D}/results.json", "w"), ensure_ascii=False, indent=1)
p = lambda x: "n/a" if x.get("value_pct") is None else f"{x['value_pct']}%"
L = ["# STT results (live Pingo on Render)", "", f"{len(rows)} cases. Error rates: lower is better. n = cases / reference units (words, or characters for CER).", "",
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
     f"| Missed entity rate, entity set only | {p(res['missed_entity_rate_entity_set'])} ({res['missed_entity_rate_entity_set']['n_missed']}/{res['missed_entity_rate_entity_set']['n_entities']}) | {res['missed_entity_rate_entity_set']['n_cases']} cases |"]
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
L += ["", "## Planted mistakes", "", "| id | said (with mistake) | intended | Pingo heard | caught? |", "|---|---|---|---|---|"]
L += [f"| {r['id']} | {r['reference']} | {r['intended_correct']} | {r['hypothesis']} | {', '.join(('yes' if m['preserved'] else 'no') + (' (ambiguous audio)' if m['audio_ambiguous'] else '') + (f' [{m["mistake"]}]' if len(r['mistakes']) > 1 else '') for m in r['mistakes'])} |" for r in rows if "mistakes" in r]
L += ["", "## Failed cases", "", "| id | reference | Pingo heard | errors |", "|---|---|---|---|"]
L += [f"| {r['id']} | {r['reference']} | {r['hypothesis']} | {'; '.join(r['errors'])} |" for r in rows if not r["pass"]]
open(f"{D}/RESULTS.md", "w").write("\n".join(L) + "\n")
print("\n".join(L[:22]))
