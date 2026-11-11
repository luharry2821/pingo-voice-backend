"""Practice state and the deterministic check of a learner's attempt.

The tutor keeps one practice phrase per session: the last phrase it taught,
corrected or broke into syllables. When the learner then says something short
that sounds like that phrase, this module scores the attempt before the LLM
sees it, so "ola" for "Hola" is judged against the phrase, not as a new
request. Two signals:

- spelling: both strings are folded (case, accents, punctuation, silent h,
  a few letters speech-to-text swaps) and compared with difflib.
- confidence: AssemblyAI's per-word confidence on the final turn. A match that
  the model heard with low confidence is downgraded from correct to close.
"""

import difflib
import re
import unicodedata

CORRECT_AT = 0.85
CLOSE_AT = 0.6
LOW_CONFIDENCE = 0.5
# Longer turns are questions or new sentences, not attempts at a short phrase.
EXTRA_WORDS = 3

VERDICTS = ("correct", "close", "try_again")


def fold(text):
    """Lowercase, strip accents and punctuation, collapse spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("ß", "ss")
    text = re.sub(r"[^\w\s']", " ", text)
    text = text.replace("'", "")
    return re.sub(r"\s+", " ", text).strip()


def sound_key(text, language="es"):
    """A rough phonetic key so spelling variants STT produces still match."""
    key = fold(text)
    if language in ("es", "pt", "fr", "it"):
        # Silent h: hola / ola, hablar / ablar. Keep ch, sh, lh, nh.
        key = re.sub(r"(?<![csln])h", "", key)
    if language == "es":
        key = key.replace("v", "b").replace("z", "s").replace("ll", "y")
        key = re.sub(r"c(?=[ei])", "s", key)
        key = key.replace("qu", "k")
    key = re.sub(r"(.)\1+", r"\1", key)
    return key


def _ratio(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_score(attempt, target, language="es"):
    """0..1. The best window of the attempt against the target phrase.

    "uh hola" or "hola hola" still match "Hola": every run of words the length
    of the target (plus or minus one) is tried and the best one wins.
    """
    target_key = sound_key(target, language)
    attempt_key = sound_key(attempt, language)
    if not target_key or not attempt_key:
        return 0.0
    best = _ratio(attempt_key, target_key)
    words = attempt_key.split()
    size = len(target_key.split())
    for width in {max(1, size - 1), size, size + 1}:
        for start in range(0, max(1, len(words) - width + 1)):
            window = " ".join(words[start : start + width])
            best = max(best, _ratio(window, target_key))
    return round(best, 3)


def looks_like_attempt(attempt, target, language="es"):
    """Short enough and close enough to be a try at the practice phrase."""
    if not target or not attempt:
        return False
    attempt_words = fold(attempt).split()
    target_words = fold(target).split()
    if len(attempt_words) > len(target_words) + EXTRA_WORDS:
        return False
    return match_score(attempt, target, language) >= CLOSE_AT


def low_confidence_words(words, threshold=LOW_CONFIDENCE):
    out = []
    for word in words or []:
        confidence = word.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < threshold:
            out.append({"text": word.get("text", ""), "confidence": round(float(confidence), 3)})
    return out


def check_attempt(attempt, target, language="es", words=None):
    """The verdict for one attempt. Returns a dict the prompt and UI both use."""
    score = match_score(attempt, target, language)
    low = low_confidence_words(words)
    confidences = [w.get("confidence") for w in words or [] if isinstance(w.get("confidence"), (int, float))]
    mean = round(sum(confidences) / len(confidences), 3) if confidences else None
    if score >= CORRECT_AT:
        verdict = "correct"
        reason = "matches the phrase"
        if low:
            verdict = "close"
            reason = "the words matched, but the speech model was unsure of " + ", ".join(
                w["text"] for w in low
            )
    elif score >= CLOSE_AT:
        verdict = "close"
        reason = "some sounds differ from the phrase"
    else:
        verdict = "try_again"
        reason = "it did not sound like the phrase"
    return {
        "target": target,
        "heard": attempt,
        "score": score,
        "verdict": verdict,
        "reason": reason,
        "low_confidence_words": low,
        "mean_confidence": mean,
    }


def slow_text(target_phrase, syllables):
    """Text for a slow read: syllables with pauses, then nothing else.

    TTS has no speed control (the HTTP API only accepts speed=1 and the SDK's
    TTSConfig has no speed field), so slowness comes from splitting the phrase
    into syllables with ellipses between them.
    """
    parts = [p.strip() for p in re.split(r"\s*[-·•|]\s*", syllables or "") if p.strip()]
    if len(parts) < 2:
        words = (target_phrase or "").split()
        if len(words) < 2:
            return ""
        parts = words
    return "... ".join(parts) + "."
