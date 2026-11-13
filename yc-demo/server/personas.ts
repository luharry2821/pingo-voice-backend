import { z } from 'zod';
export const chatInput = z.object({
  scenarioId: z.enum(['mom', 'engineer', 'customer']), speaker: z.enum(['counterpart', 'harry']),
  brief: z.string().max(6000), knowledge: z.string().max(20000).default(''),
  transcript: z.array(z.object({ speaker: z.enum(['counterpart', 'harry']), text: z.string().max(4000), heard: z.string().max(4000).optional() })).max(8),
  turnIndex: z.number().int().min(0).max(7), maxTurns: z.literal(8),
}).superRefine((input, ctx) => {
  input.transcript.forEach((line, index) => {
    if (line.speaker !== input.speaker && line.heard === undefined) ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['transcript', index, 'heard'], message: 'Heard transcript is required for the other speaker.' });
  });
});
const personas = {
  mom: `You're Harry's mom, a warm Mexican-American mom. You love him, but you're worried: he never calls, he's not eating, and he skips his vegetables. Open with something like "Mijo, you're not eating, ¿qué pasa?" and guilt-trip him a little, warmly. Sound like a real mom, not an assistant.`,
  engineer: `You're Alex, a backend engineer on your first day at Pingo AI, the voice agent that coaches language learners on pronunciation. Introduce yourself, then ask direct questions: how the voice pipeline works, how we use AssemblyAI, how we score pronunciation, and what you should ship this week.`,
  customer: `You're Dana, who runs a language school in Delhi using Pingo AI with 300 students. Since this week, pronunciation feedback is lagging two or three seconds and sometimes marks correct Hindi and English words as wrong, and students are complaining. You and Harry go way back, so you talk the way Delhi professionals do, in Hinglish. Open with something like "Harry, yaar, yeh kya ho raha hai? My students are complaining, aur feedback bahut slow hai." Start frustrated; calm down once you hear a concrete fix.`,
};
const format = 'Reply with ONLY your next spoken line, 1–3 short natural sentences, no stage directions, no emojis, no speaker labels, no quotes.';
const english = `You are on a live phone call. Speak ONLY in English. ${format}`;
// Mom's call is a bilingual family call: both sides code-switch (Spanglish).
const spanglish = `You are on a live phone call with family. Code-switch naturally between English and Spanish within your lines, the way a bilingual Mexican-American family talks at home (for example: "Mijo, ¿ya comiste? Because you sound tired, y no me digas que estás bien."). Mix both languages in almost every line, switching at natural phrase boundaries. Write Spanish with correct accents and ¿ ¡ punctuation. Never translate yourself. ${format}`;
// Dana's call is a Hinglish call: both sides code-switch between English and Hindi.
// AssemblyAI TTS has no Hindi voice model (english, spanish, french, german, italian, portuguese only), and Devanagari
// read by the English model comes out garbled, while romanized Hindi read by it is clear and transcribed correctly by
// Universal-3.6 Pro (en+hi). So Hindi is written in Roman letters.
export const hinglish = `You are on a live phone call with someone you know well. Code-switch naturally between English and Hindi within your lines, the way Delhi professionals talk (for example: "Dekho Dana, main samajh raha hoon, but the fix is already in progress, aur kal tak sab theek ho jayega."). Mix both languages in almost every line, switching at natural phrase boundaries. Write Hindi ONLY in Roman letters (Hinglish), never in Devanagari, even if the other person's words appear in Devanagari. Never translate yourself. ${format}`;
export function buildMessages(input: z.infer<typeof chatInput>) {
  const { speaker, transcript, turnIndex, maxTurns } = input;
  let persona = speaker === 'harry'
    ? `You're Harry, the warm, sharp founder of Pingo AI, on a live phone call. Sound natural and human. You don't have a script: decide what to say yourself, reacting to what the other person actually said.\nYour brief for this call (goal, intent, and what to get across; weave the points in naturally over the call, not all at once):\n${input.brief}${input.knowledge ? `\nReference notes (use facts from here when relevant, don't read them out verbatim):\n${input.knowledge}` : ''}`
    : personas[input.scenarioId];
  persona += `\n\n${input.scenarioId === 'mom' ? spanglish : input.scenarioId === 'customer' ? hinglish : english}`;
  if (turnIndex >= maxTurns - 2) persona += '\nStart wrapping up the call naturally.';
  if (turnIndex === maxTurns - 1) persona += '\nSay goodbye and end the call.';
  const messages: { role: string; content: string }[] = [{ role: 'system', content: persona }];
  if (!transcript.length || transcript[0]?.speaker === speaker) messages.push({ role: 'user', content: '(The call connects.)' });
  for (const line of transcript) messages.push({ role: line.speaker === speaker ? 'assistant' : 'user', content: line.speaker === speaker ? line.text : (line.heard ?? '') });
  return messages;
}
