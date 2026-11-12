"""The WebSocket server the Lovable browser app connects to. The API key stays here.

Pipeline position: this is the front door. The browser opens ws://.../ws and
sends two kinds of frames on the same socket:

  binary frames  16 kHz PCM16 mono mic audio -> Session.handle_audio -> STT
  text frames    JSON control messages (start, update_config, set_target,
                 replay, stop) -> Session.handle_text

Everything the session produces comes back on the same socket: JSON
messages (status, transcript, lesson, agent_text, tts_start, tts_end,
barge_in, error) and binary frames of 24 kHz PCM16 tutor speech.

GET /health is a liveness check for the host (Render).
"""

import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from server.llm import GatewayLLM
from server.session import Session, now_ms
from server.tts import GatewayTTS


def create_app(settings, api_key, *, stt_factory=None, llm=None, tts=None, log_path=None):
    # No auto-generated API docs pages: this server only serves the socket and /health.
    """Build the FastAPI app with /health and /ws.

    Inputs: the process settings and the AssemblyAI key, plus optional fakes
    (stt_factory, llm, tts) and a log path, which the tests use. One LLM and one
    TTS client are shared by all connections; each connection gets its own
    Session (and its own per-voice TTS sessions via the TTS pool).
    """
    app = FastAPI(title="Pingo", docs_url=None, redoc_url=None)
    # The page is on Lovable; the socket is on localhost or a tunnel.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Keep the shared objects on app.state so tests can inspect them.
    app.state.settings = settings
    app.state.api_key = api_key
    app.state.stt_factory = stt_factory
    app.state.llm = llm if llm is not None else GatewayLLM(api_key, settings["llm"])
    app.state.tts = tts if tts is not None else GatewayTTS(api_key, settings["tts"])
    app.state.log_path = log_path

    @app.get("/health")
    def health():
        """Liveness check: always {"ok": true}."""
        return {"ok": True}

    @app.websocket("/ws")
    async def socket(websocket: WebSocket):
        """One browser connection: create a Session and pump frames into it until disconnect.

        Binary frames are audio, text frames are JSON messages. Bad JSON is
        answered with an error and the loop keeps going. When the socket closes
        for any reason, session.shutdown() closes STT and the TTS sessions.
        """
        await websocket.accept()
        session = Session(
            settings,
            api_key,
            _sender(websocket),
            stt_factory=stt_factory,
            llm=app.state.llm,
            tts=app.state.tts,
            log_path=log_path,
        )
        try:
            while True:
                # receive() returns raw ASGI messages, so audio (bytes) and JSON (text)
                # arrive through one call in order.
                incoming = await websocket.receive()
                if incoming["type"] == "websocket.disconnect":
                    break
                if incoming.get("bytes") is not None:
                    # Stamp before any await so connect() cannot move the clock.
                    arrived = now_ms()
                    await session.handle_audio(incoming["bytes"], arrived_at=arrived)
                elif incoming.get("text") is not None:
                    try:
                        message = json.loads(incoming["text"])
                    except json.JSONDecodeError:
                        await session._error("Expected a JSON text frame.")
                        continue
                    if not isinstance(message, dict):
                        await session._error("Expected a JSON object.")
                        continue
                    if message.get("type") == "start":
                        # Keep reading audio while STT connects, so the first
                        # frame's arrival stays the speech-end origin.
                        task = asyncio.create_task(session.handle_text(message))
                        session.background.add(task)
                        task.add_done_callback(session.background.discard)
                    else:
                        await session.handle_text(message)
        except WebSocketDisconnect:
            pass
        finally:
            await session.shutdown()

    return app


def _sender(websocket):
    """Make the session's `send` callback for this socket: bytes -> binary frame, dict -> JSON text frame.
    """
    async def send(payload):
        """Send one payload to the browser in the right frame type."""
        if isinstance(payload, (bytes, bytearray)):
            await websocket.send_bytes(bytes(payload))
        else:
            await websocket.send_json(payload)

    return send
