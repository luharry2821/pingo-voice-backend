import { COUNTERPART_VOICES } from "./voices.js";
import { joinWav, splitByLanguage, wavPcm } from "./language.js";
import { z } from "zod";
import { CORS, json, resolveKey, NO_KEY, upstreamError } from "./aai-server.js";
import { chatInput, buildMessages } from "./personas.js";

export const preflight = () => new Response(null, { status: 204, headers: CORS });

// LLM Gateway: writes the next spoken line. Personas live only on the server.
export async function handleChat(request: Request): Promise<Response> {
  const key = resolveKey();
  if (!key) return json(NO_KEY, 500);
  let raw: unknown;
  try { raw = await request.json(); } catch { return json({ message: "Invalid JSON" }, 400); }
  const parsed = chatInput.safeParse(raw);
  if (!parsed.success) return json({ message: "Invalid call input", details: parsed.error.flatten() }, 400);
  const messages = buildMessages(parsed.data);
  const complete = (model: string) => fetch("https://llm-gateway.assemblyai.com/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: key, "Content-Type": "application/json" },
    body: JSON.stringify({ model, messages, max_tokens: 150, temperature: 0.8 }),
  });
  const run = async () => {
    let res = await complete("gemma-4-31b");
    if (res.status === 400 && /does not have access/i.test(await res.clone().text())) res = await complete("qwen3.5-4b-32k-fast");
    return res;
  };
  let res = await run();
  if (!res.ok) return upstreamError(res);
  let text: string = (await res.json())?.choices?.[0]?.message?.content ?? "";
  // Hinglish lines must stay in Roman letters: TTS can't speak Devanagari. Ask once more if the model slipped.
  if (parsed.data.scenarioId === "customer" && DEVANAGARI.test(text)) {
    messages.push({ role: "assistant", content: text }, { role: "user", content: "(Say that same line again, writing the Hindi in Roman letters only, no Devanagari. Reply with only the line.)" });
    const retry = await run();
    if (retry.ok) { const again: string = (await retry.json())?.choices?.[0]?.message?.content ?? ""; if (again && !DEVANAGARI.test(again)) text = again; }
  }
  return json({ text });
}

const DEVANAGARI = /[\u0900-\u097F]/;
const PASS = ["x-tts-voice-id", "x-tts-language", "x-tts-audio-duration-ms", "x-tts-voice-expires-at"];

// TTS (US zone, where cloned voices live). Returns WAV = 24 kHz 16-bit mono PCM + header.
export async function handleTts(request: Request): Promise<Response> {
  const key = resolveKey();
  if (!key) return json(NO_KEY, 500);
  let body: { text?: string; voice_id?: string; language?: string; counterpart?: string };
  try { body = await request.json(); } catch { return json({ message: "Invalid JSON" }, 400); }
  // A counterpart line uses the server-side voice for that scenario; Harry's line uses the voice_id sent.
  if (body.counterpart) {
    const fixed = COUNTERPART_VOICES[body.counterpart];
    if (!fixed) return json({ message: "Unknown counterpart" }, 400);
    body.voice_id = fixed;
  }
  if (!body.text || !body.voice_id) return json({ message: "text and voice_id required" }, 400);
  const synth = (text: string, language: string) => fetch("https://tts.us.assemblyai.com/v1/speech", {
    method: "POST",
    headers: { Authorization: key, "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice_id: body.voice_id, language, container: "wav", stream: false }),
  });
  const requested = (body.language || "english").toLowerCase();
  // "auto": code-switched line. TTS takes one language per request, so speak each language run in its own language.
  const segments = requested === "auto" ? splitByLanguage(body.text) : [{ text: body.text, language: requested }];
  if (segments.length === 1) {
    const res = await synth(segments[0]!.text, segments[0]!.language);
    if (!res.ok) return upstreamError(res);
    const headers: Record<string, string> = { ...CORS, "Content-Type": "audio/wav" };
    for (const h of PASS) { const v = res.headers.get(h); if (v) headers[h] = v; }
    return new Response(await res.arrayBuffer(), { status: 200, headers });
  }
  const responses = await Promise.all(segments.map((s) => synth(s.text, s.language)));
  const failed = responses.find((r) => !r.ok);
  if (failed) return upstreamError(failed);
  const audio = await Promise.all(responses.map(async (r) => wavPcm(await r.arrayBuffer())));
  const headers: Record<string, string> = { ...CORS, "Content-Type": "audio/wav", "x-tts-language": segments.map((s) => s.language).join("+") };
  const voice = responses[0]!.headers.get("x-tts-voice-id"); if (voice) headers["x-tts-voice-id"] = voice;
  return new Response(joinWav(audio.map((a) => a.pcm), audio[0]!.sampleRate), { status: 200, headers });
}

// Short-lived streaming token so the browser can open the STT WebSocket without the key.
export async function handleSttToken(_request: Request): Promise<Response> {
  const key = resolveKey();
  if (!key) return json(NO_KEY, 500);
  try {
    const res = await fetch("https://token.assemblyai.com/v1/token", {
      method: "POST",
      headers: { Authorization: key, "Content-Type": "application/json" },
      body: JSON.stringify({ product: "streaming", expires_in_seconds: 60, max_session_duration_seconds: 120 }),
    });
    if (!res.ok) return upstreamError(res);
    const data = z.object({ token: z.string().min(1).max(10000) }).parse(await res.json());
    return json({ token: data.token });
  } catch { return json({ message: "Could not start AssemblyAI listening." }, 502); }
}

// Fallback: pre-recorded STT on the line's WAV (upload -> transcript -> poll).
export async function handleStt(request: Request): Promise<Response> {
  const key = resolveKey();
  if (!key) return json(NO_KEY, 500);
  if (request.headers.get("content-type")?.split(";")[0] !== "audio/wav") return json({ message: "Expected audio/wav." }, 415);
  const headers = { Authorization: key };
  const deadline = Date.now() + 55000;
  // ?langs=en,es selects multilingual (code-switching) recognition for the Mom call.
  const langs = (new URL(request.url).searchParams.get("langs") || "en").split(",").filter((l) => /^[a-z]{2,3}$/.test(l)).slice(0, 4);
  const multilingual = langs.some((l) => l !== "en");
  try {
    const audio = await request.arrayBuffer();
    if (audio.byteLength < 44 || audio.byteLength > 4 * 1024 * 1024) return json({ message: "Invalid WAV size (maximum 4 MB)." }, 400);
    const view = new DataView(audio);
    if (view.getUint32(0) !== 0x52494646 || view.getUint32(8) !== 0x57415645) return json({ message: "Invalid WAV container." }, 400);
    const upload = await fetch("https://api.assemblyai.com/v2/upload", { method: "POST", headers: { ...headers, "Content-Type": "application/octet-stream" }, body: audio });
    if (!upload.ok) return upstreamError(upload);
    const { upload_url } = z.object({ upload_url: z.string().url() }).parse(await upload.json());
    const created = await fetch("https://api.assemblyai.com/v2/transcript", {
      method: "POST", headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(multilingual
        ? { audio_url: upload_url, speech_models: ["universal-3-5-pro", "universal-2"], language_codes: langs }
        : { audio_url: upload_url, speech_models: ["universal-3-pro", "universal-2"], language_code: "en" }),
    });
    if (!created.ok) return upstreamError(created);
    const { id } = z.object({ id: z.string().regex(/^[a-zA-Z0-9_-]+$/) }).parse(await created.json());
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 500));
      const poll = await fetch(`https://api.assemblyai.com/v2/transcript/${encodeURIComponent(id)}`, { headers });
      if (!poll.ok) return upstreamError(poll);
      const result = z.object({ status: z.string(), text: z.string().nullable().optional(), error: z.string().optional() }).parse(await poll.json());
      if (result.status === "completed") return json({ text: result.text ?? "" });
      if (result.status === "error") return json({ message: result.error || "AssemblyAI could not transcribe this line." }, 502);
    }
    return json({ message: "Speech recognition timed out." }, 504);
  } catch { return json({ message: "AssemblyAI speech recognition failed." }, 502); }
}
