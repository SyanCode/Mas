"use client";

import { Mascot } from "@/components/Mascot";
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Bell, Bot, Copy, ExternalLink, LayoutPanelLeft, LogOut, Minus, RotateCw, Search, Square, X } from "lucide-react";
import { Avatar, myAvatarUrl, shownName } from "@/components/Avatar";
import { CommandPalette } from "@/components/CommandPalette";
import { Backdrop, Logo } from "@/components/Shell";
import { ModePicker, ThemePicker } from "@/components/ThemePicker";
import { Kbd, StatusDot } from "@/components/ui";
import { api, rememberSession, timeAgo, type Check, type HistoryEntry, type Service, type Session } from "@/lib/api";
import { DESKTOP_APPS, appOf, setUiMode, type DesktopApp } from "@/lib/desktop";
import { LanguagePicker, useT } from "@/lib/i18n";

interface Win { id: string; app: string; title: string; href: string; x: number; y: number; w: number; h: number; z: number; min: boolean; max: boolean; back?: Rect }
interface Rect { x: number; y: number; w: number; h: number }
type Dir = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

const BAR = 32;      // menu bar height
const DOCK = 84;     // room kept for the dock under maximized windows
const MIN_W = 380;
const MIN_H = 260;
const KEY = "mas-desktop";

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

function usePolled<T>(path: string, every: number, enabled = true): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const look = () => { if (!document.hidden) api<T>(path).then((d) => alive && setData(d)).catch(() => {}); };
    look();
    const id = setInterval(look, every);
    return () => { alive = false; clearInterval(id); };
  }, [path, every, enabled]);
  return data;
}

// -- widgets on the desktop ----------------------------------------------------------------------

interface Usage { server: { cpus: number; cpu_percent: number | null; load: number[]; memory: { total_mb: number | null; used_mb: number | null }; disk: { total_gb: number; used_gb: number } | null } }

function Meter({ label, value, text }: { label: string; value: number; text: string }) {
  const tone = value > 90 ? "bg-rose-500" : value > 75 ? "bg-amber-500" : "bg-indigo-500";
  return (
    <div>
      <div className="mb-1 flex justify-between text-[11px]"><span className="text-zinc-500">{label}</span><span className="font-mono">{text}</span></div>
      <div className="h-1.5 overflow-hidden rounded-full bg-zinc-900/10 dark:bg-white/10"><div className={`h-full rounded-full transition-all duration-700 ${tone}`} style={{ width: `${clamp(value, 0, 100)}%` }} /></div>
    </div>
  );
}

function Widget({ title, onClick, children }: { title: string; onClick?: () => void; children: React.ReactNode }) {
  return (
    <div onClick={onClick} className={`glass animate-fade-in rounded-2xl p-3.5 shadow-lg ${onClick ? "cursor-pointer transition hover:border-indigo-400/40" : ""}`}>
      <div className="mb-2.5 text-[10.5px] font-medium uppercase tracking-[0.12em] text-zinc-500">{title}</div>
      {children}
    </div>
  );
}

function Widgets({ open }: { open: (href: string, title?: string) => void }) {
  const t = useT();
  const usage = usePolled<Usage>("/usage", 10000);
  const services = usePolled<{ services: Service[] }>("/services", 20000);
  const health = usePolled<{ checks: Check[] }>("/health/checks", 60000);
  const history = usePolled<HistoryEntry[]>("/history?limit=5", 20000);
  const s = usage?.server;
  const list = services?.services ?? [];
  const bad = (health?.checks ?? []).filter((c) => c.level !== "info");
  const mem = s?.memory.total_mb ? ((s.memory.used_mb ?? 0) / s.memory.total_mb) * 100 : 0;
  const disk = s?.disk ? (s.disk.used_gb / s.disk.total_gb) * 100 : 0;
  return (
    <div className="pointer-events-auto absolute right-4 top-12 z-0 hidden w-64 space-y-3 xl:block">
      <Widget title={t("Server")} onClick={() => open("/resources/")}>
        <div className="space-y-2.5">
          <Meter label={t("CPU")} value={s?.cpu_percent ?? 0} text={s?.cpu_percent == null ? "—" : `${Math.round(s.cpu_percent)}%`} />
          <Meter label={t("Memory")} value={mem} text={s?.memory.total_mb ? `${Math.round(s.memory.used_mb ?? 0)} / ${Math.round(s.memory.total_mb)} MB` : "—"} />
          <Meter label={t("Disk")} value={disk} text={s?.disk ? `${s.disk.used_gb.toFixed(0)} / ${s.disk.total_gb.toFixed(0)} GB` : "—"} />
          {s && <div className="text-[11px] text-zinc-500">{t("Load")} {s.load.map((l) => l.toFixed(2)).join(" · ")}</div>}
        </div>
      </Widget>
      <Widget title={t("Services")} onClick={() => open("/")}>
        <div className="flex items-center gap-4 text-sm">
          <span className="flex items-center gap-1.5"><StatusDot status="running" size="sm" />{list.filter((x) => x.status === "running").length}</span>
          <span className="flex items-center gap-1.5"><StatusDot status="stopped" size="sm" />{list.filter((x) => x.status === "stopped").length}</span>
          <span className="flex items-center gap-1.5"><StatusDot status="errored" size="sm" />{list.filter((x) => x.status === "errored").length}</span>
        </div>
      </Widget>
      <Widget title={t("Health")} onClick={() => open("/health/")}>
        {bad.length === 0 ? <div className="text-xs text-emerald-600 dark:text-emerald-400">{t("All checks pass.")}</div> : (
          <ul className="space-y-1.5">{bad.slice(0, 3).map((c, i) => (
            <li key={i} className="flex gap-2 text-xs"><span className={`mt-1 size-1.5 shrink-0 rounded-full ${c.level === "error" ? "bg-rose-500" : "bg-amber-500"}`} /><span className="min-w-0 truncate">{c.title}</span></li>
          ))}</ul>
        )}
      </Widget>
      <Widget title={t("Recent activity")} onClick={() => open("/history/")}>
        <ul className="space-y-1.5">{(history ?? []).map((h) => (
          <li key={h.id} className="flex items-baseline gap-2 text-xs"><span className={`size-1.5 shrink-0 translate-y-[-1px] rounded-full ${h.ok ? "bg-emerald-500" : "bg-rose-500"}`} /><span className="min-w-0 flex-1 truncate">{h.action}</span><span className="shrink-0 text-[10px] text-zinc-500">{timeAgo(h.at)}</span></li>
        ))}</ul>
      </Widget>
    </div>
  );
}

// -- one window -------------------------------------------------------------------------------------

function WindowFrame({ win, app, focused, mobile, area, update, focus, close, reloadKey, reload }: {
  win: Win; app: DesktopApp; focused: boolean; mobile: boolean; area: { w: number; h: number };
  update: (id: string, patch: Partial<Win>) => void; focus: (id: string) => void; close: (id: string) => void;
  reloadKey: number; reload: (id: string) => void;
}) {
  const t = useT();
  const Icon = app.icon;
  const [ghost, setGhost] = useState<Rect | null>(null);
  const [busy, setBusy] = useState(false);  // dragging or resizing: the page inside must not catch the pointer

  const maxRect = useCallback((): Rect => ({ x: 0, y: BAR, w: area.w, h: area.h - BAR - (mobile ? 0 : DOCK) }), [area, mobile]);
  const snapAt = useCallback((px: number, py: number): Rect | null => {
    if (py <= BAR + 2) return maxRect();
    const half = area.w / 2, h = area.h - BAR - DOCK;
    if (px <= 6) return { x: 0, y: BAR, w: half, h };
    if (px >= area.w - 7) return { x: half, y: BAR, w: half, h };
    return null;
  }, [area, maxRect]);

  const toggleMax = () => win.max ? update(win.id, { max: false, ...(win.back ?? {}), back: undefined }) : update(win.id, { max: true, back: { x: win.x, y: win.y, w: win.w, h: win.h } });

  function dragStart(e: ReactPointerEvent<HTMLDivElement>) {
    if (mobile || e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
    focus(win.id);
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    let { x, y, w } = win;
    let ox = e.clientX - x, oy = e.clientY - y;
    if (win.max) {  // dragging a maximized window takes it back to its size, under the pointer
      const b = win.back ?? { x: 80, y: BAR + 40, w: 900, h: 600 };
      x = clamp(e.clientX - (b.w * (e.clientX / area.w)), 0, area.w - b.w); y = BAR; w = b.w;
      ox = e.clientX - x; oy = e.clientY - y;
      update(win.id, { max: false, x, y, w: b.w, h: b.h, back: undefined });
    }
    const move = (ev: PointerEvent) => {
      setBusy(true);
      update(win.id, { x: clamp(ev.clientX - ox, 80 - w, area.w - 80), y: clamp(ev.clientY - oy, BAR, area.h - 60) });
      setGhost(snapAt(ev.clientX, ev.clientY));
    };
    const up = (ev: PointerEvent) => {
      el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up);
      setBusy(false); setGhost(null);
      const snap = snapAt(ev.clientX, ev.clientY);
      if (snap) update(win.id, snap.h === area.h - BAR - DOCK && snap.w === area.w ? { max: true, back: { x: clamp(x, 0, area.w - win.w), y: BAR + 20, w: win.w, h: win.h } } : { ...snap, max: false });
    };
    el.addEventListener("pointermove", move); el.addEventListener("pointerup", up);
  }

  function resizeStart(dir: Dir) {
    return (e: ReactPointerEvent<HTMLDivElement>) => {
      if (mobile || win.max || e.button !== 0) return;
      e.stopPropagation(); focus(win.id);
      const el = e.currentTarget;
      el.setPointerCapture(e.pointerId);
      const start = { px: e.clientX, py: e.clientY, x: win.x, y: win.y, w: win.w, h: win.h };
      const move = (ev: PointerEvent) => {
        setBusy(true);
        const dx = ev.clientX - start.px, dy = ev.clientY - start.py;
        const r = { x: start.x, y: start.y, w: start.w, h: start.h };
        if (dir.includes("e")) r.w = Math.max(MIN_W, start.w + dx);
        if (dir.includes("s")) r.h = Math.max(MIN_H, start.h + dy);
        if (dir.includes("w")) { r.w = Math.max(MIN_W, start.w - dx); r.x = start.x + start.w - r.w; }
        if (dir.includes("n")) { r.h = Math.max(MIN_H, start.h - dy); r.y = Math.max(BAR, start.y + start.h - r.h); r.h = start.y + start.h - r.y; }
        update(win.id, r);
      };
      const up = () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); setBusy(false); };
      el.addEventListener("pointermove", move); el.addEventListener("pointerup", up);
    };
  }

  const full = mobile || win.max;
  const rect = mobile ? { x: 0, y: BAR, w: area.w, h: area.h - BAR - 76 } : win.max ? maxRect() : win;
  const hidden = win.min;
  const handles: [Dir, string][] = [["n", "inset-x-2 -top-1 h-2 cursor-ns-resize"], ["s", "inset-x-2 -bottom-1 h-2 cursor-ns-resize"], ["e", "inset-y-2 -right-1 w-2 cursor-ew-resize"], ["w", "inset-y-2 -left-1 w-2 cursor-ew-resize"],
    ["ne", "-right-1 -top-1 size-3 cursor-nesw-resize"], ["nw", "-left-1 -top-1 size-3 cursor-nwse-resize"], ["se", "-bottom-1 -right-1 size-3 cursor-nwse-resize"], ["sw", "-bottom-1 -left-1 size-3 cursor-nesw-resize"]];

  return (
    <>
      {ghost && <div className="pointer-events-none fixed z-[900] rounded-2xl border border-indigo-400/50 bg-indigo-400/10 transition-all duration-150" style={{ left: ghost.x + 6, top: ghost.y + 6, width: ghost.w - 12, height: ghost.h - 12 }} />}
      <div
        onPointerDownCapture={() => !focused && focus(win.id)}
        style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h, zIndex: win.z, display: hidden || (mobile && !focused) ? "none" : undefined }}
        className={`panel absolute flex animate-pop flex-col overflow-hidden ${full ? "" : "rounded-xl"} ${focused ? "shadow-[0_30px_90px_-20px_rgba(0,0,0,0.55)]" : "opacity-95 shadow-xl"}`}
      >
        <div
          onPointerDown={dragStart}
          onDoubleClick={() => !mobile && toggleMax()}
          className={`flex h-10 shrink-0 select-none items-center gap-2 border-b border-zinc-200/80 px-2.5 dark:border-white/[0.06] ${focused ? "bg-zinc-50 dark:bg-zinc-900" : "bg-zinc-100/60 dark:bg-zinc-950"} ${mobile ? "" : "cursor-default touch-none"}`}
        >
          <div className="flex items-center gap-1.5">
            <button onClick={() => close(win.id)} aria-label={t("Close")} title={t("Close")} className="group flex size-3.5 items-center justify-center rounded-full bg-rose-500"><X className="size-2.5 text-rose-950 opacity-0 group-hover:opacity-100" /></button>
            <button onClick={() => update(win.id, { min: true })} aria-label={t("Minimize")} title={t("Minimize")} className="group flex size-3.5 items-center justify-center rounded-full bg-amber-400"><Minus className="size-2.5 text-amber-950 opacity-0 group-hover:opacity-100" /></button>
            {!mobile && <button onClick={toggleMax} aria-label={win.max ? t("Restore") : t("Maximize")} title={win.max ? t("Restore") : t("Maximize")} className="group flex size-3.5 items-center justify-center rounded-full bg-emerald-500"><Square className="size-2 text-emerald-950 opacity-0 group-hover:opacity-100" /></button>}
          </div>
          <div className="mx-auto flex min-w-0 items-center gap-1.5 text-[13px] font-medium"><Icon className="size-3.5 shrink-0 text-indigo-500" /><span className="truncate">{win.title}</span></div>
          <div className="flex items-center gap-0.5 text-zinc-500">
            <button onClick={() => reload(win.id)} aria-label={t("Reload")} title={t("Reload")} className="rounded p-1 hover:bg-zinc-900/10 dark:hover:bg-white/10"><RotateCw className="size-3.5" /></button>
            <a href={win.href} target="_blank" rel="noreferrer" aria-label={t("Open in a tab")} title={t("Open in a tab")} className="rounded p-1 hover:bg-zinc-900/10 dark:hover:bg-white/10"><ExternalLink className="size-3.5" /></a>
          </div>
        </div>
        <iframe key={reloadKey} src={win.href} title={win.title} className={`min-h-0 w-full flex-1 border-0 bg-[var(--bg)] ${busy || !focused ? "pointer-events-none" : ""}`} />
        {!full && handles.map(([d, cls]) => <div key={d} onPointerDown={resizeStart(d)} className={`absolute z-10 touch-none ${cls}`} />)}
      </div>
    </>
  );
}

// -- the desktop ---------------------------------------------------------------------------------------

function load(): Win[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((w) => w && typeof w.href === "string" && typeof w.app === "string" && String(w.href).startsWith("/")) : [];
  } catch { return []; }
}

export function Desktop({ session }: { session: Session }) {
  const t = useT();
  const editor = session.role !== "admin" && session.role !== "viewer";
  const apps = useMemo(() => DESKTOP_APPS.filter((a) => !editor || a.editor), [editor]);
  const [wins, setWins] = useState<Win[]>([]);
  const [ready, setReady] = useState(false);
  const [area, setArea] = useState({ w: 1280, h: 800 });
  const [reloads, setReloads] = useState<Record<string, number>>({});
  const [palette, setPalette] = useState(false);
  const [menu, setMenu] = useState<"" | "logo" | "user" | "bell">("");
  const [now, setNow] = useState(() => new Date());
  const counter = useRef(0);
  const mobile = area.w < 768;

  useEffect(() => {
    const size = () => setArea({ w: window.innerWidth, h: window.innerHeight });
    size();
    window.addEventListener("resize", size);
    const id = setInterval(() => setNow(new Date()), 15000);
    return () => { window.removeEventListener("resize", size); clearInterval(id); };
  }, []);

  useEffect(() => {  // bring back the windows of the last visit
    const saved = load().filter((w) => apps.some((a) => a.id === w.app));
    counter.current = saved.reduce((m, w) => Math.max(m, w.z), 0);
    setWins(saved);
    setReady(true);
  }, [apps]);

  useEffect(() => {
    if (!ready) return;
    const id = setTimeout(() => { try { localStorage.setItem(KEY, JSON.stringify(wins)); } catch {} }, 300);
    return () => clearTimeout(id);
  }, [wins, ready]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); }
      if (e.altKey && e.shiftKey && e.code === "KeyD") { e.preventDefault(); setUiMode("classic"); window.location.href = "/"; }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const topId = useMemo(() => wins.filter((w) => !w.min).sort((a, b) => b.z - a.z)[0]?.id, [wins]);
  const update = useCallback((id: string, patch: Partial<Win>) => setWins((all) => all.map((w) => (w.id === id ? { ...w, ...patch } : w))), []);
  const focus = useCallback((id: string) => setWins((all) => all.map((w) => (w.id === id ? { ...w, z: ++counter.current, min: false } : w))), []);
  const close = useCallback((id: string) => setWins((all) => all.filter((w) => w.id !== id)), []);

  const open = useCallback((href: string, title?: string, fresh = false) => {
    const app = appOf(href);
    setWins((all) => {
      const same = all.find((w) => w.app === app.id && (w.href === href || !fresh));
      if (same) return all.map((w) => (w.id === same.id ? { ...w, href, title: title ?? w.title, z: ++counter.current, min: false } : w));
      const n = all.length;
      const [w, h] = app.size;
      const width = Math.min(w, window.innerWidth - 40), height = Math.min(h, window.innerHeight - BAR - DOCK - 20);
      return [...all, { id: `w${Date.now().toString(36)}${n}`, app: app.id, title: title ?? t(app.label), href,
        x: clamp(60 + (n % 8) * 34, 0, Math.max(0, window.innerWidth - width)), y: clamp(BAR + 20 + (n % 8) * 30, BAR, Math.max(BAR, window.innerHeight - height - DOCK)), w: width, h: height, z: ++counter.current, min: false, max: false }];
    });
  }, [t]);

  function dockClick(app: DesktopApp, fresh: boolean) {
    const mine = wins.filter((w) => w.app === app.id);
    if (!mine.length || fresh) return open(app.href, undefined, fresh);
    const top = mine.sort((a, b) => b.z - a.z)[0];
    if (top.id === topId) update(top.id, { min: true }); else focus(top.id);
  }

  const showDesktop = () => setWins((all) => all.map((w) => ({ ...w, min: !all.every((x) => x.min) })));
  const logout = async () => { await api("/logout", { method: "POST" }).catch(() => {}); window.location.href = "/login/"; };
  const classic = () => { setUiMode("classic"); window.location.href = "/"; };

  const health = usePolled<{ checks: Check[] }>("/health/checks", 60000, !editor);
  const robot = usePolled<{ goal: string; status: "running" | "waiting"; steps: number } | null>("/robot/active", 5000, !editor);
  const usage = usePolled<Usage>("/usage", 15000, !editor);
  const problems = (health?.checks ?? []).filter((c) => c.level !== "info");
  const focusedWin = wins.find((w) => w.id === topId);
  const focusedApp = focusedWin ? t(appOf(focusedWin.href).label) : "Mas";
  const name = shownName(session);

  return (
    <div className="fixed inset-0 overflow-hidden" onPointerDown={() => menu && setMenu("")}>
      <Backdrop />
      {/* menu bar */}
      <header className="glass absolute inset-x-0 top-0 z-[1000] flex items-center gap-1 rounded-none border-x-0 border-t-0 px-2 text-[13px]" style={{ height: BAR }}>
        <div className="relative">
          <button onClick={() => setMenu(menu === "logo" ? "" : "logo")} className="flex h-6 items-center gap-1.5 rounded px-1.5 hover:bg-zinc-900/10 dark:hover:bg-white/10"><Logo className="size-4" /><span className="font-semibold tracking-tight">mas</span></button>
          {menu === "logo" && (
            <div className="panel absolute left-0 top-8 w-56 animate-pop rounded-xl p-1.5" onPointerDown={(e) => e.stopPropagation()}>
              <MenuItem icon={LayoutPanelLeft} onClick={classic}>{t("Classic view")}</MenuItem>
              <MenuItem icon={Square} onClick={() => { showDesktop(); setMenu(""); }}>{t("Show desktop")}</MenuItem>
              <MenuItem icon={X} onClick={() => { setWins([]); setMenu(""); }}>{t("Close all windows")}</MenuItem>
              <div className="my-1 h-px bg-zinc-200 dark:bg-white/[0.06]" />
              <MenuItem icon={LogOut} danger onClick={logout}>{t("Log out")}</MenuItem>
            </div>
          )}
        </div>
        <span className="hidden px-2 font-medium sm:inline">{focusedApp}</span>
        <div className="ml-auto flex items-center gap-1.5">
          {robot && (
            <button onClick={() => open("/advisor/")} className={`flex animate-pop items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${robot.status === "waiting" ? "border-amber-400/40 bg-amber-400/10 text-amber-600 dark:text-amber-300" : "border-indigo-400/30 bg-indigo-400/10 text-indigo-600 dark:text-indigo-300"}`}>
              <Bot className={`size-3.5 ${robot.status === "waiting" ? "" : "animate-pulse"}`} /><span className="hidden max-w-40 truncate sm:inline">{robot.status === "waiting" ? t("The robot needs you") : t("Robot working…")}</span>
            </button>
          )}
          <button onClick={classic} title={`${t("Classic view")} (Alt+Shift+D)`} aria-label={t("Classic view")} className="flex h-6 items-center gap-1.5 rounded px-1.5 text-zinc-600 hover:bg-zinc-900/10 dark:text-zinc-300 dark:hover:bg-white/10"><LayoutPanelLeft className="size-3.5" /><span className="hidden text-xs md:inline">{t("Classic view")}</span></button>
          {usage?.server && <span className="hidden font-mono text-[11px] text-zinc-500 md:inline">{usage.server.cpu_percent == null ? "" : `${Math.round(usage.server.cpu_percent)}%`}</span>}
          {!editor && <button onClick={() => setPalette(true)} aria-label={t("Search…")} title={t("Search…")} className="flex h-6 items-center gap-1.5 rounded px-1.5 text-zinc-600 hover:bg-zinc-900/10 dark:text-zinc-300 dark:hover:bg-white/10"><Search className="size-3.5" /><span className="hidden lg:inline"><Kbd>⌘K</Kbd></span></button>}
          {!editor && (
            <div className="relative">
              <button onClick={() => setMenu(menu === "bell" ? "" : "bell")} aria-label={t("Notifications")} title={t("Notifications")} className="relative flex h-6 items-center rounded px-1.5 hover:bg-zinc-900/10 dark:hover:bg-white/10">
                <Bell className="size-3.5" />
                {problems.length > 0 && <span className="absolute -right-0.5 -top-0.5 flex size-3.5 items-center justify-center rounded-full bg-rose-500 text-[9px] font-bold text-white">{Math.min(problems.length, 9)}</span>}
              </button>
              {menu === "bell" && (
                <div className="panel absolute right-0 top-8 w-80 max-w-[calc(100vw-1rem)] animate-pop rounded-xl p-2" onPointerDown={(e) => e.stopPropagation()}>
                  <div className="px-2 pb-1.5 pt-1 text-[11px] font-medium uppercase tracking-wider text-zinc-500">{t("Notifications")}</div>
                  {problems.length === 0 && <div className="px-2 py-4 text-center text-xs text-zinc-500">{t("Nothing needs your attention.")}</div>}
                  <ul className="max-h-80 space-y-0.5 overflow-y-auto">{problems.map((c, i) => (
                    <li key={i}><button onClick={() => { open("/health/"); setMenu(""); }} className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-zinc-100 dark:hover:bg-white/[0.06]">
                      <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${c.level === "error" ? "bg-rose-500" : "bg-amber-500"}`} />
                      <span className="min-w-0 text-xs"><span className="block truncate font-medium">{c.title}</span><span className="line-clamp-2 text-zinc-500">{c.detail}</span></span>
                    </button></li>
                  ))}</ul>
                </div>
              )}
            </div>
          )}
          <span className="px-1.5 text-xs tabular-nums text-zinc-600 dark:text-zinc-300" title={now.toLocaleDateString()}>
            <span className="hidden sm:inline">{now.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} </span>{now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
          </span>
          <div className="relative">
            <button onClick={() => setMenu(menu === "user" ? "" : "user")} className="flex rounded-full" aria-label={name} title={name}><Avatar name={name} src={myAvatarUrl(session)} size={22} /></button>
            {menu === "user" && (
              <div className="panel absolute right-0 top-8 w-[min(18rem,calc(100vw-1rem))] animate-pop rounded-xl p-2" onPointerDown={(e) => e.stopPropagation()}>
                <div className="px-2 py-1.5 text-sm font-medium">{name}<span className="block text-xs font-normal text-zinc-500">@{session.username} · v{session.version}</span></div>
                <div className="space-y-2 p-1.5"><ThemePicker /><ModePicker /></div>
                <div className="px-1.5 pb-1"><LanguagePicker className="w-full" /></div>
                <div className="my-1 h-px bg-zinc-200 dark:bg-white/[0.06]" />
                <MenuItem icon={LayoutPanelLeft} onClick={classic}>{t("Classic view")}</MenuItem>
                <MenuItem icon={LogOut} danger onClick={logout}>{t("Log out")}</MenuItem>
              </div>
            )}
          </div>
        </div>
      </header>

      {!editor && !mobile && <Widgets open={open} />}

      {/* empty desktop */}
      {ready && wins.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 text-center">
          <Logo className="size-14 opacity-80" />
          <p className="text-sm text-zinc-500">{t("Open an app from the dock below.")}</p>
        </div>
      )}

      {wins.map((w) => (
        <WindowFrame key={w.id} win={w} app={appOf(w.href)} focused={w.id === topId} mobile={mobile} area={area}
          update={update} focus={focus} close={close} reloadKey={reloads[w.id] ?? 0} reload={(id) => setReloads((r) => ({ ...r, [id]: (r[id] ?? 0) + 1 }))} />
      ))}

      {/* dock */}
      <nav className="pointer-events-none absolute inset-x-0 bottom-2 z-[1000] flex justify-center px-2">
        <div className="glass pointer-events-auto flex max-w-full items-end gap-1.5 overflow-x-auto rounded-2xl px-2 py-1.5 shadow-2xl no-scrollbar">
          {apps.map((a) => {
            const mine = wins.filter((w) => w.app === a.id);
            const Icon = a.icon;
            return (
              <button key={a.id} onClick={(e) => dockClick(a, e.shiftKey)} title={t(a.label)} aria-label={t(a.label)} className="group relative flex shrink-0 flex-col items-center">
                <span className={`flex size-11 items-center justify-center rounded-xl bg-gradient-to-br ${a.tint} text-white shadow-md transition duration-200 group-hover:-translate-y-1.5 group-hover:scale-125 group-active:scale-110 sm:size-12`}><Icon className="size-5 sm:size-[22px]" /></span>
                <span className={`mt-1 size-1 rounded-full transition ${mine.length ? (mine.every((w) => w.min) ? "bg-zinc-400" : "bg-indigo-500") : "bg-transparent"}`} />
                <span className="pointer-events-none absolute -top-8 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded-md bg-zinc-900 px-2 py-0.5 text-[11px] text-white opacity-0 transition group-hover:opacity-100 sm:block">{t(a.label)}</span>
              </button>
            );
          })}
          <span className="mx-1 mb-2 h-8 w-px shrink-0 bg-zinc-300 dark:bg-white/10" />
          <button onClick={showDesktop} title={t("Show desktop")} aria-label={t("Show desktop")} className="mb-2.5 flex size-8 shrink-0 items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-900/10 hover:text-zinc-900 dark:hover:bg-white/10 dark:hover:text-white"><Copy className="size-4" /></button>
        </div>
      </nav>

      {session.role === "admin" && <div className="relative z-[1001]"><Mascot /></div>}
      {!editor && <CommandPalette open={palette} onClose={() => setPalette(false)} onNavigate={(href, label) => open(href, label)} />}
    </div>
  );
}

function MenuItem({ icon: Icon, onClick, danger, children }: { icon: typeof X; onClick: () => void; danger?: boolean; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={`flex w-full items-center gap-2.5 rounded-md px-3 py-1.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-white/[0.06] ${danger ? "text-rose-600 dark:text-rose-400" : ""}`}>
      <Icon className="size-3.5 opacity-70" />{children}
    </button>
  );
}

/** Loads the session, then shows the desktop (a CMS account only gets its own apps). */
export function DesktopRoot() {
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => {
    setUiMode("desktop");
    api<Session>("/session").then((s) => {
      // The desktop is the server's: a CMS account goes to its CMS.
      if (s.role !== "admin" && s.role !== "viewer") return window.location.replace("/cms/");
      rememberSession(s); setSession(s);
    }).catch(() => {});
  }, []);
  if (!session) return <div className="flex min-h-screen items-center justify-center"><Backdrop /><Logo className="h-10 w-10 animate-pulse" /></div>;
  return <Desktop session={session} />;
}
