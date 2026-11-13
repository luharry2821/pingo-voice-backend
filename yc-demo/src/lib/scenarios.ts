export type HarryBrief = string;
export type Scenario = {
  id: 'mom' | 'engineer' | 'customer'; title: string; language: 'english';
  counterpartName: string;
  // TTS: 'auto' splits a code-switched line by language; STT: languages steer streaming recognition.
  ttsLanguage: 'auto' | 'english'; sttLanguages: string[]; brief: HarryBrief;
};
export const BRIEF_STORAGE_KEY = 'cmi.harry-brief.v7';
export const FALLBACK_VOICE = 'aai-ewald';
export const FOUNDER = 'Harry';
export const COMPANY = 'PINGO AI';
export const MODEL = 'gemma-4-31b';
export const MAX_TURNS = 8;
export const SCENARIOS: Scenario[] = [
  { id: 'mom', title: 'Mom', language: 'english', counterpartName: 'Mom', ttsLanguage: 'auto', sttLanguages: ['en', 'es'], brief: `# Goal\nCalm Mom down and lock in a visit.\n\n# Intent\nWarm, playful, patient — a loving son, not a founder. Code-switch naturally between English and Spanish, the way we talk at home (Spanglish).\n\n# What to get across\nI'm eating fine and I'll actually eat my vegetables. Pingo is going well: it helps people learn to say words in other languages. I'll visit in two weeks.` },
  { id: 'engineer', title: 'Alex · New engineer', language: 'english', counterpartName: 'Alex', ttsLanguage: 'english', sttLanguages: ['en'], brief: `# Goal\nGet Alex excited and productive on day one.\n\n# Intent\nEnergetic, clear, founder-to-teammate.\n\n# What to get across\nPingo AI is a voice agent that teaches learners how to say words and phrases in another language. Stack: AssemblyAI streaming STT hears the learner, the LLM Gateway coaches, AssemblyAI TTS speaks back. First-week goal: cut feedback latency under 500 ms.` },
  { id: 'customer', title: 'Dana · Customer', language: 'english', counterpartName: 'Dana', ttsLanguage: 'english', sttLanguages: ['en', 'hi'], brief: `# Goal\nTurn Dana from frustrated to confident.\n\n# Intent\nCalm, accountable, concrete. We go way back, so talk the way we always do: Hinglish, mixing English and Hindi naturally.\n\n# What to get across\nAsk one sharp question first. Likely cause: a latency regression plus strict accent thresholds on Hindi and English words. Workaround today, full fix within 48 hours, and a free month for the school.` },
];
export function cleanLine(text: string) {
  let t = text.replace(/\*[^*]*\*/g, ' ');
  while (/\([^()]*\)/.test(t)) t = t.replace(/\([^()]*\)/g, ' ');
  let previous = '';
  while (previous !== t) {
    previous = t;
    t = t.trim().replace(/^["“”‘’']+|["“”‘’']+$/g, '').trim();
    t = t.replace(/^\s*(?:Harry|Mom|Alex|Dana)\s*:\s*/i, '');
    t = t.replace(/^\s*[A-Za-zÀ-ÿ .'-]{1,40}\s*:\s*/, '');
  }
  return t.replace(/\s+/g, ' ').trim();
}
export type Line = { speaker: 'counterpart' | 'harry'; text: string; heard: string };
export function friendlyError(status: number, body: any): string {
  const msg = body?.message || body?.error?.message || body?.detail || (typeof body?.error === 'string' ? body.error : '');
  const s = JSON.stringify(body ?? '');
  if (status === 401) return 'Check your API key (401 Unauthorized).';
  if (/voice_not_found/i.test(s)) return `AssemblyAI: ${msg || 'voice not found on this account/region.'}`;
  if (/missing_server_secret/.test(s)) return msg;
  return `Error ${status}${msg ? `: ${msg}` : ''}`;
}
