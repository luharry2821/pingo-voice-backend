"""AssemblyAI Universal-3.6 Pro streaming speech-to-text.

Connect:  wss://streaming.assemblyai.com/v3/ws
Auth:     Authorization header, the raw API key (no "Bearer ").
Audio:    binary frames of 16 kHz pcm_s16le.

We do not send `end_of_turn_confidence_threshold` (Universal Streaming only)
and we do not set the streaming `llm_gateway` parameter. The lesson is a
separate chat-completions call so a failed generation cannot swallow the turn.
"""

import asyncio
import json
import logging
from urllib.parse import urlencode

import websockets

logger = logging.getLogger("pingo.stt")

STT_URL = "wss://streaming.assemblyai.com/v3/ws"
API_VERSION = "2025-05-12"

UPDATABLE = (
    "prompt",
    "keyterms_prompt",
    "min_turn_silence",
    "max_turn_silence",
    "vad_threshold",
    "interruption_delay",
    "mode",
    "language_codes",
    "agent_context",
)

KEYTERMS_MAX = 100
KEYTERM_CHARS = 50
TEXT_CHARS = 1750


def stt_query_params(stt):
    """Query fields actually sent. Omitted fields keep the server default.

    Empty `language_codes` is left unset so the model code-switches across all
    32 languages. `mode` and `interruption_delay` are sent: both are documented
    for universal-3-6-pro. `prompt` is sent when it is non-empty.
    """
    params = {
        "speech_model": stt.get("speech_model") or "universal-3-6-pro",
        "sample_rate": int(stt.get("sample_rate") or 16000),
        "encoding": stt.get("encoding") or "pcm_s16le",
        "language_detection": "true" if stt.get("language_detection", True) else "false",
    }
    if stt.get("mode"):
        params["mode"] = stt["mode"]
    if stt.get("interruption_delay") is not None:
        params["interruption_delay"] = int(stt["interruption_delay"])
    prompt = (stt.get("prompt") or "").strip()
    if prompt:
        params["prompt"] = prompt[:TEXT_CHARS]
    codes = [code for code in (stt.get("language_codes") or []) if code]
    if codes:
        params["language_codes"] = json.dumps(codes)
    terms = clip_keyterms(stt.get("keyterms_prompt") or [])
    if terms:
        params["keyterms_prompt"] = json.dumps(terms)
    if stt.get("agent_context_enabled") and (stt.get("agent_context") or "").strip():
        params["agent_context"] = str(stt["agent_context"])[:TEXT_CHARS]
    if stt.get("min_turn_silence") is not None:
        params["min_turn_silence"] = int(stt["min_turn_silence"])
    if stt.get("max_turn_silence") is not None:
        params["max_turn_silence"] = int(stt["max_turn_silence"])
    if stt.get("vad_threshold") is not None:
        params["vad_threshold"] = stt["vad_threshold"]
    return params


def update_configuration_message(changes, stt):
    """An UpdateConfiguration body, or None when there is nothing to send."""
    if not changes:
        return None
    body = {"type": "UpdateConfiguration"}
    for key in UPDATABLE:
        if key not in changes:
            continue
        if key == "agent_context" and not stt.get("agent_context_enabled"):
            continue
        value = changes[key]
        if value is None and key in (
            "min_turn_silence",
            "max_turn_silence",
            "interruption_delay",
            "vad_threshold",
        ):
            continue
        if key == "keyterms_prompt":
            value = clip_keyterms(value or [])
        elif key == "language_codes":
            value = [str(code) for code in (value or []) if code]
        elif key in ("prompt", "agent_context") and value is not None:
            value = str(value)[:TEXT_CHARS]
        elif key in ("min_turn_silence", "max_turn_silence", "interruption_delay"):
            value = int(value)
        body[key] = value
    if len(body) == 1:
        return None
    return body


def clip_keyterms(terms):
    cleaned = []
    for term in terms or []:
        text = str(term).strip()
        if not text:
            continue
        cleaned.append(text[:KEYTERM_CHARS])
        if len(cleaned) >= KEYTERMS_MAX:
            break
    return cleaned


def build_url(params):
    return STT_URL + "?" + urlencode(params)


class StreamingSTT:
    """One upstream session. The reader task calls on_event with each JSON message."""

    def __init__(self, api_key, params, on_event):
        self.api_key = api_key
        self.params = params
        self.on_event = on_event
        self.ws = None
        self._reader = None

    async def connect(self):
        self.ws = await websockets.connect(
            build_url(self.params),
            additional_headers={
                "Authorization": self.api_key,
                "AssemblyAI-Version": API_VERSION,
            },
            open_timeout=15,
            max_size=8 * 1024 * 1024,
        )
        self._reader = asyncio.create_task(self._read())

    async def send_audio(self, pcm: bytes):
        if self.ws is not None:
            await self.ws.send(pcm)

    async def send_json(self, payload: dict):
        if self.ws is not None:
            await self.ws.send(json.dumps(payload))

    async def close(self):
        ws = self.ws
        self.ws = None
        if ws is not None:
            try:
                await ws.send(json.dumps({"type": "Terminate"}))
            except Exception:
                logger.debug("terminate send failed", exc_info=True)
            try:
                await ws.close()
            except Exception:
                logger.debug("websocket close failed", exc_info=True)
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()

    async def _read(self):
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("speech-to-text sent non-JSON text")
                    continue
                await self.on_event(message)
        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed as exc:
            await self.on_event(
                {"type": "Error", "error": f"Speech-to-text connection closed ({exc.code})."}
            )
        except Exception as exc:
            await self.on_event({"type": "Error", "error": f"Speech-to-text failed: {exc}"})
