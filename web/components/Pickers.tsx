"use client";

import { useMemo, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/cms/ui/popover";
import { Select } from "@/components/Select";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";

const pad = (n: number) => String(n).padStart(2, "0");
const iso = (y: number, m: number, d: number) => `${y}-${pad(m + 1)}-${pad(d)}`;
const parse = (v: string): [number, number, number] | null => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v);
  return m ? [Number(m[1]), Number(m[2]) - 1, Number(m[3])] : null;
};

const FIELD =
  "flex h-[38px] w-full items-center gap-2 rounded-lg border border-zinc-300 bg-white/80 px-3 text-left text-sm text-zinc-900 outline-none transition hover:border-zinc-400 " +
  "focus-visible:border-indigo-400 focus-visible:ring-4 focus-visible:ring-indigo-500/15 dark:border-white/10 dark:bg-black/40 dark:text-zinc-100 dark:hover:border-white/20";

const PANEL = "cms rounded-xl bg-popover p-3 text-popover-foreground shadow-[0_0_0_1px_var(--border),0_16px_40px_-12px_rgb(0_0_0/0.25)]";

/** A calendar in a popover, in place of the browser's native date input. Value: "YYYY-MM-DD". */
export function DatePicker({ value, onChange, min, id, className, "aria-label": label }: { value: string; onChange: (v: string) => void; min?: string; id?: string; className?: string; "aria-label"?: string }) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);
  const sel = parse(value);
  const today = new Date();
  const [view, setView] = useState<[number, number]>(() => (sel ? [sel[0], sel[1]] : [today.getFullYear(), today.getMonth()]));
  const [y, m] = view;
  const locale = lang === "fr" ? "fr-FR" : "en-US";
  const monthName = new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(new Date(y, m, 1));
  const weekdays = useMemo(() => Array.from({ length: 7 }, (_, i) => new Intl.DateTimeFormat(locale, { weekday: "narrow" }).format(new Date(2024, 0, 1 + i))), [locale]); // 2024-01-01 is a Monday
  const lead = (new Date(y, m, 1).getDay() + 6) % 7;
  const days = new Date(y, m + 1, 0).getDate();
  const shift = (d: number) => setView(([yy, mm]) => { const n = new Date(yy, mm + d, 1); return [n.getFullYear(), n.getMonth()]; });
  const todayIso = iso(today.getFullYear(), today.getMonth(), today.getDate());
  const text = sel ? new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(sel[0], sel[1], sel[2])) : t("Pick a date");
  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o && sel) setView([sel[0], sel[1]]); }}>
      <PopoverTrigger id={id} aria-label={label} className={cn(FIELD, !sel && "text-zinc-400", className)}>
        <CalendarDays className="size-4 shrink-0 opacity-50" />
        <span className="truncate">{text}</span>
      </PopoverTrigger>
      <PopoverContent align="start">
        <div className={cn(PANEL, "w-[17.5rem]")}>
          <div className="mb-2 flex items-center justify-between">
            <button type="button" onClick={() => shift(-1)} aria-label={t("Previous month")} className="grid size-8 place-items-center rounded-lg hover:bg-muted"><ChevronLeft className="size-4" /></button>
            <span className="text-sm font-medium capitalize">{monthName}</span>
            <button type="button" onClick={() => shift(1)} aria-label={t("Next month")} className="grid size-8 place-items-center rounded-lg hover:bg-muted"><ChevronRight className="size-4" /></button>
          </div>
          <div className="grid grid-cols-7 text-center text-[11px] uppercase text-muted-foreground">
            {weekdays.map((w, i) => <span key={i} className="py-1">{w}</span>)}
          </div>
          <div className="grid grid-cols-7 gap-y-0.5">
            {Array.from({ length: lead }, (_, i) => <span key={`b${i}`} />)}
            {Array.from({ length: days }, (_, i) => {
              const d = iso(y, m, i + 1);
              const off = !!min && d < min;
              const on = d === value;
              return (
                <button key={d} type="button" disabled={off} onClick={() => { onChange(d); setOpen(false); }}
                  className={cn("mx-auto grid size-8 place-items-center rounded-lg text-sm tabular-nums transition-colors", on ? "bg-foreground text-background" : "hover:bg-muted", d === todayIso && !on && "font-semibold text-brand", off && "pointer-events-none opacity-30")}>
                  {i + 1}
                </button>
              );
            })}
          </div>
          <div className="mt-2 flex justify-between border-t border-border pt-2 text-xs">
            <button type="button" disabled={!!min && todayIso < min} onClick={() => { onChange(todayIso); setOpen(false); }} className="rounded-md px-2 py-1 hover:bg-muted disabled:opacity-40">{t("Today")}</button>
            {value && <button type="button" onClick={() => { onChange(""); setOpen(false); }} className="rounded-md px-2 py-1 text-muted-foreground hover:bg-muted">{t("Clear")}</button>}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

const HOURS = Array.from({ length: 24 }, (_, i) => pad(i));
const MINUTES = Array.from({ length: 12 }, (_, i) => pad(i * 5));

/** Date and time, both picked from menus in the dashboard's style. Value: "YYYY-MM-DDTHH:MM" or "". */
export function DateTimePicker({ value, onChange, min, id, "aria-label": label }: { value: string; onChange: (v: string) => void; min?: string; id?: string; "aria-label"?: string }) {
  const [date = "", time = "09:00"] = value.split("T");
  const [h, mi] = time.split(":");
  const minutes = MINUTES.includes(mi) ? MINUTES : [...MINUTES, mi].sort();
  const set = (d: string, hh: string, mm: string) => onChange(d ? `${d}T${hh}:${mm}` : "");
  return (
    <div className="flex gap-2">
      <DatePicker id={id} aria-label={label} value={date} min={min} onChange={(d) => set(d, h, mi)} className="flex-1" />
      <Select value={h} onChange={(e) => set(date || (min ?? ""), e.target.value, mi)} aria-label="HH" className="!w-[4.5rem]">
        {HOURS.map((x) => <option key={x}>{x}</option>)}
      </Select>
      <Select value={mi} onChange={(e) => set(date || (min ?? ""), h, e.target.value)} aria-label="MM" className="!w-[4.5rem]">
        {minutes.map((x) => <option key={x}>{x}</option>)}
      </Select>
    </div>
  );
}

const SWATCHES = ["#17171a", "#5b5bd6", "#2563eb", "#0891b2", "#059669", "#65a30d", "#ca8a04", "#ea580c", "#dc2626", "#db2777", "#9333ea", "#64748b"];
const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

/** A colour menu (swatches and a hex field), in place of the native colour input. */
export function ColorPicker({ value, onChange, id, "aria-label": label }: { value: string; onChange: (v: string) => void; id?: string; "aria-label"?: string }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<string | null>(null);
  const shown = HEX.test(value) ? value : "#000000";
  return (
    <Popover onOpenChange={() => setDraft(null)}>
      <PopoverTrigger id={id} aria-label={label ?? t("Color")} className={cn(FIELD, "w-auto gap-2.5 pr-3")}>
        <span className="size-5 rounded-md shadow-[inset_0_0_0_1px_rgb(0_0_0/0.12)]" style={{ background: shown }} />
        <span className="mono text-xs uppercase">{shown}</span>
      </PopoverTrigger>
      <PopoverContent align="start">
        <div className={cn(PANEL, "w-56 space-y-3")}>
          <div className="grid grid-cols-6 gap-1.5">
            {SWATCHES.map((c) => (
              <button key={c} type="button" aria-label={c} onClick={() => { onChange(c); setDraft(null); }}
                className={cn("size-7 rounded-lg shadow-[inset_0_0_0_1px_rgb(0_0_0/0.12)] transition-transform hover:scale-110", c.toLowerCase() === shown.toLowerCase() && "ring-2 ring-foreground ring-offset-2 ring-offset-popover")}
                style={{ background: c }} />
            ))}
          </div>
          <input value={draft ?? value} onChange={(e) => { const v = e.target.value; setDraft(v); if (HEX.test(v)) onChange(v); }} placeholder="#1a2b3c" spellCheck={false} aria-label="Hex"
            className="mono h-9 w-full rounded-lg bg-muted/70 px-3 text-xs outline-none focus:ring-4 focus:ring-brand/10" />
        </div>
      </PopoverContent>
    </Popover>
  );
}
