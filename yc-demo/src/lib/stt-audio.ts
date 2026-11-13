export const SAMPLE_RATE = 24000;
export const FRAME_SAMPLES = 1200;
export const FRAME_MS = 50;
export const FINAL_TIMEOUT_MS = 4000;

// Convert decoded audio independently of the browser AudioContext's sample rate.
export function pcm24k(channel: Float32Array, sourceRate: number): Int16Array {
  const output = new Int16Array(Math.round(channel.length * SAMPLE_RATE / sourceRate));
  for (let i = 0; i < output.length; i++) {
    const position = i * sourceRate / SAMPLE_RATE;
    const left = Math.floor(position);
    const fraction = position - left;
    const value = (channel[left] ?? 0) * (1 - fraction) + (channel[left + 1] ?? channel[left] ?? 0) * fraction;
    const clamped = Math.max(-1, Math.min(1, value));
    output[i] = Math.round(clamped * (clamped < 0 ? 32768 : 32767));
  }
  return output;
}
export function pcmFrame(samples: Int16Array, offset: number): ArrayBuffer {
  const bytes = new ArrayBuffer(FRAME_SAMPLES * 2);
  const view = new DataView(bytes);
  for (let i = 0; i < FRAME_SAMPLES; i++) view.setInt16(i * 2, samples[offset + i] ?? 0, true);
  return bytes;
}
export type SttTurn = { type: 'Turn'; turn_order: number; transcript: string; end_of_turn: boolean; turn_is_formatted: boolean };
export class HeardTurns {
  private turns = new Map<number, SttTurn>();
  update(turn: SttTurn) { this.turns.set(turn.turn_order, turn); }
  text(finalOnly = false) {
    return [...this.turns.values()].sort((a, b) => a.turn_order - b.turn_order)
      .filter((turn) => !finalOnly || turn.end_of_turn).map((turn) => turn.transcript.trim()).filter(Boolean).join(' ');
  }
  complete() { return this.turns.size > 0 && [...this.turns.values()].every((turn) => turn.end_of_turn); }
}
