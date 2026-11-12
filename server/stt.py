"""Streaming speech-to-text through AssemblyAI's official Python SDK.

Pipeline position: browser mic audio -> **streaming STT (this file)** -> LLM
tutor -> TTS -> browser. server/session.py creates one StreamingSTT per
`start`, streams 16 kHz PCM16 into it, and receives events as plain dicts:

  Begin          the session opened (id + the configuration actually used)
  SpeechStarted  voice activity began (used for barge-in)
  Turn           a partial or final transcript with words, confidence and
                 the detected language (end_of_turn: true marks the final)
  Error          something went wrong upstream

This is the playground pattern: a RealTimeTranscriber, event handlers, then
connect / stream / disconnect. The SDK runs its own threads. Events are
handed back to the asyncio session with run_coroutine_threadsafe.

`mode` is a connect parameter. The SDK's set_params model (RealTimeSessionParameters)
has no `mode` field, so a mid-session mode change is kept for the next start.
`end_of_turn_confidence_threshold` and the streaming `llm_gateway` parameter
are never sent.
"""

import asyncio
import logging

from assemblyai.streaming.v3 import (
    RealTimeEvents,
    RealTimeParameters,
    RealTimeSessionParameters,
    RealTimeTranscriber,
    RealTimeTranscriberOptions,
)

logger = logging.getLogger("pingo.stt")

# Settings the browser may change while a stream is live (update_config). Anything else
# (sample rate, Voice Focus, model) only applies on the next `start`.
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

# What set_params can actually carry. `mode` is not one of them.
SESSION_FIELDS = set(RealTimeSessionParameters.model_fields) - {
    "end_of_turn_confidence_threshold",
    "min_end_of_turn_silence_when_confident",
}

# API limits: up to 100 keyterms of up to 50 characters; prompt and agent_context up to
# 1750 characters.
KEYTERMS_MAX = 100
KEYTERM_CHARS = 50
TEXT_CHARS = 1750


def stt_query_params(stt):
    """Fields passed to RealTimeParameters. Omitted fields keep the server default.

    Empty language_codes stays unset so the model code-switches across all 32
    languages. mode and interruption_delay are included. prompt is included
    when it is non-empty.
    """
    # Always sent: audio format, model, and language detection.
    params = {
        "sample_rate": int(stt.get("sample_rate") or 16000),
        "encoding": stt.get("encoding") or "pcm_s16le",
        "speech_model": stt.get("speech_model") or "universal-3-6-pro",
        "language_detection": bool(stt.get("language_detection", True)),
    }
    # Optional fields are added only when set, so the server's own defaults apply
    # otherwise.
    if stt.get("mode"):
        params["mode"] = stt["mode"]
    if stt.get("interruption_delay") is not None:
        params["interruption_delay"] = int(stt["interruption_delay"])
    prompt = (stt.get("prompt") or "").strip()
    if prompt:
        params["prompt"] = prompt[:TEXT_CHARS]
    # Empty language_codes = let the model detect any supported language.
    codes = [code for code in (stt.get("language_codes") or []) if code]
    if codes:
        params["language_codes"] = codes
    terms = clip_keyterms(stt.get("keyterms_prompt") or [])
    if terms:
        params["keyterms_prompt"] = terms
    # agent_context (what the tutor just said) is only sent when the feature flag is on.
    if stt.get("agent_context_enabled") and (stt.get("agent_context") or "").strip():
        params["agent_context"] = str(stt["agent_context"])[:TEXT_CHARS]
    if stt.get("min_turn_silence") is not None:
        params["min_turn_silence"] = int(stt["min_turn_silence"])
    if stt.get("max_turn_silence") is not None:
        params["max_turn_silence"] = int(stt["max_turn_silence"])
    if stt.get("vad_threshold") is not None:
        params["vad_threshold"] = float(stt["vad_threshold"])
    # Profanity filter: sent only when True (absent = off).
    if stt.get("filter_profanity"):
        params["filter_profanity"] = True
    # Voice Focus is a connection parameter: a change takes effect on the next start.
    focus = (stt.get("voice_focus") or "").strip().lower()
    if focus and focus != "off":
        params["voice_focus"] = focus
        if stt.get("voice_focus_threshold") is not None:
            params["voice_focus_threshold"] = float(stt["voice_focus_threshold"])
    return params


def build_parameters(params):
    """The object passed to RealTimeTranscriber.connect."""
    return RealTimeParameters(**params)


def update_configuration_message(changes, stt):
    """Fields for set_params, or None when the SDK cannot apply the change.

    `mode` is dropped here: RealTimeSessionParameters does not have it.
    """
    if not changes:
        return None
    body = {}
    # Keep only fields that are both live-updatable and accepted by set_params.
    for key in UPDATABLE:
        if key not in changes or key not in SESSION_FIELDS:
            continue
        # agent_context is ignored unless the feature is enabled for this session.
        if key == "agent_context" and not stt.get("agent_context_enabled"):
            continue
        value = changes[key]
        # None for a numeric setting means "leave as is", not "set to null".
        if value is None and key in (
            "min_turn_silence",
            "max_turn_silence",
            "interruption_delay",
            "vad_threshold",
        ):
            continue
        # Normalise each value the same way the connection parameters are normalised.
        if key == "keyterms_prompt":
            value = clip_keyterms(value or [])
        elif key == "language_codes":
            value = [str(code) for code in (value or []) if code]
        elif key in ("prompt", "agent_context") and value is not None:
            value = str(value)[:TEXT_CHARS]
        elif key in ("min_turn_silence", "max_turn_silence", "interruption_delay"):
            value = int(value)
        elif key == "vad_threshold":
            value = float(value)
        body[key] = value
    if not body:
        return None
    body["type"] = "UpdateConfiguration"
    return body


def clip_keyterms(terms):
    """Clean the keyterms list for the API: drop blanks, cut each term to 50 chars, keep at most 100.
    """
    cleaned = []
    for term in terms or []:
        text = str(term).strip()
        if not text:
            continue
        cleaned.append(text[:KEYTERM_CHARS])
        if len(cleaned) >= KEYTERMS_MAX:
            break
    return cleaned


def _event_dict(event):
    """Convert an SDK event object (pydantic v2 or v1) into a JSON-ready dict."""
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    return event.dict()


class StreamingSTT:
    """One SDK streaming session. on_event receives plain dicts, same as before."""

    def __init__(self, api_key, params, on_event):
        """Remember the key, the connection parameters and the event callback. Nothing connects yet.

        params: the dict from stt_query_params(). on_event: async callable(dict)
        that the session uses to receive Begin / Turn / SpeechStarted / Error.
        """
        self.api_key = api_key
        self.params = params
        self.on_event = on_event
        self._client = None
        self._loop = None
        self._open = False
        self._connect_error = None

    async def connect(self):
        """Open the streaming connection (blocking SDK call, run in a thread).

        Registers the event handlers first so no early event is missed. The SDK does
        not raise when the handshake fails, so success is checked by whether its
        writer thread is alive; otherwise the error it reported is raised.
        """
        self._loop = asyncio.get_running_loop()
        # Same construction as the AssemblyAI streaming playground snippet.
        self._client = RealTimeTranscriber(
            RealTimeTranscriberOptions(
                api_key=self.api_key,
                api_host="streaming.assemblyai.com",
                connect_timeout=15,
            )
        )
        self._client.on(RealTimeEvents.Begin, self._on_begin)
        self._client.on(RealTimeEvents.Turn, self._on_turn)
        self._client.on(RealTimeEvents.SpeechStarted, self._on_speech_started)
        self._client.on(RealTimeEvents.Termination, self._on_terminated)
        self._client.on(RealTimeEvents.Error, self._on_error)
        await asyncio.to_thread(self._client.connect, build_parameters(self.params))
        # _write_thread is private SDK state, but it is the only reliable signal that
        # the handshake succeeded.
        thread = getattr(self._client, "_write_thread", None)
        if thread is None or not thread.is_alive():
            message = self._connect_error or "Speech-to-text did not connect."
            self._client = None
            raise RuntimeError(message)
        self._open = True

    async def send_audio(self, pcm: bytes):
        """Queue one chunk of PCM16 audio for the SDK to send."""
        # stream() only enqueues. 50 ms PCM16 frames are what the SDK example sends.
        if self._client is not None and pcm:
            self._client.stream(pcm)

    async def send_json(self, payload: dict):
        """Apply a live configuration change (UpdateConfiguration) through set_params.

        Only fields RealTimeSessionParameters supports are passed; the "type" key and
        anything else are dropped. Does nothing when not connected.
        """
        if self._client is None or not payload:
            return
        # Drop the "type" marker and anything set_params cannot carry.
        fields = {key: value for key, value in payload.items() if key in SESSION_FIELDS}
        if not fields:
            return
        self._client.set_params(RealTimeSessionParameters(**fields))

    async def close(self):
        """Disconnect (waiting for the server to terminate the session) in a worker thread.

        Safe to call twice: the client is cleared first.
        """
        client = self._client
        self._client = None
        self._open = False
        if client is not None:
            await asyncio.to_thread(client.disconnect, True)

    def _on_begin(self, _client, event):
        """SDK thread: Begin event -> session."""
        self._schedule(_event_dict(event))

    def _on_turn(self, _client, event):
        """SDK thread: Turn event (partial or final transcript) -> session."""
        self._schedule(_event_dict(event))

    def _on_speech_started(self, _client, event):
        """SDK thread: SpeechStarted event -> session (may trigger barge-in)."""
        self._schedule(_event_dict(event))

    def _on_terminated(self, _client, event):
        """SDK thread: the session ended; only logged, the session already knows it closed."""
        logger.info("STT terminated: %s", _event_dict(event))

    def _on_error(self, _client, error):
        """SDK thread: an error. Before the connection is open it is saved for connect() to raise.
        """
        text = getattr(error, "args", ("",))[0] or str(error)
        if not self._open:
            self._connect_error = text
            return
        self._schedule({"type": "Error", "error": text})

    def _schedule(self, message):
        """Hand an event from the SDK's thread to the asyncio loop running the session.

        run_coroutine_threadsafe is the safe way to call async code from another
        thread. If the loop is already gone (shutdown), the event is dropped.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self.on_event(message), loop)
        except RuntimeError:
            logger.debug("event loop closed before an STT event could be delivered")
            return
        future.add_done_callback(_log_handler_error)


def _log_handler_error(future):
    """Log any exception raised by the session while handling an STT event."""
    try:
        future.result()
    except Exception:
        logger.exception("speech-to-text event handler failed")
