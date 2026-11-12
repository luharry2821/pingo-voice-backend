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
              "content word, flipped polarity, wrong number. The count must be between 0 and the number of errors. "
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
    if c.get("planted_mistake"):
        # caught = the planted mistake appears verbatim (after the same normalization) in the transcript
        fold = (lambda t: re.sub(r"\s+", "", basic(t))) if cjk else (lambda t: " " + norm(t, langs) + " ")
        row.update(intended_correct=c["intended_correct"], planted_mistake=c["planted_mistake"],
                   mistake_preserved=fold(c["planted_mistake"]).strip() in fold(hyp))
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
res = {
  "n_cases": len(rows),
  "wer_overall": rate(word),
  "semantic_wer": semantic,
  "fwer": rate(word, "F_E", "F_N"),
  "mer_overall": {"value_pct": round(100 * sum(r["E"] for r in word) / sum(r["H"] + r["E"] for r in word), 2), "n_cases": len(word)},
  "cpwer": rate(by(lambda r: "cp_E" in r), "cp_E", "cp_N"),
  "wer_by_language": {l: rate(by(lambda r, l=l: r["languages"] == [l] and r["category"] in ("english", "german", "other")))
                      for l in ("en", "de", "es", "fr", "it")},
  "cer_by_language": {l: rate(by(lambda r, l=l: r["languages"] == [l] and r["category"] != "planted_mistake")) for l in ("ja", "zh", "ko")},
  "sentence_error_rate": {"value_pct": round(100 * sum(not r["pass"] for r in rows) / len(rows), 2), "n_cases": len(rows)},
  "codeswitch_wer": {"en_hi": rate(by(lambda r: r["category"] == "codeswitch_en_hi")),
                     "en_es": rate(by(lambda r: r["category"] == "codeswitch_en_es"))},
  "mistake_preservation_rate": {"value_pct": round(100 * sum(r["mistake_preserved"] for r in by(lambda r: "planted_mistake" in r)) / max(1, len(by(lambda r: "planted_mistake" in r))), 2),
                                "n_cases": len(by(lambda r: "planted_mistake" in r)), "higher_is_better": True},
  "formatting_fwer": rate(by(lambda r: r["category"] == "formatting"), "F_E", "F_N"),
  "formatting_wer": rate(by(lambda r: r["category"] == "formatting")),
  "cases": rows,
}
json.dump(res, open(f"{D}/results.json", "w"), ensure_ascii=False, indent=1)
p = lambda x: "n/a" if x.get("value_pct") is None else f"{x['value_pct']}%"
L = ["# STT results (live Pingo on Render)", "", f"{len(rows)} cases. Error rates: lower is better. n = cases / reference units (words, or characters for CER).", "",
     "| KPI | Value | n |", "|---|---|---|",
     f"| WER (normalized, all word-based cases) | {p(res['wer_overall'])} | {res['wer_overall']['n_cases']} / {res['wer_overall']['n_units']} |",
     f"| Semantic WER (LLM judge) | {p(semantic)} | {semantic.get('n_cases','-')} |",
     f"| FWER (raw, no normalization) | {p(res['fwer'])} | {res['fwer']['n_cases']} / {res['fwer']['n_units']} |",
     f"| cpWER (2-speaker) | {p(res['cpwer'])} | {res['cpwer']['n_cases']} / {res['cpwer']['n_units']} |",
     f"| Mistake-preservation rate (higher is better) | {p(res['mistake_preservation_rate'])} | {res['mistake_preservation_rate']['n_cases']} |",
     f"| Sentence error rate | {p(res['sentence_error_rate'])} | {len(rows)} |"]
for l, v in res["wer_by_language"].items(): L.append(f"| WER {l} | {p(v)} | {v['n_cases']} / {v['n_units']} |")
for l, v in res["cer_by_language"].items(): L.append(f"| CER {l} | {p(v)} | {v['n_cases']} / {v['n_units']} |")
for l, v in res["codeswitch_wer"].items(): L.append(f"| Code-switching WER {l} | {p(v)} | {v['n_cases']} / {v['n_units']} |")
L.append(f"| Formatting cases: FWER / WER | {p(res['formatting_fwer'])} / {p(res['formatting_wer'])} | {res['formatting_fwer']['n_cases']} |")
L += ["", "## Planted mistakes", "", "| id | said (with mistake) | intended | Pingo heard | caught? |", "|---|---|---|---|---|"]
L += [f"| {r['id']} | {r['reference']} | {r['intended_correct']} | {r['hypothesis']} | {'yes' if r['mistake_preserved'] else 'no'} |" for r in rows if "planted_mistake" in r]
L += ["", "## Failed cases", "", "| id | reference | Pingo heard | errors |", "|---|---|---|---|"]
L += [f"| {r['id']} | {r['reference']} | {r['hypothesis']} | {'; '.join(r['errors'])} |" for r in rows if not r["pass"]]
open(f"{D}/RESULTS.md", "w").write("\n".join(L) + "\n")
print("\n".join(L[:22]))
