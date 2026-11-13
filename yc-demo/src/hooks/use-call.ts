import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { FOUNDER, cleanLine, friendlyError, MAX_TURNS, type Line, type Scenario, type HarryBrief } from "@/lib/scenarios";
import { pcm24k } from '@/lib/stt-audio';
import { listenToLine, mintSttToken } from '@/lib/stt-stream';

export type SpokenLine = Line & { id: number; buffer: AudioBuffer; final: boolean; fallback: boolean; startedAt: number; duration: number };
export type TurnTiming = { turn: number; speaker: Line['speaker']; voiceId: string; durationMs: number; gapMs: number | null; sttFinalAfterStartMs: number | null; said?: string; heard?: string };
const sleep = (ms: number, signal: AbortSignal) => new Promise<void>((resolve) => { const t = setTimeout(resolve, ms); signal.addEventListener('abort', () => { clearTimeout(t); resolve(); }, { once: true }); });
async function callApi(path: string, body: unknown, signal: AbortSignal) {
  const res = await fetch(path, { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) {
    let error: unknown = null;
    try { error = await res.json(); } catch { /* No JSON body. */ }
    throw new Error(friendlyError(res.status, error));
  }
  return res;
}

export function useCall() {
  const [lines, setLines] = useState<SpokenLine[]>([]);
  const [speaker, setSpeaker] = useState<Line["speaker"] | null>(null);
  const [preparing, setPreparing] = useState<Line["speaker"] | null>(null);
  const [listening, setListening] = useState<Line['speaker'] | null>(null);
  const [status, setStatus] = useState("Ready to call");
  const [running, setRunning] = useState(false);
  const testing = false;
  const [ended, setEnded] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const startedAt = useRef(0);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt.current) / 1000)), 500);
    return () => clearInterval(timer);
  }, [running]);

  const audio = () => {
    let ctx = ctxRef.current;
    if (!ctx) {
      ctx = new AudioContext();
      ctxRef.current = ctx;
      const node = ctx.createAnalyser(); node.fftSize = 1024; node.smoothingTimeConstant = 0.6; node.connect(ctx.destination);
      analyserRef.current = node; setAnalyser(node);
    }
    void ctx.resume();
    return ctx;
  };
  const stopAudio = useCallback(() => {
    try { sourceRef.current?.stop(); } catch { /* Already stopped. */ }
    sourceRef.current = null;
  }, []);
  const stop = useCallback(() => {
    abortRef.current?.abort(); abortRef.current = null; stopAudio();
    setRunning(false); setSpeaker(null); setPreparing(null); setListening(null); setLines((prev) => prev.map((line) => ({ ...line, final: true }))); setEnded(true); setStatus("Call ended");
  }, [stopAudio]);
  useEffect(() => () => {
    abortRef.current?.abort(); stopAudio(); void ctxRef.current?.close();
  }, [stopAudio]);

  const play = (buffer: AudioBuffer, signal: AbortSignal) => new Promise<void>((resolve) => {
    if (signal.aborted) { resolve(); return; }
    const ctx = audio(); const node = analyserRef.current;
    if (!node) { resolve(); return; }
    stopAudio();
    const source = ctx.createBufferSource(); source.buffer = buffer; source.connect(node);
    const finish = () => { signal.removeEventListener("abort", cancel); resolve(); };
    const cancel = () => { try { source.stop(); } catch { /* Already ended. */ } finish(); };
    source.onended = finish; signal.addEventListener("abort", cancel, { once: true });
    sourceRef.current = source; source.start();
  });
  // voice_id is Harry's saved clone; counterpart lines pass the scenario id and the server picks the voice.
  const tts = async (text: string, voice: { voice_id: string } | { counterpart: string }, signal: AbortSignal, label?: string, language: 'auto' | 'english' = 'english') => {
    let response: Response;
    const shown = "voice_id" in voice ? voice.voice_id : `${voice.counterpart} default`;
    try { response = await callApi("/api/public/aai-tts", { text, ...voice, language }, signal); }
    catch (error) { if (signal.aborted || !label) throw error; throw new Error(`${label} voice "${shown}" failed. ${(error as Error).message}`); }
    const usedVoice = response.headers.get("x-tts-voice-id") ?? shown;
    const wav = await response.arrayBuffer();
    const buffer = await audio().decodeAudioData(wav.slice(0));
    signal.throwIfAborted(); return { buffer, wav, usedVoice };
  };

  const start = async (scenario: Scenario, brief: HarryBrief, knowledge: string, voiceId: string) => {
    stop(); audio();
    const controller = new AbortController(); abortRef.current = controller;
    const { signal } = controller;
    setLines([]); setElapsed(0); startedAt.current = Date.now(); setRunning(true); setEnded(false);
    const voices = { counterpart: { counterpart: scenario.id }, harry: { voice_id: voiceId } };
    const transcript: Line[] = [];
    const timings: TurnTiming[] = [];
    (window as unknown as { __ycTimings?: TurnTiming[] }).__ycTimings = timings;
    const chat = async (body: unknown) => {
      for (let attempt = 0; ; attempt++) {
        try { return (await (await callApi("/api/public/aai-chat", body, signal)).json()) as { text?: string }; }
        catch (error) { if (signal.aborted || attempt >= 1) throw error; }
      }
    };
    // LLM -> TTS -> open the listener's STT socket, all before this line is needed.
    const prepare = async (index: number) => {
      const who = index % 2 === 0 ? "counterpart" as const : "harry" as const;
      const { text } = await chat({ scenarioId: scenario.id, speaker: who, brief, knowledge, transcript: [...transcript], turnIndex: index, maxTurns: MAX_TURNS });
      signal.throwIfAborted();
      const line = cleanLine(text || "") || "…";
      const { buffer, wav, usedVoice } = await tts(line, voices[who], signal, who === "harry" ? `${FOUNDER}'s` : `${scenario.counterpartName}'s`, scenario.ttsLanguage);
      signal.throwIfAborted();
      const recognition = listenToLine({
        pcm: pcm24k(buffer.getChannelData(0), buffer.sampleRate), wav, signal, token: mintSttToken(signal), languages: scenario.sttLanguages,
        onPartial: (heard) => { if (!signal.aborted) setLines((prev) => prev.map((item) => item.id === index ? { ...item, heard } : item)); },
      });
      void recognition.result.catch(() => {});
      return { who, line, buffer, recognition, usedVoice };
    };
    try {
      setStatus(`${scenario.counterpartName} is getting ready`); setPreparing("counterpart");
      let pending = prepare(0); void pending.catch(() => {});
      let lastEnd = 0;
      for (let i = 0; i < MAX_TURNS; i++) {
        const item = await pending; signal.throwIfAborted();
        const listener = item.who === 'harry' ? 'counterpart' : 'harry';
        const begin = performance.now();
        const timing: TurnTiming = { turn: i, speaker: item.who, voiceId: item.usedVoice, durationMs: Math.round(item.buffer.duration * 1000), gapMs: lastEnd ? Math.round(begin - lastEnd) : null, sttFinalAfterStartMs: null };
        timings.push(timing);
        setPreparing(null);
        setLines((prev) => [...prev, { id: i, speaker: item.who, text: item.line, heard: '', buffer: item.buffer, final: false, fallback: false, startedAt: begin, duration: item.buffer.duration }]);
        setListening(listener); setSpeaker(item.who); setStatus(`${item.who === "harry" ? FOUNDER : scenario.counterpartName} is speaking`);
        item.recognition.begin();
        const playback = play(item.buffer, signal).then(() => { lastEnd = performance.now(); item.recognition.playbackEnded(); if (!signal.aborted) setSpeaker(null); });
        const heard = await item.recognition.result; signal.throwIfAborted();
        timing.sttFinalAfterStartMs = Math.round(performance.now() - begin); timing.said = item.line; timing.heard = heard.heard;
        transcript.push({ speaker: item.who, text: item.line, heard: heard.heard });
        setLines((prev) => prev.map((line) => line.id === i ? { ...line, ...heard, final: true } : line));
        // Prefetch the next turn while this line is still playing.
        if (i + 1 < MAX_TURNS) { pending = prepare(i + 1); void pending.catch(() => {}); }
        await playback; signal.throwIfAborted();
        setListening(null);
        console.info(`[yc] turn ${i + 1} ${item.who} (${timing.voiceId}) ${timing.durationMs}ms audio · gap before ${timing.gapMs ?? '-'}ms · STT final at +${timing.sttFinalAfterStartMs}ms`);
        if (i + 1 < MAX_TURNS) {
          setPreparing(listener); setStatus(`${listener === 'harry' ? FOUNDER : scenario.counterpartName} is replying`);
          await sleep(180, signal); // a natural breath between speakers
        }
      }
      setRunning(false); setPreparing(null); setEnded(true); setStatus("Call ended");
      toast.success("Call ended. Your clone handled it.");
      abortRef.current = null;
    } catch (error) {
      if (signal.aborted) return;
      controller.abort(); stopAudio(); setRunning(false); setSpeaker(null); setPreparing(null); setListening(null); setLines((prev) => prev.map((line) => ({ ...line, final: true }))); setStatus("Ready to call");
      toast.error((error as Error).message, { duration: 12000 });
    }
  };
  // Validate a voice ID without playing anything.
  const checkVoice = async (voiceId: string) => {
    const controller = new AbortController();
    try { await callApi("/api/public/aai-tts", { text: "Hi.", voice_id: voiceId, language: "english" }, controller.signal); return null; }
    catch (error) { return (error as Error).message; }
  };
  const replay = async (line: SpokenLine) => {
    abortRef.current?.abort();
    const controller = new AbortController(); abortRef.current = controller;
    setSpeaker(line.speaker); await play(line.buffer, controller.signal);
    if (abortRef.current === controller) { setSpeaker(null); abortRef.current = null; }
  };
  const clear = () => { stop(); setLines([]); setElapsed(0); setEnded(false); setStatus("Ready to call"); };
  return { lines, speaker, preparing, listening, status, running, testing, ended, elapsed, analyser, start, stop, checkVoice, replay, clear };
}
