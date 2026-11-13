import { FINAL_TIMEOUT_MS, FRAME_MS, FRAME_SAMPLES, HeardTurns, pcmFrame, type SttTurn } from './stt-audio';

// The listener's mic is the speaker's actual audio. We stream it 3x faster than real time so the
// transcript is final well before playback ends, which lets the next turn be prepared during playback.
export const STREAM_SPEED = 3;
import { friendlyError } from './scenarios';

export async function mintSttToken(signal: AbortSignal): Promise<string> {
  const response = await fetch('/api/public/aai-stt-token', { method: 'POST', signal });
  const body = await response.json();
  if (!response.ok) throw new Error(friendlyError(response.status, body));
  if (typeof body.token !== 'string' || !body.token) throw new Error('AssemblyAI did not return a listening token.');
  return body.token;
}

export function listenToLine(options: {
  pcm: Int16Array; wav: ArrayBuffer; signal: AbortSignal; token: Promise<string>; languages?: string[];
  onPartial: (text: string) => void;
}) {
  const { signal, pcm, wav, token, onPartial } = options;
  const languages = options.languages?.length ? options.languages : ['en'];
  let socket: WebSocket | undefined;
  let frameTimer: ReturnType<typeof setTimeout> | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  let formattedTimer: ReturnType<typeof setTimeout> | undefined;
  let openTimer: ReturnType<typeof setTimeout> | undefined;
  let settled = false;
  let fallbackStarted = false;
  let playbackFinished = false;
  let endpointSent = false;
  let begun = false;
  let startStreaming: () => void = () => {};
  let resolveReady: () => void = () => {};
  const ready = new Promise<void>((resolve) => { resolveReady = resolve; });
  const turns = new HeardTurns();
  let resolveResult: (result: { heard: string; fallback: boolean }) => void = () => {};
  let rejectResult: (error: unknown) => void = () => {};
  const result = new Promise<{ heard: string; fallback: boolean }>((resolve, reject) => { resolveResult = resolve; rejectResult = reject; });
  const close = () => {
    clearTimeout(frameTimer); clearTimeout(deadline); clearTimeout(formattedTimer); clearTimeout(openTimer);
    if (socket) {
      socket.onmessage = null; socket.onerror = null; socket.onclose = null;
      try {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'Terminate' }));
        socket.close();
      } catch { /* A failed connection is already closed. */ }
      socket = undefined;
    }
  };
  const finish = (heard: string, fallback: boolean) => {
    if (settled || signal.aborted) return;
    settled = true; close(); resolveReady(); signal.removeEventListener('abort', abort);
    resolveResult({ heard, fallback });
  };
  const abort = () => {
    if (settled) return;
    settled = true; close(); resolveReady(); signal.removeEventListener('abort', abort);
    rejectResult(new DOMException('Stopped', 'AbortError'));
  };
  const fallback = async () => {
    if (settled || fallbackStarted || signal.aborted) return;
    fallbackStarted = true; close(); resolveReady();
    try {
      const response = await fetch(`/api/public/aai-stt?langs=${encodeURIComponent(languages.join(','))}`, { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wav, signal });
      const body = await response.json();
      if (!response.ok) throw new Error(friendlyError(response.status, body));
      if (typeof body.text !== 'string' || !body.text.trim()) throw new Error('AssemblyAI did not hear speech in this line.');
      finish(body.text.trim(), true);
    } catch (error) {
      if (!settled) { settled = true; signal.removeEventListener('abort', abort); rejectResult(error); }
    }
  };
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  // The initial handshake is bounded, independently of the post-playback deadline.
  if (!settled) openTimer = setTimeout(() => void fallback(), 4000);
  void token.then((value) => {
    if (settled || fallbackStarted || signal.aborted) return;
    const params = new URLSearchParams({ sample_rate: '24000', encoding: 'pcm_s16le', speech_model: 'universal-3-6-pro', mode: 'balanced', token: value });
    // universal-3-6-pro code-switches natively; language_codes steers it toward the call's languages.
    params.set('language_codes', JSON.stringify(languages));
    socket = new WebSocket(`wss://streaming.assemblyai.com/v3/ws?${params}`);
    let started = false;
    let offset = 0;
    let silence = 0;
    let streamStart = 0;
    let sent = 0;
    // Time-based pacing: survives timer clamping in background tabs.
    const sendFrame = () => {
      if (!socket || socket.readyState !== WebSocket.OPEN || settled || fallbackStarted || endpointSent) return;
      const allowed = Math.floor((performance.now() - streamStart) / (FRAME_MS / STREAM_SPEED)) + 1;
      while (sent < allowed) {
        if (offset < pcm.length) { socket.send(pcmFrame(pcm, offset)); offset += FRAME_SAMPLES; }
        else if (silence < 4) { socket.send(pcmFrame(new Int16Array(0), 0)); silence++; }
        else { endpointSent = true; socket.send(JSON.stringify({ type: 'ForceEndpoint' })); return; }
        sent++;
      }
      frameTimer = setTimeout(sendFrame, FRAME_MS / STREAM_SPEED);
    };
    startStreaming = () => { if (started && begun && !streamStart) { streamStart = performance.now(); sendFrame(); } };
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'Begin' && !started) { started = true; clearTimeout(openTimer); resolveReady(); startStreaming(); }
        if (message.type === 'Turn' && Number.isInteger(message.turn_order) && typeof message.transcript === 'string') {
          turns.update(message as SttTurn); onPartial(turns.text());
          if (endpointSent && message.end_of_turn && turns.complete() && turns.text(true)) {
            clearTimeout(formattedTimer);
            // Allow adjacent final/formatting messages for additional turn orders to settle.
            formattedTimer = setTimeout(() => { if (turns.complete()) finish(turns.text(true), false); }, 150);
          }
        }
        if (message.type === 'Termination' && !settled) void fallback();
      } catch { void fallback(); }
    };
    socket.onerror = () => void fallback();
    socket.onclose = () => { if (!settled) void fallback(); };
  }).catch(() => void fallback());
  return {
    ready,
    result,
    // Called when playback of this line starts: the listener begins hearing it.
    begin: () => {
      if (begun) return; begun = true; startStreaming();
      // Bound the wait even if the socket never produced a final turn.
      if (!settled) deadline = setTimeout(() => void fallback(), (pcm.length / 24000) * 1000 / STREAM_SPEED + FINAL_TIMEOUT_MS + 4000);
    },
    playbackEnded: () => {
      if (playbackFinished || settled || fallbackStarted) return;
      playbackFinished = true;
      clearTimeout(deadline); deadline = setTimeout(() => void fallback(), FINAL_TIMEOUT_MS);
    },
  };
}
