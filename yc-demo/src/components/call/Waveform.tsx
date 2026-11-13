import { useEffect, useRef } from 'react';
import type { Line } from '@/lib/scenarios';
// Live audio visualizer between the two avatars. Driven by the AnalyserNode on the playing audio:
// each frame the current loudness enters at the speaker's side and flows across toward the listener,
// with a live oscilloscope trace of the actual waveform on top.
export function Waveform({ analyser, speaker }: { analyser: AnalyserNode | null; speaker: Line['speaker'] | null }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const speakerRef = useRef(speaker);
  speakerRef.current = speaker;
  useEffect(() => {
    const canvas = ref.current; if (!canvas) return;
    const ctx = canvas.getContext('2d'); if (!ctx) return;
    // Theme colors are re-read when light/dark mode changes.
    const readColors = () => {
      const style = getComputedStyle(document.documentElement);
      return { harry: style.getPropertyValue('--primary').trim() || '#3923C7', counterpart: style.getPropertyValue('--warm').trim() || '#D9822B', idle: style.getPropertyValue('--tint-medium').trim() || '#b8b0e8' };
    };
    let colors = readColors();
    const themeObserver = new MutationObserver(() => { colors = readColors(); });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const COLS = 96;
    const levels = new Array<number>(COLS).fill(0);
    const owners = new Array<Line['speaker'] | null>(COLS).fill(null);
    const time = new Uint8Array(analyser?.fftSize ?? 1024);
    let smooth = 0, raf = 0, phase = 0, w = 0, h = 0, last = performance.now(), carry = 0;
    const COLS_PER_SECOND = 80; // energy crosses the gap in ~1.2 s, independent of frame rate
    const resize = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const rect = canvas.getBoundingClientRect();
      w = Math.max(1, rect.width); h = Math.max(1, rect.height);
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(resize); observer.observe(canvas);
    const draw = () => {
      const who = speakerRef.current;
      let level = 0;
      if (who && analyser) {
        analyser.getByteTimeDomainData(time);
        let sum = 0; for (let i = 0; i < time.length; i++) { const v = (time[i]! - 128) / 128; sum += v * v; }
        level = Math.min(1, Math.sqrt(sum / time.length) * 5.5);
      }
      smooth += (level - smooth) * (level > smooth ? 0.55 : 0.18);
      // Advance the flow: new energy enters at the speaker's edge (index 0) and moves toward the listener.
      const now = performance.now(); const dt = Math.min(0.25, (now - last) / 1000); last = now;
      carry += dt * COLS_PER_SECOND;
      while (carry >= 1) { carry -= 1; levels.pop(); levels.unshift(who ? smooth : 0); owners.pop(); owners.unshift(who); }
      phase += dt * 3.5;
      ctx.clearRect(0, 0, w, h);
      const mid = h / 2, gap = w / COLS, barW = Math.max(2, gap * 0.55);
      // Baseline (dotted when idle).
      ctx.globalAlpha = 0.5; ctx.fillStyle = colors.idle;
      for (let i = 0; i < COLS; i += 2) ctx.fillRect(i * gap, mid - 0.75, barW, 1.5);
      // Flowing mirrored bars.
      for (let i = 0; i < COLS; i++) {
        const owner = owners[i]; const raw = levels[i] ?? 0;
        const idleWave = (Math.sin(phase + i * 0.35) * 0.5 + 0.5) * 0.04;
        const amp = reduced ? (who ? 0.25 : 0) : Math.max(raw, owner ? 0 : idleWave);
        if (amp < 0.01) continue;
        const fromLeft = owner !== 'harry';
        const x = (fromLeft ? i : COLS - 1 - i) * gap + (gap - barW) / 2;
        const travel = i / COLS; // 0 at the speaker, 1 at the listener
        const bh = Math.max(2, amp * (h * 0.46) * (1 - travel * 0.35));
        ctx.globalAlpha = owner ? 0.95 - travel * 0.55 : 0.35;
        ctx.fillStyle = owner ? colors[owner] : colors.idle;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(x, mid - bh, barW, bh * 2, barW / 2); else ctx.rect(x, mid - bh, barW, bh * 2);
        ctx.fill();
      }
      // Live oscilloscope trace of the actual samples, glowing in the speaker's color.
      if (who && analyser && !reduced) {
        ctx.globalAlpha = 0.75; ctx.strokeStyle = colors[who]; ctx.lineWidth = 1.75;
        ctx.shadowColor = colors[who]; ctx.shadowBlur = 12;
        ctx.beginPath();
        const step = Math.max(1, Math.floor(time.length / 160));
        for (let j = 0, n = 0; j < time.length; j += step, n++) {
          const t = j / (time.length - 1);
          const x = (who === 'harry' ? 1 - t : t) * w;
          const fade = 1 - t * 0.6;
          const y = mid + ((time[j]! - 128) / 128) * h * 0.36 * fade;
          if (n === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke(); ctx.shadowBlur = 0;
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); observer.disconnect(); themeObserver.disconnect(); };
  }, [analyser]);
  return <canvas role="img" aria-label={speaker ? `Live audio flowing ${speaker === 'harry' ? 'from Harry to the caller' : 'from the caller to Harry'}` : 'Audio visualizer, idle'} ref={ref} className="block h-full w-full" />;
}
export function SpeakerRing({ analyser, active }: { analyser: AnalyserNode | null; active: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const element = ref.current; if (!element || !active || !analyser) return;
    const data = new Uint8Array(analyser.fftSize);
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let raf = 0;
    const draw = () => {
      analyser.getByteTimeDomainData(data);
      const level = Math.min(1, Math.sqrt(data.reduce((s, n) => s + ((n - 128) / 128) ** 2, 0) / data.length) * 5);
      element.style.setProperty('--audio-level', String(reduced ? 0.4 : level));
      raf = requestAnimationFrame(draw);
    };
    draw(); return () => { cancelAnimationFrame(raf); element.style.removeProperty('--audio-level'); };
  }, [analyser, active]);
  return <span ref={ref} aria-hidden="true" className={`audio-ring ${active ? 'is-active' : ''}`} />;
}
