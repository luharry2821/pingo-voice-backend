# yc-clone — "I'm a solo YC founder building a company."

A live demo for the YC batch: Harry, the founder of **Pingo AI**, has three calls today (Mom, a new engineer, an upset customer). His **AssemblyAI voice clone** takes them. Two AI voices talk turn by turn, and **each side really listens**: every spoken line is transcribed by AssemblyAI streaming speech-to-text, and the other side only ever sees what STT heard.

Powered by AssemblyAI · Voice cloning, TTS, Streaming STT & LLM Gateway.

## How a turn works

1. **LLM Gateway** writes the speaker's next line (server-side). Counterpart personas (Mom / Alex / Dana) live only in `server/personas.ts`. Harry gets no script, only his brief: **Goal**, **Intent**, **What to get across**, and an optional attached doc (PDF / TXT / MD / DOCX, truncated to 20k chars).
2. **TTS** speaks it: `POST https://tts.us.assemblyai.com/v1/speech` (WAV = 24 kHz 16-bit mono PCM). Harry uses your cloned `voice_id` (cloned voices are served only from their own zone, US). Counterparts use stock voices (`aai-kyla`, `aai-titus`, `aai-audrey`).
3. **Streaming STT** listens while the line plays: the server mints a temporary token (`POST https://token.assemblyai.com/v1/token`) and the browser streams the PCM to `wss://streaming.assemblyai.com/v3/ws?sample_rate=24000&encoding=pcm_s16le&speech_model=universal-3-6-pro&mode=balanced&token=…`, showing live partials. If streaming fails, it falls back to pre-recorded STT (`/v2/upload` → `/v2/transcript`).
4. The other side's LLM receives **only the STT transcript** of what was said, then replies, and so on.

## Layout

```
api/public/aai-chat.ts       LLM Gateway (gemma-4-31b, falls back to qwen3.5-4b-32k-fast)
api/public/aai-tts.ts        Text-to-speech
api/public/aai-stt-token.ts  Temporary streaming STT token
api/public/aai-stt.ts        Pre-recorded STT fallback
server/                      Shared server code (key handling, personas, handlers)
src/                         React + Vite + Tailwind v4 front end
```

## Run locally

```bash
cp .env.example .env     # then put your key in .env: ASSEMBLYAI_API_KEY=...
npm install
npm run dev              # http://localhost:5173 (the api/ routes are served by a Vite dev middleware)
```

Open the gear icon (top right), paste your cloned voice ID (slug or UUID from the AssemblyAI dashboard → Voices), and hit **Test my clone**. With no voice ID it falls back to the stock voice `aai-ewald`.

## Deploy (Vercel)

Import the repo in Vercel (framework: Vite) and add the environment variable `ASSEMBLYAI_API_KEY`. The key is read only by the serverless functions in `api/`; it is never shipped to the browser and never committed (`.env` is gitignored).

If the key is missing, every API route returns `500 {"error":"missing_server_secret"}` and the page shows a clear toast.

## Notes

- The LLM Gateway models available depend on your AssemblyAI account; the code uses `gemma-4-31b` and retries with `qwen3.5-4b-32k-fast` if the account has no access.
- Streaming STT sends audio no faster than real time, so each line's "hearing" takes about as long as the line itself; partials appear live in the bubble.
- Originally prototyped in Lovable; this repo is a standalone Vite + Vercel Functions version of the same app.
