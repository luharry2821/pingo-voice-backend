export const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type, authorization",
  "Access-Control-Expose-Headers":
    "x-tts-voice-id, x-tts-language, x-tts-audio-duration-ms, x-tts-voice-expires-at",
};
export function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}
export function resolveKey(): string | null {
  // ASSEMBLYAI_API_KEY is the canonical name; "assembly" is accepted as a fallback alias.
  return (process.env["ASSEMBLYAI_API_KEY"] || process.env["assembly"] || "").trim() || null;
}
export const NO_KEY = {
  error: "missing_server_secret",
  message: "ASSEMBLYAI_API_KEY is not set on the server. Add it as a project secret.",
};
export async function upstreamError(res: Response) {
  const text = await res.text();
  let body: unknown;
  try { body = JSON.parse(text); } catch { body = { error: "upstream_error", message: text || res.statusText }; }
  return json(body, res.status);
}
