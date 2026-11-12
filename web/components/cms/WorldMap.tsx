"use client";

import DottedMap from "dotted-map";
import { useMemo, useState } from "react";
import { CENTROIDS, countryName, flag } from "@/lib/countries";
import { useI18n, useT } from "@/lib/i18n";

export interface CountryVisits { code: string; visitors: number }

// The dotted land is computed once (the library caches the grid) and shared by every map.
let land: { svg: string; width: number; height: number; pin: (lat: number, lng: number) => { x: number; y: number } } | null = null;
function dottedLand() {
  if (!land) {
    const map = new DottedMap({ height: 60, grid: "diagonal" });
    const svg = map.getSVG({ radius: 0.2, color: "currentColor", shape: "circle", backgroundColor: "transparent" });
    const [, w, h] = /viewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(svg) ?? [];
    land = {
      svg: svg.replace(/^<svg[^>]*>/, "").replace(/<\/svg>\s*$/, ""),
      width: Number(w) || 119, height: Number(h) || 60,
      pin: (lat, lng) => { const p = map.getPin({ lat, lng }); return { x: p?.x ?? 0, y: p?.y ?? 0 }; },
    };
  }
  return land;
}

/** Visitors by country on a dotted world map: a pulsing marker per country, sized by its share. */
export function WorldMap({ countries, className = "" }: { countries: CountryVisits[]; className?: string }) {
  const t = useT();
  const { lang } = useI18n();
  const [hover, setHover] = useState<string | null>(null);
  const world = useMemo(() => dottedLand(), []);
  const max = Math.max(1, ...countries.map((c) => c.visitors));
  const total = countries.reduce((n, c) => n + c.visitors, 0);
  const markers = useMemo(() => countries.flatMap((c) => {
    const at = CENTROIDS[c.code];
    return at ? [{ ...c, ...world.pin(at[0], at[1]) }] : [];
  }), [countries, world]);
  const active = markers.find((m) => m.code === hover);

  return (
    <div className={`relative w-full ${className}`}>
      <svg viewBox={`0 0 ${world.width} ${world.height}`} className="w-full text-muted-foreground/50" role="img" aria-label={t("Visitors by country")}>
        <g dangerouslySetInnerHTML={{ __html: world.svg }} />
        {markers.map((m) => {
          const r = 0.9 + 2.4 * Math.sqrt(m.visitors / max);
          return (
            <g key={m.code} onMouseEnter={() => setHover(m.code)} onMouseLeave={() => setHover(null)} className="cursor-pointer">
              <circle cx={m.x} cy={m.y} r={r} className="animate-ping fill-brand/40" style={{ transformOrigin: `${m.x}px ${m.y}px`, animationDuration: "2.6s" }} />
              <circle cx={m.x} cy={m.y} r={r * 0.55} className="fill-brand" />
              <circle cx={m.x} cy={m.y} r={r * 1.6} className="fill-transparent" />
            </g>
          );
        })}
      </svg>
      {active && (
        <div className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground shadow-md"
          style={{ left: `${(active.x / world.width) * 100}%`, top: `${(active.y / world.height) * 100 - 3}%` }}>
          <div className="font-medium">{flag(active.code)} {countryName(active.code, lang)}</div>
          <div className="text-muted-foreground">{t("{n} visitors · {pct}%", { n: new Intl.NumberFormat(lang).format(active.visitors), pct: Math.round((active.visitors / total) * 100) })}</div>
        </div>
      )}
    </div>
  );
}

/** Ranked list next to the map. */
export function CountryList({ countries, limit = 8 }: { countries: CountryVisits[]; limit?: number }) {
  const { lang } = useI18n();
  const max = Math.max(1, ...countries.map((c) => c.visitors));
  const total = countries.reduce((n, c) => n + c.visitors, 0) || 1;
  return (
    <ul className="space-y-2.5">
      {countries.slice(0, limit).map((c) => (
        <li key={c.code} className="space-y-1">
          <div className="flex items-center justify-between gap-2 text-sm">
            <span className="truncate">{flag(c.code)} {countryName(c.code, lang)}</span>
            <span className="tabular-nums text-muted-foreground">{Math.round((c.visitors / total) * 100)}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-brand" style={{ width: `${(c.visitors / max) * 100}%` }} /></div>
        </li>
      ))}
    </ul>
  );
}
