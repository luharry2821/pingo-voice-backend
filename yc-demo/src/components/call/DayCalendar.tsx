import { PhoneIncoming } from "lucide-react";
import { useEffect, useState } from "react";

// Harry's day in a Google Calendar-style day view: 9:00 AM to 5:00 PM, sixteen back-to-back
// 30-minute meetings. At 10:00 three calls clash; each maps to a call scenario.
type Block = { start: number; title: string };
const DAY_START = 9; const DAY_END = 17; const HOUR = 104; const SLOT = 0.5;
const MEETINGS: Block[] = [
  { start: 9, title: "Team standup" },
  { start: 9.5, title: "YC Bookface + inbox triage" },
  { start: 10.5, title: "Eng sync: STT latency" },
  { start: 11, title: "Investor update w/ YC partner" },
  { start: 11.5, title: "Pitch deck edits" },
  { start: 12, title: "Lunch w/ cofounder candidate" },
  { start: 12.5, title: "Coffee chat: angel investor" },
  { start: 13, title: "Pingo AI design review" },
  { start: 13.5, title: "Pronunciation model eval" },
  { start: 14, title: "Hiring interview: ML engineer" },
  { start: 14.5, title: "Hiring debrief" },
  { start: 15, title: "Product demo: language school" },
  { start: 15.5, title: "Pricing & billing review" },
  { start: 16, title: "YC office hours" },
  { start: 16.5, title: "Weekly metrics review" },
];
export const CLASH: { id: "mom" | "engineer" | "customer"; title: string }[] = [
  { id: "mom", title: "MOM catch up" },
  { id: "engineer", title: "Onboarding new engineer <> Alex" },
  { id: "customer", title: "Customer Sync Dana" },
];
const clock = (h: number) => { const hr = Math.floor(h); const m = Math.round((h - hr) * 60); return `${hr > 12 ? hr - 12 : hr}:${String(m).padStart(2, "0")}`; };
const hourLabel = (h: number) => `${h > 12 ? h - 12 : h} ${h < 12 ? "AM" : "PM"}`;
const range = (start: number) => `${clock(start)} – ${clock(start + SLOT)}${start + SLOT < 12 ? "am" : "pm"}`;
const top = (h: number) => (h - DAY_START) * HOUR;

export function DayCalendar({ selected, disabled, onSelect }: { selected: string; disabled?: boolean; onSelect: (id: string) => void }) {
  const hours = Array.from({ length: DAY_END - DAY_START }, (_, i) => DAY_START + i);
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => { setNow(new Date()); const t = setInterval(() => setNow(new Date()), 60_000); return () => clearInterval(t); }, []);
  const nowH = now ? now.getHours() + now.getMinutes() / 60 : -1;
  const showNow = nowH >= DAY_START && nowH <= DAY_END;
  const weekday = (now ?? new Date(0)).toLocaleDateString("en-US", { weekday: "short" }).toUpperCase();
  return (
    <section aria-label="Harry's calendar today" className="day-calendar mx-auto mt-10 max-w-[720px] overflow-hidden rounded-xl border bg-card text-left">
      <div className="flex items-center gap-4 border-b px-4 py-3">
        <div className="flex w-12 flex-col items-center sm:w-14">
          <span className="text-[11px] font-medium text-primary">{now ? weekday : ""}</span>
          <span className="cal-today grid size-10 place-items-center rounded-full text-[20px] font-medium">{now ? now.getDate() : ""}</span>
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">Harry's day</p>
          <p className="text-[12px] text-muted-foreground">16 meetings, back to back</p>
        </div>
        <p className="cal-clash-badge shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium">3 calls at 10:00</p>
      </div>
      <div className="relative pb-3 pr-3 pt-4 sm:pr-4">
        <div className="relative" style={{ height: (DAY_END - DAY_START) * HOUR }}>
          {hours.map((h) => (
            <div key={h} className="absolute inset-x-0" style={{ top: top(h) }}>
              <span className="absolute -top-2 left-0 w-12 pr-2 text-right text-[10px] text-muted-foreground sm:w-16 sm:text-[11px]">{hourLabel(h)}</span>
              <span className="absolute left-12 right-0 top-0 h-px bg-border sm:left-16" />
              <span className="cal-half absolute left-12 right-0 h-px sm:left-16" style={{ top: HOUR / 2 }} />
            </div>
          ))}
          <div className="absolute bottom-0 left-12 right-0 top-0 border-l sm:left-16">
            {MEETINGS.map((m) => (
              <div key={m.title} className="cal-event absolute left-1 right-1 overflow-hidden rounded-md px-2 py-1" style={{ top: top(m.start) + 1, height: SLOT * HOUR - 3 }}>
                <p className="truncate text-[12px] font-medium leading-4 sm:text-[13px]">{m.title}</p>
                <p className="truncate text-[11px] leading-4 opacity-80">{range(m.start)}</p>
              </div>
            ))}
            <div className="cal-clash absolute left-1 right-1 grid grid-cols-3 gap-1 rounded-md" style={{ top: top(10) + 1, height: SLOT * HOUR - 3 }}>
              {CLASH.map((c) => {
                const active = c.id === selected;
                return (
                  <button key={c.id} type="button" disabled={disabled} aria-pressed={active} onClick={() => onSelect(c.id)} title={`Take “${c.title}” with my voice clone`}
                    className={`cal-clash-event min-w-0 overflow-hidden rounded-md px-2 py-1 text-left transition-colors disabled:cursor-not-allowed ${active ? "is-active" : ""}`}>
                    <p className="flex items-start gap-1 text-[11px] font-medium leading-[14px] sm:text-[12px] sm:leading-[15px]"><PhoneIncoming className="mt-px size-3 shrink-0" aria-hidden="true" /><span className="line-clamp-2">{c.title}</span></p>
                    <p className="truncate text-[10px] leading-[14px] opacity-85 sm:text-[11px]">{active ? "Selected · " : ""}10:00 – 10:30am</p>
                  </button>
                );
              })}
            </div>
            {showNow && (
              <div className="cal-now pointer-events-none absolute -left-1.5 right-0 z-10 flex items-center" style={{ top: top(nowH) - 5 }} aria-hidden="true">
                <span className="size-2.5 rounded-full" /><span className="h-0.5 flex-1" />
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
