"""The LLM tutor: turns one learner transcript into one tutor turn (a "lesson").

Pipeline position: mic audio -> streaming STT -> **LLM tutor (this file)** ->
TTS -> browser. server/session.py calls GatewayLLM.complete() with the final
transcript, the detected language and the practice state; it gets back a
Lesson with the phrase to teach, a respelling, syllables, a tip, a verdict on
practice attempts and what to say next.

The request is the same one the AssemblyAI LLM Gateway playground makes:

    response = requests.post(
        "https://llm-gateway.assemblyai.com/v1/chat/completions",
        headers={"authorization": API_KEY},
        json={
            "model": "qwen3.5-4b-32k-fast",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        },
    )
    text = response.json()["choices"][0]["message"]["content"]

The POST runs in a thread so the asyncio session is not blocked. The default
model is gemma-4-31b, which gets response_format (strict JSON schema).
qwen3.5-4b-32k-fast is the same request without response_format.

Most of this file is defensive parsing: small models sometimes return broken
JSON, control characters, copied template slots or a stray word at the end.
Each cleanup function names the live failure it was written for.
"""

import asyncio
import json
import logging
import re
import time

import requests

logger = logging.getLogger("pingo.llm")

# Gateway endpoint and model names. Only gemma gets the strict JSON schema
# (response_format); other models are asked for JSON by the prompt alone.
LLM_URL = "https://llm-gateway.assemblyai.com/v1/chat/completions"
DEFAULT_MODEL = "gemma-4-31b"
GEMMA_MODEL = "gemma-4-31b"

# What kind of turn the model says this is, and its judgement on practice attempts.
# Anything else from the model is mapped back to a default by _choice().
INTENTS = ("teach_phrase", "pronunciation", "practice_attempt", "correction", "chat")
VERDICTS = ("correct", "close", "try_again", "none")

# One tutor turn. Every field is required so strict json_schema decoding works;
# fields that do not apply are empty strings, "none" or [].
LESSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "pingo_tutor_turn",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "intent": {"type": "string", "enum": list(INTENTS)},
                "reply": {"type": "string"},
                "target_phrase": {"type": "string"},
                "pronunciation": {"type": "string"},
                "syllables": {"type": "string"},
                "tip": {"type": "string"},
                "verdict": {"type": "string", "enum": list(VERDICTS)},
                "verdict_reason": {"type": "string"},
                "next_prompt": {"type": "string"},
                "detected_errors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "heard": {"type": "string"},
                            "issue": {"type": "string"},
                        },
                        "required": ["heard", "issue"],
                    },
                },
            },
            "required": [
                "intent",
                "reply",
                "target_phrase",
                "pronunciation",
                "syllables",
                "tip",
                "verdict",
                "verdict_reason",
                "next_prompt",
                "detected_errors",
            ],
        },
    },
}

def _auto_schema():
    """LESSON_SCHEMA plus `target_language`: the language the learner asked for this turn."""
    # Deep copy via JSON so the base schema is not modified.
    schema = json.loads(json.dumps(LESSON_SCHEMA))
    body = schema["json_schema"]["schema"]
    schema["json_schema"]["name"] = "pingo_tutor_turn_auto"
    body["properties"]["target_language"] = {"type": "string"}
    body["required"].append("target_language")
    return schema


AUTO_LESSON_SCHEMA = _auto_schema()

# Appended to the system prompt in auto-target mode. {target} in the main
# prompt becomes "the target language"; this block says how to pick it.
AUTO_TARGET_PROMPT = """
# Auto-target mode

The learner did not pick a language. They can speak any language. Each turn, decide which
language they want to learn and return it as target_language:
- The ISO 639-1 code of the language they ask for in this turn, in whatever language they ask
  ("How do I say thanks in French?" -> "fr", "¿Cómo se dice hola en alemán?" -> "de").
- If they do not name a language, use the previous target language from the turn details.
- If they name none and there is no previous target, return "" with intent chat and an empty
  target_phrase, and ask which language they want to learn it in.
- Pingo teaches only en, es, fr, de, it and pt. If they ask for another language, still return its
  code (e.g. "ja"), with intent chat and an empty target_phrase.
- "The target language" above means this turn's target_language. In reply, name it
  ("In German, that's"), never say "the target language".
"""

# Lesson fields beyond the original schema, passed to Lesson(**turn).
TURN_FIELDS = ("intent", "reply", "syllables", "verdict", "verdict_reason", "next_prompt", "target_language")

# Control characters and the replacement character. Seen live from the model:
# "Buenos d\x7fas" (DEL where "í" belongs). The gateway bytes are valid UTF-8
# and we decode them as UTF-8, so this comes from the model's output itself.
BAD_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufffd]")


class LLMError(RuntimeError):
    """Raised when the tutor model cannot be reached, times out or returns no text.

    The session catches it and shows the message to the learner as an error.
    """
    pass


class Lesson:
    """One tutor turn. `translation` is the target-language phrase (kept under
    its old name for the eval scorer and older clients)."""

    def __init__(self, translation, pronunciation, tip, detected_errors, model, tokens_in, tokens_out, first_token_at, done_at, streamed, **turn):
        """Store one parsed tutor turn.

        The positional fields are the original lesson schema; **turn carries the
        newer tutor-turn fields (intent, reply, syllables, verdict, verdict_reason,
        next_prompt, target_language, corrupt). Missing ones get safe defaults: an
        intent of teach_phrase if there is a phrase (else chat), verdict "none".
        unparsed / raw_text / retried / salvaged are set later by the caller.
        """
        self.translation = translation
        self.pronunciation = pronunciation
        self.tip = tip
        self.detected_errors = detected_errors
        self.model = model
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.first_token_at = first_token_at
        self.done_at = done_at
        self.streamed = streamed
        self.unparsed = False
        self.raw_text = ""
        self.retried = False
        # True when the model's phrase or syllables had control characters (now stripped).
        self.corrupt = bool(turn.get("corrupt"))
        self.intent = turn.get("intent") or ("teach_phrase" if translation else "chat")
        self.reply = turn.get("reply") or ""
        self.syllables = turn.get("syllables") or ""
        self.verdict = turn.get("verdict") or "none"
        self.verdict_reason = turn.get("verdict_reason") or ""
        self.next_prompt = turn.get("next_prompt") or ""
        # Auto-target mode only: the language code the model says the learner asked for.
        self.target_language = turn.get("target_language") or ""


def error_strings(errors):
    """Scorer wants a list of strings, e.g. 'soy -> tengo'."""
    out = []
    for item in errors or []:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            heard = str(item.get("heard") or item.get("span") or "").strip()
            issue = str(item.get("issue") or item.get("difference") or "").strip()
            if heard and issue:
                text = f"{heard} -> {issue}"
            else:
                text = heard or issue
        else:
            text = ""
        if text:
            out.append(text)
    return out


class GatewayLLM:
    """Client for the AssemblyAI LLM Gateway (OpenAI-style chat completions).

    Built with the API key and the "llm" section of the config (model,
    system_prompt, max_tokens, temperature, timeout_s).
    """
    def __init__(self, api_key, llm_settings):
        """Keep the key and the LLM settings; no connection is opened here."""
        self.api_key = api_key
        self.settings = llm_settings

    async def complete(
        self,
        transcript,
        source_language,
        language_confidence,
        target_name,
        history=None,
        native_name="english",
        practice_target="",
        misses=0,
        auto_target=False,
        previous_target="",
        other_language="",
    ):
        # Overall time budget for this tutor call, in seconds.
        """Ask the tutor model for one turn; returns a Lesson.

        Inputs: the learner transcript, STT's detected language and its confidence,
        the target and native language names, recent chat history, and routing hints
        from the session (practice phrase, misses on it, other_language,
        auto-target fields). The blocking HTTP call runs in a worker thread, wrapped
        in an overall timeout (timeout_s + 1 s). Raises LLMError on timeout.
        """
        seconds = float(self.settings.get("timeout_s") or 30)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._post,
                    transcript,
                    source_language,
                    language_confidence,
                    target_name,
                    seconds,
                    list(history or []),
                    native_name,
                    practice_target,
                    misses,
                    auto_target,
                    previous_target,
                    other_language,
                ),
                timeout=seconds + 1,
            )
        except asyncio.TimeoutError as exc:
            raise LLMError(f"The tutor model timed out after {int(seconds)} seconds.") from exc

    def _post(
        self,
        transcript,
        source_language,
        language_confidence,
        target_name,
        seconds,
        history,
        native_name="english",
        practice_target="",
        misses=0,
        auto_target=False,
        previous_target="",
        other_language="",
    ):
        """Build the prompt and request body, send it, and retry/salvage bad replies.

        Runs in a worker thread. Retry policy (at most one retry, and only if the
        first answer came back within a third of the time budget, so a slow model
        never doubles the wait):
          * not JSON at all -> retry once; keep the retry if it parses.
          * JSON but control characters in the phrase/syllables -> retry once;
            otherwise keep the cleaned first answer.
          * still not JSON -> salvage the fields that were complete before the
            model got cut off (salvage_lesson).
        """
        prompt = system_prompt(self.settings.get("system_prompt") or "", target_name, native_name, auto_target)
        user_text = build_user_text(
            transcript,
            source_language,
            language_confidence,
            target_name,
            native_name,
            practice_target,
            misses,
            auto_target=auto_target,
            previous_target=previous_target,
            other_language=other_language,
        )
        # Request body: system prompt, recent history, then this turn. Low temperature
        # keeps answers consistent between runs.
        model = self.settings.get("model") or DEFAULT_MODEL
        body = {
            "model": model,
            "messages": [{"role": "system", "content": prompt}, *history, {"role": "user", "content": user_text}],
            "max_tokens": int(self.settings.get("max_tokens") or 450),
            "temperature": float(self.settings.get("temperature", 0.2)),
        }
        if model == GEMMA_MODEL:
            body["response_format"] = AUTO_LESSON_SCHEMA if auto_target else LESSON_SCHEMA
        # Timestamp used by the retry rule below (retry only if there is time left).
        started = time.time()
        lesson = self._send(body, model, seconds)
        if lesson.unparsed and time.time() - started < seconds / 3:
            # Rare non-JSON reply: one quick retry beats a silent turn.
            logger.warning("tutor reply was not JSON, retrying once: %r", lesson.raw_text[:200])
            retry = self._send(body, model, seconds - (time.time() - started))
            if not retry.unparsed:
                retry.retried = True
                return retry
        elif lesson.corrupt and time.time() - started < seconds / 3:
            # A control character inside the phrase or syllables: the phrase is
            # spoken and shown, so ask once more. The stripped first reply is the fallback.
            logger.warning("tutor phrase had control characters, retrying once: %r", lesson.raw_text[:200])
            retry = self._send(body, model, seconds - (time.time() - started))
            if not retry.unparsed and not retry.corrupt:
                retry.retried = True
                return retry
        # Still not JSON after any retry: keep whatever fields were complete.
        if lesson.unparsed:
            salvaged = salvage_lesson(lesson)
            if salvaged is not None:
                logger.warning("tutor reply was cut off; kept its complete fields: %r", lesson.raw_text[:200])
                return salvaged
        return lesson

    def _send(self, body, model, seconds):
        """One HTTP POST to the gateway; returns a Lesson (possibly marked unparsed).

        Network errors, timeouts and HTTP >= 400 become LLMError with a readable
        message. The body is decoded as UTF-8 by hand, then the message text is
        parsed into a Lesson together with token usage for cost tracking.
        """
        try:
            response = requests.post(
                LLM_URL,
                headers={"authorization": self.api_key},
                json=body,
                timeout=max(1.0, seconds),
            )
        except requests.Timeout as exc:
            raise LLMError(f"The tutor model timed out after {int(seconds)} seconds.") from exc
        except requests.RequestException as exc:
            raise LLMError(f"The tutor model could not be reached ({exc.__class__.__name__}).") from exc
        if response.status_code >= 400:
            raise LLMError(
                f"The tutor model returned HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            # Decode the whole body as UTF-8 ourselves (the gateway sends
            # application/json without relying on a charset guess).
            body = getattr(response, "content", None)
            data = json.loads(body.decode("utf-8", errors="replace")) if isinstance(body, bytes) else response.json()
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("The tutor model returned a lesson with no text.") from exc
        # Token usage is logged per turn for cost tracking. first_token_at is None
        # because this is not a streamed response.
        usage = data.get("usage") if isinstance(data, dict) else None
        lesson = _lesson(text or "", model, usage, None, time.time(), streamed=False)
        lesson.raw_text = text or ""
        return lesson


def system_prompt(template, target_name, native_name="english", auto_target=False):
    """Fill {target} and {native}. Auto-target mode appends how to pick the target."""
    # Auto-target: the language is not known yet, so the prompt says "the target
    # language" and the appended block explains how to pick it.
    if auto_target:
        target = "the target language"
    else:
        # Capitalise names like "spanish" for the prompt; leave already-styled names
        # alone.
        target = target_name.capitalize() if target_name.islower() else target_name
    prompt = template.replace("{target}", target).replace("{native}", (native_name or "english").capitalize())
    if auto_target:
        prompt = prompt.rstrip() + "\n" + AUTO_TARGET_PROMPT
    return prompt


def build_user_text(
    transcript,
    source_language,
    language_confidence,
    target_name,
    native_name="english",
    practice_target="",
    misses=0,
    auto_target=False,
    previous_target="",
    other_language="",
):
    # The first lines are context; the transcript always comes last.
    """Build the user message for this turn: context lines, then the verbatim transcript.

    The context lines tell the model the target and native languages, what STT
    detected, the current practice phrase and, depending on routing, one extra
    instruction: how many times the learner has missed the practice phrase, or
    treat this turn as speech in another language. The model itself decides
    whether the turn is an attempt and grades it. The transcript is passed
    verbatim so the model can spot (and not silently fix) learner mistakes.
    """
    confidence = "unknown" if language_confidence is None else language_confidence
    if auto_target:
        target_line = f"Previous target language: {previous_target or 'none'}."
    else:
        target_line = f"Target language: {target_name}."
    lines = [
        target_line,
        f"Learner's own language: {native_name or 'english'}.",
        f"Detected language of this turn: {source_language or 'unknown'} (confidence {confidence}).",
        f"Current practice phrase: {practice_target or '(none yet)'}.",
    ]
    # After earlier misses on the practice phrase, tell the model a slow syllable read
    # follows a wrong attempt, so its reply can lead into it.
    if practice_target and misses:
        lines.append(
            f"Misses on the practice phrase so far: {misses}. If this turn is an attempt and not correct, "
            "the syllables are read slowly after your reply, so lead into that."
        )
    # Routing hint: clear speech in some third language; teach its meaning instead of
    # saying "I didn't catch that".
    if other_language:
        native = native_name or "english"
        if auto_target:
            target = "the language they ask for (else the previous target language)"
            if not previous_target:
                target = "the language they ask for"
        else:
            target = target_name or "target language"
        # With a phrase pending, a mislabelled attempt ("Hola" tagged "pt") must still be
        # judged as an attempt, so the model gets the final say.
        attempt_note = (
            "If it is really an attempt at the current practice phrase (the language code is often wrong "
            "on short attempts), judge it as a practice_attempt instead. Otherwise: "
            if practice_target else ""
        )
        lines.append(
            f"Routing (automatic): this turn is clear speech in another language, not {native}. "
            + attempt_note +
            "The detected language code can be wrong; trust the transcript. Do not ask them to repeat it. "
            f"Understand it as if they had said it in {native}: if it asks how to say something, teach that; "
            f"otherwise teach what a native speaker would naturally say in {target} for its meaning "
            "(correct grammar, not word for word). "
            f"Use intent teach_phrase and explain in {native}."
            + (" If they name no language and there is no previous target, ask which one, as above." if auto_target and not previous_target else "")
        )
    lines.append("Learner transcript, verbatim. Never silently correct it:")
    lines.append(transcript)
    return "\n".join(lines)


def _has_bad_chars(value):
    """True if the value contains control characters or the U+FFFD replacement character."""
    return bool(BAD_CHARS.search(str(value or "")))


def _clean_chars(value):
    """Drop control characters other than tab and newline."""
    return BAD_CHARS.sub("", str(value or ""))


def _field(value):
    """A string field, without JSON debris the model sometimes leaves at the end.

    Seen live: '.\',\'}' and an unclosed fragment like 'silent in Spanish. {C'.
    Fields never contain braces, so an unclosed '{' or '[' and everything after
    it is dropped, and so are stray closing braces at the end.
    """
    text = _clean_chars(value).strip()
    # Drop an unclosed '{' or '[' and everything after it.
    text = re.sub(r"\s*[{\[][^{}\[\]]*$", "", text)
    # Strip trailing quote/comma/brace debris; repeat (max 3 times) until nothing
    # changes.
    for _ in range(3):
        before = text
        text = re.sub(r"""['"]?\s*,?\s*['"]?\s*[{}\[\]]+\s*$""", "", text)
        text = re.sub(r"""['"]\s*,\s*['"]?\s*$""", "", text).strip()
        # A dash left after the closing punctuation: 'sound is off.-'
        text = re.sub(r"([.!?])\s*[-–]+$", r"\1", text)
        if text == before:
            break
    return text


def _drop_junk_tail(text):
    """Drop one stray word after the last sentence end.

    Seen live twice from gemma-4-31b on a correct attempt: "...want to say?
    corticosteroids". A real sentence after "?", "!" or "." has more than one
    word or ends with punctuation of its own.
    """
    # Group 1: everything up to the last sentence end. Group 2: one trailing word with
    # no punctuation of its own.
    match = re.match(r"^(.*[.!?])\s+([^\s.!?]+)$", text or "", re.S)
    if match and len(match.group(2)) > 1:
        logger.warning("dropping a stray word after the sentence: %r", match.group(2))
        return match.group(1)
    return text


def _spoken(value):
    """Text a voice will read: drop template slots like {target_phrase} the model sometimes copies."""
    # Remove {slots}, then tidy the spacing and punctuation left behind.
    text = re.sub(r"\{[^{}]*\}", "", _field(value))
    text = re.sub(r"\s*\.\.\.\s*([.!?:,]|$)", r"\1", text)
    text = re.sub(r"\s+([.!?:,])", r"\1", text)
    text = re.sub(r"([.!?:,])\1+", r"\1", text)
    return _drop_junk_tail(re.sub(r"\s{2,}", " ", text).strip())


def _letters(text):
    """Lowercase letters and digits only (punctuation, spaces and _ removed), for comparing texts.
    """
    return re.sub(r"[^\w]|_", "", (text or "").lower())


def _trim_phrase(phrase, syllables):
    """Drop a stray tail the syllables do not cover.

    Seen live: target_phrase "Hola. corticosteroids" with syllables "ho - la".
    When the phrase has a sentence end followed by more words, and the syllables
    spell only the part before it, the tail is junk.
    """
    if not phrase or not syllables:
        return phrase
    spelled = _letters(syllables)
    if not spelled or _letters(phrase) == spelled:
        return phrase
    # Look for a sentence end followed by more words; if the syllables spell exactly the
    # text before it, the rest is junk.
    for match in re.finditer(r"[.!?…]+(?=\s+\S)", phrase):
        head = phrase[: match.end()].strip()
        if _letters(head) == spelled:
            # Keep ? and !, drop a lone sentence period: "Hola." -> "Hola".
            return re.sub(r"(?<!\.)\.$", "", head)
    return phrase


def _choice(value, allowed, default):
    """Normalise an enum-like field ("Try again" -> "try_again"); fall back to default if not allowed.
    """
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return text if text in allowed else default


def parse_lesson_text(text):
    """Parse the tutor JSON. None means the reply is not usable JSON.

    Accepts the tutor-turn schema and the older lesson schema
    (`translation` instead of `target_phrase`).
    """
    raw = (text or "").strip()
    # Some models wrap the JSON in a ``` code fence; unwrap it.
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    data = _load_object(raw)
    if data is None:
        return None
    errors = []
    # detected_errors may be plain strings or dicts with older key names; normalise to
    # {heard, issue}.
    for item in data.get("detected_errors") or []:
        if isinstance(item, str):
            errors.append({"heard": item, "issue": ""})
        elif isinstance(item, dict):
            errors.append(
                {
                    "heard": str(item.get("heard") or item.get("span") or ""),
                    "issue": str(item.get("issue") or item.get("difference") or ""),
                }
            )
    # Remember whether the raw phrase had control characters (triggers one retry), then
    # clean it and cut any tail the syllables do not cover.
    raw_phrase = data.get("target_phrase") or data.get("translation")
    corrupt = _has_bad_chars(raw_phrase) or _has_bad_chars(data.get("syllables"))
    phrase = _trim_phrase(_field(raw_phrase), _field(data.get("syllables")))
    intent = _choice(data.get("intent"), INTENTS, "teach_phrase" if phrase else "chat")
    verdict = _choice(data.get("verdict"), VERDICTS, "none")
    # A verdict only makes sense for a practice attempt.
    if intent != "practice_attempt":
        verdict = "none"
    reply = _spoken(data.get("reply") or data.get("reply_native"))
    next_prompt = _spoken(data.get("next_prompt"))
    # Do not say the next prompt twice if the model also appended it to the reply.
    if next_prompt and len(reply) > len(next_prompt) and reply.lower().endswith(next_prompt.lower()):
        reply = reply[: -len(next_prompt)].rstrip(" ,;-")
    return {
        "intent": intent,
        "reply": reply,
        "translation": phrase,
        "pronunciation": _field(data.get("pronunciation")),
        "syllables": _field(data.get("syllables")),
        "tip": _drop_junk_tail(_field(data.get("tip"))),
        "verdict": verdict,
        "verdict_reason": _drop_junk_tail(_field(data.get("verdict_reason"))),
        "next_prompt": next_prompt,
        "detected_errors": errors,
        "corrupt": corrupt,
        # Only the auto-target schema has it. Validated by the session.
        "target_language": _field(data.get("target_language")).lower(),
    }


# One complete "key": "string" pair, or an empty detected_errors list.
_COMPLETE_FIELD = re.compile(r'"(\w+)"\s*:\s*("(?:[^"\\]|\\.)*")')


def salvage_lesson(lesson):
    """A Lesson from a reply the model cut off, or None.

    Seen live: gemma sometimes loops inside one string ("gra - cias-t-i-a-s-t-i-a-s...")
    until max_tokens, so the JSON never closes. The fields it finished before
    the loop are fine. Keep those, drop the unfinished one, and require a
    target phrase so there is something real to teach.
    """
    raw = getattr(lesson, "raw_text", "") or ""
    data = {}
    # Keep the first complete value of each key; a cut-off string never matches the
    # regex.
    for key, value in _COMPLETE_FIELD.findall(raw):
        try:
            data.setdefault(key, json.loads(value, strict=False))
        except json.JSONDecodeError:
            continue
    if not str(data.get("target_phrase") or "").strip():
        return None
    # The errors list is usually the part that got cut off; drop it.
    data["detected_errors"] = []
    fixed = _lesson(json.dumps(data), lesson.model, None, lesson.first_token_at, lesson.done_at, lesson.streamed)
    if fixed.unparsed:
        return None
    fixed.tokens_in, fixed.tokens_out = lesson.tokens_in, lesson.tokens_out
    fixed.raw_text = raw
    fixed.salvaged = True
    return fixed


def _load_object(raw):
    """Parse a JSON object from the model text; None if there is none.

    Falls back to the outermost {...} span, for replies with chatter around the
    JSON. Anything that is not a dict counts as unusable.
    """
    # strict=False accepts the raw newline qwen sometimes puts inside a string.
    try:
        data = json.loads(raw, strict=False)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1], strict=False)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return data


def _tokens(usage):
    """Read (input_tokens, output_tokens) from a usage dict or object.

    Accepts both naming styles (input/output_tokens and prompt/completion_tokens).
    Returns (None, None) when usage is missing.
    """
    if not usage:
        return None, None
    if not isinstance(usage, dict):
        usage = {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        }
    incoming = usage.get("input_tokens")
    if incoming is None:
        incoming = usage.get("prompt_tokens")
    outgoing = usage.get("output_tokens")
    if outgoing is None:
        outgoing = usage.get("completion_tokens")
    return incoming, outgoing


def _lesson(text, model, usage, first_token_at, done_at, streamed):
    """Turn the model's text into a Lesson. Unusable text gives an empty Lesson with unparsed=True.
    """
    parsed = parse_lesson_text(text)
    tokens_in, tokens_out = _tokens(usage)
    if parsed is None:
        lesson = Lesson(
            translation="",
            pronunciation="",
            tip="",
            detected_errors=[],
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            first_token_at=first_token_at,
            done_at=done_at,
            streamed=streamed,
        )
        lesson.unparsed = True
        return lesson
    return Lesson(
        translation=parsed["translation"],
        pronunciation=parsed["pronunciation"],
        tip=parsed["tip"],
        detected_errors=parsed["detected_errors"],
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        first_token_at=first_token_at,
        done_at=done_at,
        streamed=streamed,
        corrupt=parsed["corrupt"],
        **{key: parsed[key] for key in TURN_FIELDS},
    )
