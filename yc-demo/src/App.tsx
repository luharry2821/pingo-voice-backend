import { useEffect, useRef, useState } from "react";
import { Settings, Sun, Moon, Phone, PhoneOff, Play, RotateCcw, Paperclip, X, Ear, Mic, ChevronDown, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Waveform, SpeakerRing } from "@/components/call/Waveform";
import { useCall, type SpokenLine } from "@/hooks/use-call";
import { FALLBACK_VOICE, FOUNDER, SCENARIOS, BRIEF_STORAGE_KEY, type HarryBrief } from "@/lib/scenarios";

import { toast } from "sonner";
import { Toaster } from "@/components/ui/sonner";
import { useTheme } from "@/lib/theme";
import { DayCalendar } from "@/components/call/DayCalendar";
import { extractDocument, KNOWLEDGE_STORAGE_KEY, KNOWLEDGE_LIMIT, type Attachment } from "@/lib/knowledge";


export default function Index() {
  const [voiceId, setVoiceId] = useState("");
  const [voiceDraft, setVoiceDraft] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { theme, toggle: toggleTheme } = useTheme();
  const [briefs, setBriefs] = useState<Record<string, HarryBrief>>({});
  const [scenarioId, setScenarioId] = useState("mom");
  const [loaded, setLoaded] = useState(false);
  const [attachments, setAttachments] = useState<Record<string, Attachment>>({});
  const [extracting, setExtracting] = useState(false);
  const [saidOpen, setSaidOpen] = useState<Record<number, boolean>>({});
  const fileRef = useRef<HTMLInputElement>(null);
  const transcriptEnd = useRef<HTMLDivElement>(null);
  const call = useCall();
  useEffect(() => { transcriptEnd.current?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "nearest" }); }, [call.lines, call.preparing]);
  useEffect(() => { if (call.lines.length === 0) setSaidOpen({}); }, [call.lines.length]);
  const scenario = SCENARIOS.find((item) => item.id === scenarioId) ?? SCENARIOS[0];
  useEffect(() => {
    try {
      // Remove the old object entirely; never read its credential or prompt values.
      localStorage.removeItem("cmi.settings"); localStorage.removeItem("cmi.prompts"); localStorage.removeItem("cmi.situations"); localStorage.removeItem("cmi.speaker-prompts.v2"); localStorage.removeItem("cmi.harry-goals.v3"); localStorage.removeItem("cmi.harry-brief.v4"); localStorage.removeItem("cmi.harry-brief.v5"); localStorage.removeItem("cmi.harry-brief.v6");
      const savedVoice = (localStorage.getItem("cmi.voice-id") ?? "").trim(); setVoiceId(savedVoice); setVoiceDraft(savedVoice);
      const saved: unknown = JSON.parse(localStorage.getItem(BRIEF_STORAGE_KEY) ?? "{}");
      if (saved && typeof saved === "object" && !Array.isArray(saved)) {
        const valid: Record<string, HarryBrief> = {};
        for (const item of SCENARIOS) {
          const value = (saved as Record<string, unknown>)[item.id];
          if (typeof value === "string" && value.length <= 6000) valid[item.id] = value;
        }
        setBriefs(valid);
      }
      const docs = JSON.parse(localStorage.getItem(KNOWLEDGE_STORAGE_KEY) ?? "{}");
      if (docs && typeof docs === "object") {
        const valid: Record<string, Attachment> = {};
        for (const item of SCENARIOS) {
          const doc = docs[item.id];
          if (doc && typeof doc.name === "string" && typeof doc.text === "string" && doc.text.length <= KNOWLEDGE_LIMIT) valid[item.id] = { name: doc.name, text: doc.text, truncated: Boolean(doc.truncated) };
        }
        setAttachments(valid);
      }
    } catch { /* Storage may be unavailable. */ }
    setLoaded(true);
  }, []);
  useEffect(() => {
    if (!loaded) return;
    try { localStorage.setItem("cmi.voice-id", voiceId); localStorage.setItem(BRIEF_STORAGE_KEY, JSON.stringify(briefs)); localStorage.setItem(KNOWLEDGE_STORAGE_KEY, JSON.stringify(attachments)); } catch { /* Storage may be unavailable. */ }
  }, [loaded, voiceId, briefs, attachments]);
  if (!scenario) return null;
  const brief = briefs[scenario.id] ?? scenario.brief;
  const updateBrief = (value: string) => setBriefs((prev) => ({ ...prev, [scenario.id]: value }));
  const attachment = attachments[scenario.id];
  const founderVoice = voiceId.trim() || FALLBACK_VOICE;
  const busy = call.running || call.testing;
  const attach = async (file: File) => {
    const id = scenario.id; setExtracting(true);
    try { const doc = await extractDocument(file); setAttachments((prev) => ({ ...prev, [id]: doc })); }
    catch (error) { toast.error(`Could not read document: ${(error as Error).message}`); }
    finally { setExtracting(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const saveVoice = (event: React.FormEvent) => {
    event.preventDefault();
    const id = voiceDraft.trim().slice(0, 200);
    setVoiceId(id); setVoiceDraft(id);
    try { localStorage.setItem("cmi.voice-id", id); } catch { /* Storage may be unavailable. */ }
    setSettingsOpen(false);
    if (!id) { toast.success(`Cleared. Harry will speak as ${FALLBACK_VOICE}.`); return; }
    toast.success(`Saved ✓ Harry will speak as ${id}`);
    // Check the ID quietly (nothing is played) so a bad ID surfaces now, not mid-call.
    void call.checkVoice(id).then((problem) => { if (problem) toast.error(`Voice ID "${id}" didn't work with AssemblyAI TTS. ${problem}`, { duration: 15000 }); });
  };
  const timer = `${String(Math.floor(call.elapsed / 60)).padStart(2, "0")}:${String(call.elapsed % 60).padStart(2, "0")}`;

  return (
    <div className="min-h-screen bg-background font-sans text-foreground">
      <header className="mx-auto flex h-24 max-w-6xl items-center justify-between px-6 sm:px-10">
        <a href="/" className="font-display text-2xl">Harry</a>
        <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-foreground" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} title={theme === "dark" ? "Light mode" : "Dark mode"}>
          {theme === "dark" ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}
        </Button>
        <Popover open={settingsOpen} onOpenChange={(open) => { setSettingsOpen(open); if (open) setVoiceDraft(voiceId); }}>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon" className="relative text-muted-foreground" aria-label="Voice settings" title="Voice settings">
              <Settings className="size-[18px]" />
              {!voiceId && <span className="absolute right-1.5 top-1.5 size-1.5 rounded-full bg-primary" />}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" sideOffset={12} className="w-[320px] max-w-[calc(100vw-32px)] shadow-none">
            <form onSubmit={saveVoice} className="space-y-3">
              <Label htmlFor="voice-id" className="text-xs">Harry's cloned voice ID</Label>
              <Input id="voice-id" value={voiceDraft} onChange={(event) => setVoiceDraft(event.target.value)} placeholder="Paste your AssemblyAI voice ID" disabled={busy} autoComplete="off" spellCheck={false} className="font-mono text-xs" />
              <p className="text-[11px] leading-4 text-muted-foreground">{voiceId ? <span className="flex items-center gap-1"><Check className="size-3 text-primary" />Saved: <code className="truncate font-mono text-foreground">{voiceId}</code></span> : <>Not set. Harry uses {FALLBACK_VOICE}.</>}</p>
              <Button type="submit" size="sm" disabled={busy || voiceDraft.trim() === voiceId} className="w-full">Save voice ID</Button>
            </form>
          </PopoverContent>
        </Popover>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-5">
        <section className="pb-8 pt-6 text-center sm:pb-12 sm:pt-16">
          <h1 className="mx-auto max-w-3xl font-display text-[40px] font-normal leading-[1.1] sm:text-[64px]">I'm a solo YC founder building a company.</h1>
          <p className="mt-5 text-sm leading-relaxed text-body sm:text-base">Three calls today. My AssemblyAI voice clone is taking them.</p>
          <DayCalendar selected={scenarioId} disabled={busy} onSelect={(id) => { call.clear(); setScenarioId(id); }} />
        </section>
        <section aria-label="Call scenarios" className="text-center">
          <div className="flex items-center justify-center gap-2 sm:gap-3">
            {SCENARIOS.map((item) => <Button key={item.id} variant="ghost" disabled={busy} aria-pressed={item.id === scenarioId} onClick={() => { call.clear(); setScenarioId(item.id); }} className={`h-10 rounded-none border-b px-3 font-mono text-[11px] uppercase sm:px-5 ${item.id === scenarioId ? "border-primary text-primary" : "border-transparent text-muted-foreground"}`}>{item.title}</Button>)}
          </div>
          <div className="mx-auto mt-8 max-w-[640px] text-left">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-medium">Harry's brief</h2>
              <Button variant="ghost" size="icon" className="size-6 text-muted-foreground" disabled={busy || briefs[scenario.id] === undefined} aria-label="Reset Harry's brief" title="Reset Harry's brief" onClick={() => setBriefs((prev) => { const next = { ...prev }; delete next[scenario.id]; return next; })}><RotateCcw className="size-3" /></Button>
            </div>
            <PromptBox id="harry-brief" value={brief} disabled={busy} onChange={updateBrief} />
            <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
              {attachment && <div className="mr-auto flex min-w-0 max-w-full items-center gap-2 rounded border border-border px-2 py-1 text-[11px] text-body">
                <Paperclip className="size-3 shrink-0" /><span className="max-w-40 truncate" title={attachment.name}>{attachment.name}</span><span className="shrink-0 text-muted-foreground">{attachment.text.length.toLocaleString()} chars{attachment.truncated ? " · truncated" : ""}</span>
                <Button variant="ghost" size="icon" className="size-5 shrink-0" disabled={busy} aria-label="Remove document" title="Remove document" onClick={() => setAttachments((prev) => { const next = { ...prev }; delete next[scenario.id]; return next; })}><X className="size-3" /></Button>
              </div>}
              <input ref={fileRef} type="file" accept=".pdf,.txt,.md,.docx" className="hidden" aria-label="Knowledge document" onChange={(event) => { const file = event.target.files?.[0]; if (file) void attach(file); }} />
              <Button variant="ghost" size="sm" disabled={busy || extracting} onClick={() => fileRef.current?.click()} className="h-8 text-xs text-muted-foreground"><Paperclip className="size-3" />{extracting ? "Reading…" : "Attach doc"}</Button>
            </div>
          </div>
        </section>
        <section aria-label="Call stage" className="call-stage relative mx-auto pb-10 pt-10 sm:pt-16">
          <div className="relative mx-auto grid max-w-[780px] grid-cols-2 gap-12 sm:gap-24">
            <div className="stage-ribbon"><Waveform analyser={call.analyser} speaker={call.speaker} /></div>
            <Avatar name={scenario.counterpartName} label={scenario.title} initial={scenario.counterpartName.slice(0, 1)} active={call.speaker === "counterpart"} listening={call.listening === 'counterpart'} dimmed={call.speaker !== null && call.speaker !== "counterpart"} analyser={call.analyser} />
            <Avatar name={FOUNDER} label={voiceId ? `voice clone · ${voiceId.length > 18 ? `${voiceId.slice(0, 16)}…` : voiceId}` : `stock voice · ${FALLBACK_VOICE}`} initial="H" cloned active={call.speaker === "harry"} listening={call.listening === 'harry'} dimmed={call.speaker !== null && call.speaker !== "harry"} analyser={call.analyser} />
          </div>
          <p aria-live="polite" className="relative mt-5 min-h-5 text-center font-mono text-[11px] uppercase text-muted-foreground">{call.status}<span className="mx-2">·</span><span className="font-mono text-xs">{timer}</span></p>
          <div className="relative mt-7 flex justify-center">
            <Button size="lg" variant={call.running ? "destructive" : "default"} disabled={extracting} onClick={() => call.running ? call.stop() : void call.start(scenario, brief, attachment?.text ?? "", founderVoice)} className={`h-12 min-w-40 rounded-md px-7 text-sm ${call.running ? "hover:bg-destructive/90" : "hover:bg-primary-hover"}`}>{call.running ? <PhoneOff /> : <Phone />}{call.running ? "End call" : call.ended ? "Call again" : "Start call"}</Button>
          </div>
        </section>
        {(call.lines.length > 0 || call.preparing) && <section aria-label="Call transcript" className="mx-auto max-w-[640px] space-y-4 border-t border-border py-10">
          {call.lines.map((line) => {
            const founder = line.speaker === "harry";
            return <article key={line.id} className={`transcript-line group flex flex-col ${founder ? "items-end text-right" : "items-start text-left"}`}>
              <div className={`mb-1 flex h-6 items-center gap-2 font-mono text-[10px] uppercase ${founder ? "text-primary" : "text-muted-foreground"}`}>
                <span>{founder ? FOUNDER : scenario.counterpartName}</span>
                <Button size="icon" variant="ghost" aria-label={`Replay ${founder ? FOUNDER : scenario.counterpartName} line ${line.id + 1}`} title="Replay line" disabled={busy} onClick={() => void call.replay(line)} className="size-6 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100"><Play className="size-3" /></Button>
              </div>
              <p className={`max-w-[90%] break-words rounded-md px-4 py-2 text-sm leading-7 sm:text-base ${founder ? 'bg-accent' : 'bg-secondary'} ${line.final ? 'text-body' : 'text-muted-foreground'}`} aria-label={`${founder ? FOUNDER : scenario.counterpartName} heard line ${line.id + 1}`}><HeardText line={line} /></p>
              <div className="mt-1 flex max-w-full flex-wrap items-center gap-2 font-mono text-[9px] text-muted-foreground">
                <span className="flex items-center gap-1"><Ear className="size-3" />heard via AssemblyAI STT{line.fallback ? ' · fallback' : ''}</span>
                <Button variant="ghost" size="sm" className="h-6 px-1 text-[9px] text-muted-foreground" aria-expanded={Boolean(saidOpen[line.id])} aria-label={`Show said line ${line.id + 1}`} onClick={() => setSaidOpen((prev) => ({ ...prev, [line.id]: !prev[line.id] }))}>said<ChevronDown className="size-3" /></Button>
              </div>
              {saidOpen[line.id] && <p className="max-w-[90%] break-words text-xs leading-5 text-muted-foreground">{line.text}</p>}
            </article>;
          })}
          {call.preparing && !call.speaker && <div aria-label={`${call.preparing === "harry" ? "Harry" : scenario.counterpartName} is preparing a line`} className={`flex ${call.preparing === "harry" ? "justify-end" : "justify-start"}`}><div className={`typing-bubble ${call.preparing === "harry" ? "bg-accent text-primary" : "bg-secondary text-muted-foreground"}`}><span /><span /><span /></div></div>}
          <div ref={transcriptEnd} />
        </section>}
      </main>
      <Toaster theme={theme} richColors position="top-center" />
      <footer className="mx-auto px-5 pb-8 pt-10 text-center font-mono text-[10px] leading-relaxed text-muted-foreground sm:text-[11px]">Powered by AssemblyAI · Voice cloning, TTS, Streaming STT &amp; LLM Gateway</footer>
    </div>
  );
}

function Avatar({ name, label, initial, cloned, active, dimmed, listening, analyser }: { name: string; label: string; initial: string; cloned?: boolean; active: boolean; dimmed: boolean; listening: boolean; analyser: AnalyserNode | null }) {
  return <div className={`relative z-10 flex flex-col items-center text-center transition-opacity duration-500 ${dimmed ? "opacity-40" : "opacity-100"}`}>
    <div className={`speaker-avatar relative grid size-28 place-items-center rounded-full border text-4xl font-medium sm:size-40 sm:text-5xl ${cloned ? "border-primary/30 bg-accent text-primary" : "border-border bg-secondary text-foreground"} ${cloned ? "speaker-harry" : "speaker-counterpart"}`}>{initial}<SpeakerRing analyser={analyser} active={active} /></div>
    <p className="mt-6 text-sm font-medium sm:text-base">{name}</p>
    <p className="mt-1 flex min-h-4 items-center justify-center gap-1 text-[10px] text-muted-foreground sm:text-xs">{listening ? <><Mic className="listening-mic size-3" />listening…</> : dimmed ? "listening" : label}</p>
  </div>;
}

function PromptBox({ id, value, disabled, onChange }: { id: string; value: string; disabled: boolean; onChange: (value: string) => void }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const resize = () => { element.style.height = "auto"; element.style.height = `${element.scrollHeight}px`; };
    resize();
    const observer = new ResizeObserver(resize); observer.observe(element);
    return () => observer.disconnect();
  }, [value]);
  return <Textarea ref={ref} id={id} rows={8} maxLength={6000} spellCheck={false} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} className="min-h-48 resize-none overflow-hidden rounded-md border-border bg-secondary/40 px-4 py-3 font-mono text-[13px] leading-6 text-body shadow-none" />;
}

// Reveals what the listener's STT heard in step with the audio that is playing.
function HeardText({ line }: { line: SpokenLine }) {
  const [now, setNow] = useState(() => performance.now());
  const total = line.duration * 1000;
  const playing = now - line.startedAt < total;
  useEffect(() => {
    if (!playing) return;
    let raf = 0; let last = 0;
    const tick = (t: number) => { if (t - last > 80) { last = t; setNow(performance.now()); } raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);
  const words = line.heard.split(/\s+/).filter(Boolean);
  const progress = Math.min(1, Math.max(0, (now - line.startedAt) / total + 0.08));
  const shown = playing ? words.slice(0, Math.ceil(words.length * progress)).join(" ") : line.heard;
  const done = line.final && !playing;
  return <>{shown || (done ? "No speech heard." : "…")}{!done && <span className="stt-caret" aria-hidden="true">▍</span>}</>;
}
