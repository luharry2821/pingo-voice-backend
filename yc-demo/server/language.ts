// AssemblyAI TTS speaks one `language` per request (english, spanish, ...; no mixed/auto mode).
// For code-switched (Spanglish) lines we split the text into clauses, tag each clause English or
// Spanish, merge neighbours with the same language, and synthesize each run in its own language.
export type TtsLanguage = "english" | "spanish";
export type Segment = { text: string; language: TtsLanguage };

const ES = new Set(("que qué y pero porque por para con sin también cómo como cuándo cuando dónde donde aquí allá eso esto esta este ese esa " +
  "el la los las un una unos unas del al mi mis tu tus su sus tú yo él ella nosotros ustedes usted ellos " +
  "es está están estás estoy eres soy son somos era fue ser estar hay ya muy más menos bien mal nada todo todos siempre nunca " +
  "mijo mija hijo hija mamá mami papá papi ay dios mío mía verdad oye mira claro bueno pues entonces ahora luego mañana hoy " +
  "comiste comer comida come comes cena desayuno verduras vegetales sopa frijoles tortillas café " +
  "voy vas va vamos van tengo tienes tiene tenemos quiero quieres sé sabes sabe digas dime dices dijo hablas llamas llámame " +
  "gracias favor cariño corazón amor semana semanas días día visita vienes venir ven cuídate cuidate besos abrazo te lo le les se nos").split(/\s+/));
const EN = new Set(("the a an and but because so if then when where what why how who is are was were be been am i you he she we they it " +
  "my your his her our their me him us them this that these those to of in on at for with from about just really very " +
  "not don't can't won't i'm you're it's that's i'll i've do does did have has had will would can could should " +
  "eat eating ate food fine okay ok sure yes yeah know think love miss call calling busy work working company startup " +
  "promise week weeks visit come coming going go see good great tired mom honey").split(/\s+/));

function classify(clause: string): TtsLanguage | null {
  if (/[ñáéíóú¿¡]/i.test(clause)) return "spanish";
  let es = 0, en = 0;
  for (const raw of clause.toLowerCase().match(/[a-zñáéíóúü']+/g) ?? []) {
    if (ES.has(raw)) es++;
    if (EN.has(raw)) en++;
  }
  if (es === en) return null;
  return es > en ? "spanish" : "english";
}

export function splitByLanguage(text: string, fallback: TtsLanguage = "english"): Segment[] {
  // Clauses end at , ; : . ? ! and an opening ¿ / ¡ starts a new clause.
  const clauses = text.replace(/\s+/g, " ").trim().match(/[¿¡]?[^,;:.?!¿¡]+[,;:.?!]*/g) ?? [text];
  const out: Segment[] = [];
  let previous: TtsLanguage = fallback;
  for (const clause of clauses) {
    const trimmed = clause.trim(); if (!trimmed) continue;
    const language = classify(trimmed) ?? previous;
    const last = out[out.length - 1];
    if (last && last.language === language) last.text += ` ${trimmed}`; else out.push({ text: trimmed, language });
    previous = language;
  }
  return out.length ? out : [{ text, language: fallback }];
}

// --- WAV helpers (AssemblyAI returns 24 kHz, 16-bit mono PCM in a WAV container). ---
export function wavPcm(buffer: ArrayBuffer): { pcm: Uint8Array; sampleRate: number } {
  const view = new DataView(buffer); let offset = 12; let sampleRate = 24000;
  while (offset + 8 <= buffer.byteLength) {
    const id = String.fromCharCode(view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2), view.getUint8(offset + 3));
    const size = view.getUint32(offset + 4, true);
    if (id === "fmt ") sampleRate = view.getUint32(offset + 12, true);
    if (id === "data") return { pcm: new Uint8Array(buffer, offset + 8, Math.min(size, buffer.byteLength - offset - 8)), sampleRate };
    offset += 8 + size + (size % 2);
  }
  return { pcm: new Uint8Array(buffer, 44), sampleRate };
}
export function joinWav(parts: Uint8Array[], sampleRate: number, gapMs = 40): ArrayBuffer {
  const gap = Math.round(sampleRate * gapMs / 1000) * 2;
  const dataSize = parts.reduce((n, p) => n + p.length, 0) + gap * Math.max(0, parts.length - 1);
  const out = new ArrayBuffer(44 + dataSize); const v = new DataView(out); const bytes = new Uint8Array(out);
  const tag = (o: number, s: string) => { for (let i = 0; i < 4; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  tag(0, "RIFF"); v.setUint32(4, 36 + dataSize, true); tag(8, "WAVE"); tag(12, "fmt "); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); tag(36, "data"); v.setUint32(40, dataSize, true);
  let o = 44; parts.forEach((p, i) => { if (i) o += gap; bytes.set(p, o); o += p.length; });
  return out;
}
