"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useT } from "@/lib/i18n";
import { BRAND, MUTED } from "@/lib/brand";

export function TrafficChart({ days, lang }: { days: { day: string; pageviews: number; visitors: number }[]; lang: string }) {
  const t = useT();
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={days} margin={{ left: -16, right: 8, top: 8 }}>
          <defs>
            <linearGradient id="pv" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={BRAND} stopOpacity={0.35} /><stop offset="100%" stopColor={BRAND} stopOpacity={0} /></linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="currentColor" strokeOpacity={0.1} />
          <XAxis dataKey="day" tickFormatter={(d: string) => new Date(`${d}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" })}
            tick={{ fontSize: 11, fill: "currentColor", opacity: 0.6 }} axisLine={false} tickLine={false} minTickGap={24} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "currentColor", opacity: 0.6 }} axisLine={false} tickLine={false} width={48} />
          <Tooltip contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12, color: "var(--popover-foreground)" }}
            labelFormatter={(d) => new Date(`${d}T12:00:00`).toLocaleDateString(lang, { weekday: "short", day: "numeric", month: "long" })} />
          <Area type="monotone" dataKey="pageviews" name={t("Page views")} stroke={BRAND} strokeWidth={2} fill="url(#pv)" />
          <Area type="monotone" dataKey="visitors" name={t("Visitors")} stroke={MUTED} strokeWidth={2} fill="none" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
