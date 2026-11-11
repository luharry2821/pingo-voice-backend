"""WebSocket server the Lovable page connects to. The API key stays here."""

import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from server.llm import GatewayLLM
from server.session import Session, now_ms
from server.tts import GatewayTTS


def create_app(settings, api_key, *, stt_factory=None, llm=None, tts=None, log_path=None):
    app = FastAPI(title="Pingo", docs_url=None, redoc_url=None)
    # The page is on Lovable; the socket is on localhost or a tunnel.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.api_key = api_key
    app.state.stt_factory = stt_factory
    app.state.llm = llm if llm is not None else GatewayLLM(api_key, settings["llm"])
    app.state.tts = tts if tts is not None else GatewayTTS(api_key, settings["tts"])
    app.state.log_path = log_path

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.websocket("/ws")
    async def socket(websocket: WebSocket):
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
            await session.close()

    return app


def _sender(websocket):
    async def send(payload):
        if isinstance(payload, (bytes, bytearray)):
            await websocket.send_bytes(bytes(payload))
        else:
            await websocket.send_json(payload)

    return send
