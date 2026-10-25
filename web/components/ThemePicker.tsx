"use client";

import { useT } from "@/lib/i18n";
import { STYLES, useTheme, type Mode, type ThemeStyle } from "@/lib/theme";

function Swatch({ style, big = false }: { style: ThemeStyle; big?: boolean }) {
  const { bg, surface, text, accents } = style.swatch;
  const layout = style.layout;
  return (
    <span className={`relative block overflow-hidden ${big ? "h-24 rounded-xl" : "h-10 rounded-lg"}`} style={{ background: bg }} aria-hidden>
      <span className={`absolute ${big ? "inset-2.5 gap-1.5" : "inset-1.5 gap-1"} grid`}
        style={{ gridTemplateColumns: layout === "list" ? "1fr" : "repeat(3, 1fr)", gridAutoRows: "1fr" }}>
        {layout === "bento" ? (
          <>
            <span className="row-span-2 rounded-[4px]" style={{ background: `linear-gradient(135deg, ${accents[0]}, ${accents[1]})` }} />
            <span className="rounded-[4px]" style={{ background: surface }} />
            <span className="rounded-[4px]" style={{ background: accents[2], opacity: 0.85 }} />
            <span className="col-span-2 rounded-[4px]" style={{ background: surface }} />
          </>
        ) : layout === "list" ? (
          [0, 1, 2].map((i) => (
            <span key={i} className="flex items-center gap-1 rounded-[2px] px-1" style={{ background: surface }}>
              <span className="h-1 w-1 rounded-full" style={{ background: accents[i] }} />
              <span className="h-[2px] flex-1 rounded" style={{ background: text, opacity: 0.35 }} />
            </span>
          ))
        ) : (
          [0, 1, 2].map((i) => (
            <span key={i} className="flex flex-col overflow-hidden rounded-[4px]" style={{ background: surface }}>
              {style.banner && <span className="h-1/2" style={{ background: `linear-gradient(135deg, ${accents[i]}, ${accents[(i + 1) % 3]})` }} />}
              <span className="m-1 h-[2px] rounded" style={{ background: text, opacity: 0.4 }} />
            </span>
          ))
        )}
      </span>
    </span>
  );
}

/** Mode buttons (dark / light / system), disabled when the style exists in one mode only. */
export function ModePicker() {
  const t = useT();
  const { style, mode, setMode } = useTheme();
  return (
    <div className="flex gap-1">
      {(["dark", "light", "system"] as Mode[]).map((m) => {
        const active = style.mode ? style.mode === m : mode === m;
        return (
          <button key={m} type="button" onClick={() => setMode(m)} disabled={!!style.mode}
            className={`flex-1 rounded-md px-2 py-1 text-xs disabled:cursor-not-allowed ${active ? "bg-zinc-200 dark:bg-white/10" : "text-zinc-500 hover:bg-zinc-100 disabled:opacity-40 dark:hover:bg-white/[0.05]"}`}>
            {m === "dark" ? t("Dark") : m === "light" ? t("Light") : t("System")}
          </button>
        );
      })}
    </div>
  );
}

/** Theme choice: a compact grid (menu) or big preview cards (settings). */
export function ThemePicker({ size = "compact" }: { size?: "compact" | "full" }) {
  const t = useT();
  const { style: current, setStyle } = useTheme();
  if (size === "compact") {
    return (
      <div className="grid grid-cols-3 gap-1.5">
        {STYLES.map((s) => (
          <button key={s.id} type="button" onClick={() => setStyle(s.id)} title={t(s.description)}
            className={`rounded-lg p-1 text-left transition ${current.id === s.id ? "ring-2 ring-indigo-500" : "ring-1 ring-zinc-200 hover:ring-zinc-400 dark:ring-white/10 dark:hover:ring-white/30"}`}>
            <Swatch style={s} />
            <span className="mt-1 block truncate px-0.5 text-[10px] font-medium">{t(s.label)}</span>
          </button>
        ))}
      </div>
    );
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {STYLES.map((s) => (
        <button key={s.id} type="button" onClick={() => setStyle(s.id)}
          className={`glass rounded-2xl p-2.5 text-left transition hover:-translate-y-0.5 ${current.id === s.id ? "ring-2 ring-indigo-500" : ""}`}>
          <Swatch style={s} big />
          <span className="mt-2.5 flex items-center justify-between gap-2 px-1">
            <span className="text-sm font-semibold">{t(s.label)}</span>
            <span className="text-[11px] text-zinc-500">
              {s.layout === "bento" ? t("Bento layout") : s.layout === "list" ? t("List layout") : t("Card layout")}
              {s.mode === "dark" ? ` · ${t("dark only")}` : ""}
            </span>
          </span>
          <span className="mt-1 block px-1 pb-1 text-xs text-zinc-500">{t(s.description)}</span>
        </button>
      ))}
    </div>
  );
}
