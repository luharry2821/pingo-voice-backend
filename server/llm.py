"""Tutor turns from the AssemblyAI LLM Gateway, using the playground request.

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
model is gemma-4-31b, which gets response_format. qwen3.5-4b-32k-fast is the
same request without response_format.
"""

import asyncio
import json
import logging
import re
import time

import requests

logger = logging.getLogger("pingo.llm")

LLM_URL = "https://llm-gateway.assemblyai.com/v1/chat/completions"
DEFAULT_MODEL = "gemma-4-31b"
GEMMA_MODEL = "gemma-4-31b"

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

TURN_FIELDS = ("intent", "reply", "syllables", "verdict", "verdict_reason", "next_prompt")


class LLMError(RuntimeError):
    pass


class Lesson:
    """One tutor turn. `translation` is the target-language phrase (kept under
    its old name for the eval scorer and older clients)."""

    def __init__(self, translation, pronunciation, tip, detected_errors, model, tokens_in, tokens_out, first_token_at, done_at, streamed, **turn):
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
        self.intent = turn.get("intent") or ("teach_phrase" if translation else "chat")
        self.reply = turn.get("reply") or ""
        self.syllables = turn.get("syllables") or ""
        self.verdict = turn.get("verdict") or "none"
        self.verdict_reason = turn.get("verdict_reason") or ""
        self.next_prompt = turn.get("next_prompt") or ""

    @property
    def target_phrase(self):
        return self.translation

    def as_json(self):
        return {
            "intent": self.intent,
            "reply": self.reply,
            "target_phrase": self.translation,
            "translation": self.translation,
            "pronunciation": self.pronunciation,
            "syllables": self.syllables,
            "tip": self.tip,
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "next_prompt": self.next_prompt,
            "detected_errors": self.detected_errors,
        }


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
    def __init__(self, api_key, llm_settings):
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
        practice_check=None,
        new_phrase=False,
    ):
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
                    practice_check,
                    new_phrase,
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
        practice_check=None,
        new_phrase=False,
    ):
        prompt = (
            (self.settings.get("system_prompt") or "")
            .replace("{target}", target_name.capitalize() if target_name.islower() else target_name)
            .replace("{native}", (native_name or "english").capitalize())
        )
        user_text = build_user_text(
            transcript,
            source_language,
            language_confidence,
            target_name,
            native_name,
            practice_target,
            practice_check,
            new_phrase,
        )
        model = self.settings.get("model") or DEFAULT_MODEL
        body = {
            "model": model,
            "messages": [{"role": "system", "content": prompt}, *history, {"role": "user", "content": user_text}],
            "max_tokens": int(self.settings.get("max_tokens") or 450),
            "temperature": float(self.settings.get("temperature", 0.2)),
        }
        if model == GEMMA_MODEL:
            body["response_format"] = LESSON_SCHEMA
        started = time.time()
        lesson = self._send(body, model, seconds)
        if lesson.unparsed and time.time() - started < seconds / 3:
            # Rare non-JSON reply: one quick retry beats a silent turn.
            logger.warning("tutor reply was not JSON, retrying once: %r", lesson.raw_text[:200])
            retry = self._send(body, model, seconds - (time.time() - started))
            if not retry.unparsed:
                retry.retried = True
                return retry
        return lesson

    def _send(self, body, model, seconds):
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
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("The tutor model returned a lesson with no text.") from exc
        usage = data.get("usage") if isinstance(data, dict) else None
        lesson = _lesson(text or "", model, usage, None, time.time(), streamed=False)
        lesson.raw_text = text or ""
        return lesson


def build_user_text(
    transcript,
    source_language,
    language_confidence,
    target_name,
    native_name="english",
    practice_target="",
    practice_check=None,
    new_phrase=False,
):
    confidence = "unknown" if language_confidence is None else language_confidence
    lines = [
        f"Target language: {target_name}.",
        f"Learner's own language: {native_name or 'english'}.",
        f"Detected language of this turn: {source_language or 'unknown'} (confidence {confidence}).",
        f"Current practice phrase: {practice_target or '(none yet)'}.",
    ]
    if practice_check:
        low = ", ".join(
            f"{w['text']} ({w['confidence']})" for w in practice_check.get("low_confidence_words") or []
        ) or "none"
        lines.append(
            "Practice check (automatic, trust it): this turn sounds like an attempt at "
            f"\"{practice_check['target']}\". Match score {practice_check['score']}, "
            f"verdict {practice_check['verdict']} ({practice_check['reason']}). "
            f"Low-confidence words: {low}. Use intent practice_attempt and this verdict "
            "unless the learner is clearly asking a question."
        )
        misses = practice_check.get("misses_before")
        if misses:
            lines.append(
                f"Misses on this phrase before this turn: {misses}. If this one is not correct either, "
                "the syllables are read slowly after your reply, so lead into that."
            )
    elif new_phrase:
        lines.append(
            f"Routing (automatic): this turn is an everyday {native_name or 'english'} phrase, not an attempt "
            "at the practice phrase. Use intent teach_phrase and teach its natural "
            f"{target_name} equivalent, unless it is a question about pronunciation or "
            "repeating the current phrase, or meta talk about the lesson."
        )
    lines.append("Learner transcript, verbatim. Never silently correct it:")
    lines.append(transcript)
    return "\n".join(lines)


def _field(value):
    """A string field, without JSON debris the model sometimes leaves at the end.

    Seen live: '.\',\'}' and an unclosed fragment like 'silent in Spanish. {C'.
    Fields never contain braces, so an unclosed '{' or '[' and everything after
    it is dropped, and so are stray closing braces at the end.
    """
    text = str(value or "").strip()
    text = re.sub(r"\s*[{\[][^{}\[\]]*$", "", text)
    for _ in range(3):
        before = text
        text = re.sub(r"""['"]?\s*,?\s*['"]?\s*[{}\[\]]+\s*$""", "", text)
        text = re.sub(r"""['"]\s*,\s*['"]?\s*$""", "", text).strip()
        # A dash left after the closing punctuation: 'sound is off.-'
        text = re.sub(r"([.!?])\s*[-–]+$", r"\1", text)
        if text == before:
            break
    return text


def _spoken(value):
    """Text a voice will read: drop template slots like {target_phrase} the model sometimes copies."""
    text = re.sub(r"\{[^{}]*\}", "", _field(value))
    text = re.sub(r"\s*\.\.\.\s*([.!?:,]|$)", r"\1", text)
    text = re.sub(r"\s+([.!?:,])", r"\1", text)
    text = re.sub(r"([.!?:,])\1+", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _choice(value, allowed, default):
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return text if text in allowed else default


def parse_lesson_text(text):
    """Parse the tutor JSON. None means the reply is not usable JSON.

    Accepts the tutor-turn schema and the older lesson schema
    (`translation` instead of `target_phrase`).
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    data = _load_object(raw)
    if data is None:
        return None
    errors = []
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
    phrase = _field(data.get("target_phrase") or data.get("translation"))
    intent = _choice(data.get("intent"), INTENTS, "teach_phrase" if phrase else "chat")
    verdict = _choice(data.get("verdict"), VERDICTS, "none")
    if intent != "practice_attempt":
        verdict = "none"
    reply = _spoken(data.get("reply") or data.get("reply_native"))
    next_prompt = _spoken(data.get("next_prompt"))
    if next_prompt and len(reply) > len(next_prompt) and reply.lower().endswith(next_prompt.lower()):
        reply = reply[: -len(next_prompt)].rstrip(" ,;-")
    return {
        "intent": intent,
        "reply": reply,
        "translation": phrase,
        "pronunciation": _field(data.get("pronunciation")),
        "syllables": _field(data.get("syllables")),
        "tip": _field(data.get("tip")),
        "verdict": verdict,
        "verdict_reason": _field(data.get("verdict_reason")),
        "next_prompt": next_prompt,
        "detected_errors": errors,
    }


def _load_object(raw):
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
        **{key: parsed[key] for key in TURN_FIELDS},
    )
