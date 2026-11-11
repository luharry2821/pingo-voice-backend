"""Lesson generation via the AssemblyAI LLM Gateway.

POST https://llm-gateway.assemblyai.com/v1/chat/completions
Same API key as speech-to-text. OpenAI-compatible, with a JSON schema so the
tutor card is always {translation, pronunciation, tip, detected_errors}.

We stream when the model allows it, only to stamp time-to-first-token.
Text-to-speech waits for the full object. We do not start the model early
off `end_of_turn_confidence`.
"""

import asyncio
import json
import time

import httpx

LLM_URL = "https://llm-gateway.assemblyai.com/v1/chat/completions"

LESSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "pingo_lesson",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "translation": {"type": "string"},
                "pronunciation": {"type": "string"},
                "tip": {"type": "string"},
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
            "required": ["translation", "pronunciation", "tip", "detected_errors"],
        },
    },
}


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
    """Scorer wants a list of strings, e.g. 'soy -> tengo'. Empty stays empty."""
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
                self._complete(transcript, source_language, language_confidence, target_name),
                timeout=seconds,
            )
        except asyncio.TimeoutError as exc:
            raise LLMError(
                f"The tutor model timed out after {int(seconds)} seconds."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"The tutor model timed out after {int(seconds)} seconds."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"The tutor model could not be reached ({exc.__class__.__name__}).") from exc

    async def _complete(self, transcript, source_language, language_confidence, target_name):
        prompt = (self.settings.get("system_prompt") or "").replace("{target}", target_name)
        confidence = "unknown" if language_confidence is None else language_confidence
        user = (
            f"Target language: {target_name}.\n"
            f"Detected source language: {source_language or 'unknown'}.\n"
            f"Source language confidence: {confidence}.\n"
            "Learner transcript, verbatim. Never silently correct it:\n"
            f"{transcript}"
        )
        body = {
            "model": self.settings.get("model") or "gemini-2.5-flash-lite",
            "temperature": float(self.settings.get("temperature", 0.2)),
            "max_tokens": int(self.settings.get("max_tokens", 500)),
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user},
            ],
            "response_format": LESSON_SCHEMA,
            "post_processing_steps": [{"type": "json-repair"}],
        }
        headers = {"Authorization": self.api_key, "Content-Type": "application/json"}
        timeout = httpx.Timeout(connect=10.0, read=float(self.settings.get("timeout_s") or 30), write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            if self.settings.get("stream", True):
                try:
                    return await self._stream(client, headers, body)
                except LLMError as exc:
                    # Some models reject stream. One non-streaming retry still
                    # returns a lesson; first-token time is then left empty.
                    if "stream" not in str(exc).lower():
                        raise
            return await self._once(client, headers, body)

    async def _stream(self, client, headers, body):
        payload = dict(body, stream=True)
        first_token_at = None
        parts = []
        usage = {}
        async with client.stream("POST", LLM_URL, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                detail = (await response.aread()).decode("utf-8", "replace")[:300]
                raise LLMError(f"The tutor model returned HTTP {response.status_code}: {detail}")
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type and "text/event-stream" not in content_type:
                data = json.loads((await response.aread()).decode("utf-8"))
                return lesson_from_response(data, body["model"], streamed=False)
            async for line in response.aiter_lines():
                piece, used, done = parse_sse_line(line)
                if used:
                    usage = used
                if piece:
                    if first_token_at is None:
                        first_token_at = time.time()
                    parts.append(piece)
                if done:
                    break
        text = "".join(parts).strip()
        if not text:
            raise LLMError("The tutor model returned an empty lesson.")
        return _lesson(text, body["model"], usage, first_token_at, time.time(), streamed=True)

    async def _once(self, client, headers, body):
        response = await client.post(LLM_URL, headers=headers, json=body)
        if response.status_code >= 400:
            raise LLMError(
                f"The tutor model returned HTTP {response.status_code}: {response.text[:300]}"
            )
        return lesson_from_response(response.json(), body["model"], streamed=False)


def lesson_from_response(data, model, streamed):
    if isinstance(data, dict) and "error" in data and "choices" not in data:
        raise LLMError(f"The tutor model returned an error: {data.get('error')}")
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("The tutor model returned a lesson with no text.") from exc
    done_at = time.time()
    return _lesson(text or "", model, data.get("usage") or {}, None, done_at, streamed)


def parse_sse_line(line):
    """Return (text_piece, usage_or_none, is_done) for one SSE line."""
    if not line or not line.startswith("data:"):
        return "", None, False
    data = line[len("data:") :].strip()
    if data == "[DONE]":
        return "", None, True
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return "", None, False
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
    choices = event.get("choices") or []
    if not choices:
        return "", usage, False
    choice = choices[0]
    delta = choice.get("delta") or {}
    piece = delta.get("content") or ""
    if not piece and isinstance(choice.get("message"), dict):
        piece = choice["message"].get("content") or ""
    return piece or "", usage, False


def parse_lesson_text(text):
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("The tutor model did not return JSON.") from exc
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as inner:
            raise LLMError("The tutor model did not return JSON.") from inner
    if not isinstance(data, dict):
        raise LLMError("The tutor model did not return a JSON object.")
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


def _tokens(usage):
    if not usage:
        return None, None
    return (
        usage.get("input_tokens", usage.get("prompt_tokens")),
        usage.get("output_tokens", usage.get("completion_tokens")),
    )


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
