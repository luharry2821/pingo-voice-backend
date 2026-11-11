"""Lessons from the AssemblyAI LLM Gateway, using the playground request.

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

The POST runs in a thread so the asyncio session is not blocked. The model
is asked for JSON in the system prompt. response_format is not sent.
"""

import asyncio
import json
import time

import requests

LLM_URL = "https://llm-gateway.assemblyai.com/v1/chat/completions"
DEFAULT_MODEL = "qwen3.5-4b-32k-fast"


class LLMError(RuntimeError):
    pass


class Lesson:
    def __init__(self, translation, pronunciation, tip, detected_errors, model, tokens_in, tokens_out, first_token_at, done_at, streamed):
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

    def as_json(self):
        return {
            "translation": self.translation,
            "pronunciation": self.pronunciation,
            "tip": self.tip,
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

    async def complete(self, transcript, source_language, language_confidence, target_name):
        seconds = float(self.settings.get("timeout_s") or 30)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._post, transcript, source_language, language_confidence, target_name, seconds
                ),
                timeout=seconds + 1,
            )
        except asyncio.TimeoutError as exc:
            raise LLMError(f"The tutor model timed out after {int(seconds)} seconds.") from exc

    def _post(self, transcript, source_language, language_confidence, target_name, seconds):
        prompt = (self.settings.get("system_prompt") or "").replace("{target}", target_name)
        confidence = "unknown" if language_confidence is None else language_confidence
        user_text = (
            f"Target language: {target_name}.\n"
            f"Detected source language: {source_language or 'unknown'}.\n"
            f"Source language confidence: {confidence}.\n"
            "Learner transcript, verbatim. Never silently correct it:\n"
            f"{transcript}"
        )
        model = self.settings.get("model") or DEFAULT_MODEL
        try:
            response = requests.post(
                LLM_URL,
                headers={"authorization": self.api_key},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_text},
                    ],
                },
                timeout=seconds,
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
        return _lesson(text or "", model, usage, None, time.time(), streamed=False)


def parse_lesson_text(text):
    """Parse the tutor JSON. If it is not JSON, the whole reply is the translation."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    data = _load_object(raw)
    if data is None:
        return {
            "translation": (text or "").strip(),
            "pronunciation": "",
            "tip": "",
            "detected_errors": [],
        }
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
    return {
        "translation": str(data.get("translation") or "").strip(),
        "pronunciation": str(data.get("pronunciation") or "").strip(),
        "tip": str(data.get("tip") or "").strip(),
        "detected_errors": errors,
    }


def _load_object(raw):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
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
    )
