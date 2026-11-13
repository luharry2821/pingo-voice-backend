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

Who may connect: browsers always send an Origin header, so /ws refuses any
Origin that is not the Pingo Lovable app (or its editor preview, or localhost),
and CORS allows the same list. A missing Origin is a non-browser client (the
eval scripts, curl); it is allowed, so the Origin check stops other websites from
spending the key through a visitor's browser, not a determined script. Every
client also gets a per-IP cap: concurrent sockets and new sockets per minute.
"""

import asyncio
import json
import os
import re
import time
from collections import defaultdict, deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from server.llm import GatewayLLM
from server.session import Session, now_ms
from server.tts import GatewayTTS

# The live app. PINGO_ALLOWED_ORIGINS (comma-separated) replaces this list.
DEFAULT_ALLOWED_ORIGINS = ("https://pingo-ai.lovable.app",)
# The same Lovable project's editor previews, and local frontend dev servers.
LOVABLE_PROJECT_ID = "1216d0da-cf60-4a31-91c4-6916a3d74f35"
DEFAULT_ORIGIN_REGEX = (
    r"^(https://((id-)?preview--)?(pingo-ai|" + LOVABLE_PROJECT_ID + r")\.lovable\.app"
    r"|https://" + LOVABLE_PROJECT_ID + r"\.lovableproject\.com"
    r"|https?://(localhost|127\.0\.0\.1)(:\d+)?)$"
)
# Per client IP. One learner uses one socket; 3 leaves room for a second tab and
# the 2-session eval runner. Each socket opens 2 TTS sessions and the account may
# open 5 a minute, so new sockets are capped too.
MAX_CONNECTIONS_PER_IP = 3
MAX_NEW_CONNECTIONS_PER_IP_PER_MINUTE = 10


def allowed_origins(env=None):
    """The exact allowed origins: PINGO_ALLOWED_ORIGINS if set, else the live app."""
    env = os.environ if env is None else env
    raw = env.get("PINGO_ALLOWED_ORIGINS", "")
    items = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
    return items or list(DEFAULT_ALLOWED_ORIGINS)


def origin_allowed(origin, origins, pattern):
    """True for a missing Origin (not a browser), an exact match, or the preview/localhost pattern."""
    if not origin:
        return True
    origin = origin.rstrip("/")
    return origin in origins or bool(pattern and re.match(pattern, origin))


def client_ip(websocket, trust_proxy):
    """The caller's IP. Behind Render (Cloudflare in front) the socket peer is a proxy,
    so trust True-Client-IP / CF-Connecting-IP, then the first X-Forwarded-For entry,
    which Render sets to the real client."""
    headers = websocket.headers
    if trust_proxy:
        for name in ("true-client-ip", "cf-connecting-ip"):
            if headers.get(name):
                return headers[name].strip()
        forwarded = headers.get("x-forwarded-for", "")
        if forwarded.split(",")[0].strip():
            return forwarded.split(",")[0].strip()
    return websocket.client.host if websocket.client else "unknown"


class ConnectionLimiter:
    """Per-IP cap on open sockets and on new sockets per rolling minute (in memory, one process)."""

    def __init__(self, max_open, max_per_minute, clock=time.monotonic):
        self.max_open = max_open
        self.max_per_minute = max_per_minute
        self.clock = clock
        self.open = defaultdict(int)
        self.recent = defaultdict(deque)

    def acquire(self, ip):
        """Count a new socket for ip. Returns None if allowed, else the reason it is refused."""
        now = self.clock()
        recent = self.recent[ip]
        while recent and now - recent[0] >= 60:
            recent.popleft()
        if self.max_open and self.open[ip] >= self.max_open:
            return f"Too many open Pingo connections from this address (max {self.max_open}). Close another tab and try again."
        if self.max_per_minute and len(recent) >= self.max_per_minute:
            return "Too many new Pingo connections from this address. Wait a minute and try again."
        recent.append(now)
        self.open[ip] += 1
        return None

    def release(self, ip):
        """The socket for ip closed."""
        self.open[ip] = max(0, self.open[ip] - 1)
        if not self.open[ip]:
            self.open.pop(ip, None)


def _env_int(env, name, default):
    """An integer environment knob (0 turns the limit off)."""
    try:
        return int(env.get(name, default))
    except (TypeError, ValueError):
        return default


def create_app(settings, api_key, *, stt_factory=None, llm=None, tts=None, log_path=None, env=None):
    # No auto-generated API docs pages: this server only serves the socket and /health.
    """Build the FastAPI app with /health and /ws.

    Inputs: the process settings and the AssemblyAI key, plus optional fakes
    (stt_factory, llm, tts) and a log path, which the tests use. One LLM and one
    TTS client are shared by all connections; each connection gets its own
    Session (and its own per-voice TTS sessions via the TTS pool).
    """
    env = os.environ if env is None else env
    app = FastAPI(title="Pingo", docs_url=None, redoc_url=None)
    origins = allowed_origins(env)
    origin_regex = env.get("PINGO_ALLOWED_ORIGIN_REGEX", DEFAULT_ORIGIN_REGEX)
    # The page is on Lovable; only it (and its previews, and localhost) may call us.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=origin_regex or None,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    # Render sets RENDER=true; only then are the proxy's client-IP headers trusted.
    trust_proxy = str(env.get("PINGO_TRUST_PROXY_HEADERS", env.get("RENDER", ""))).lower() in ("1", "true", "yes")
    limiter = ConnectionLimiter(
        _env_int(env, "PINGO_MAX_CONNECTIONS_PER_IP", MAX_CONNECTIONS_PER_IP),
        _env_int(env, "PINGO_MAX_NEW_CONNECTIONS_PER_IP_PER_MINUTE", MAX_NEW_CONNECTIONS_PER_IP_PER_MINUTE),
    )
    app.state.limiter = limiter
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
        if not origin_allowed(websocket.headers.get("origin"), origins, origin_regex):
            # Closing before accept refuses the handshake (HTTP 403).
            await websocket.close(code=1008)
            return
        ip = client_ip(websocket, trust_proxy)
        refused = limiter.acquire(ip)
        await websocket.accept()
        if refused:
            # Accept first so the browser can show why, then close: 1013 = try again later.
            await websocket.send_json({"type": "error", "message": refused})
            await websocket.close(code=1013)
            return
        try:
            await _serve(websocket)
        finally:
            limiter.release(ip)

    async def _serve(websocket):
        """Pump one accepted socket into a new Session until it disconnects."""
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
