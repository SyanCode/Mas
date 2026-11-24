"use client";

import { AiText } from "@/components/AiText";
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import Link from "next/link";
import { ArrowLeftRight, Check, Minus, Send, Square, Wand2, X } from "lucide-react";
import { MascotBot, type Mood } from "@/components/MascotBot";
import { PromptCoach } from "@/components/PromptCoach";
import { Button, Spinner } from "@/components/ui";
import { api, ApiError, type App, type RobotRun, type RobotStep } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

interface Active { id: number; status: "running" | "waiting"; question?: boolean }
interface Place { side: "left" | "right"; y: number; hidden: boolean }  // y: share of the window height (top of the robot)

const KEY = "mas-mascot";
const RUN_KEY = "mas-mascot-run";
const SIZE = 64;
const PANEL_W = 360;
const SUGGESTIONS = [msg("What needs my attention right now?"), msg("Are my sites well referenced?"), msg("Why did the last deployment fail?")];

function loadPlace(): Place {
  try {
    const p = JSON.parse(localStorage.getItem(KEY) || "null");
    if (p && (p.side === "left" || p.side === "right") && typeof p.y === "number") return { side: p.side, y: Math.min(0.92, Math.max(0.05, p.y)), hidden: !!p.hidden };
  } catch { /* default */ }
  return { side: "right", y: 0.72, hidden: false };
}

/** The robot's last words without the RESULT line it ends its reports with. */
function answerOf(run: RobotRun): string {
  return (run.summary || "").replace(/\n?RESULT:.*$/i, "").trim();
}

/**
 * The robot as a mascot: docked on the left or right edge of the screen (drag it up, down or to the other side), it
 * shows what the robot is doing on its face. A click opens a panel to ask it something (it only looks: nothing is
 * changed) or give it something to do, follow the run and answer its questions.
 */
/** A change the robot wants to make, waiting for the administrator's click. */
function Approval({ run, busy, withGoal, onDecide }: { run: RobotRun; busy: boolean; withGoal?: boolean; onDecide: (approve: boolean) => void }) {
  const t = useT();
  return (
    <div className="rounded-xl border border-amber-400/40 bg-amber-400/10 p-2.5">
      <div className="text-xs font-medium text-amber-700 dark:text-amber-300">{t("The robot asks your permission")}</div>
      {withGoal && <div className="mt-0.5 truncate text-[11px] text-zinc-500">{run.goal}</div>}
      <div className="mt-1 text-xs">{run.pending?.detail}</div>
      <div className="mt-2 flex gap-2">
        <Button variant="primary" disabled={busy} onClick={() => onDecide(true)}><Check className="size-3.5" />{t("Allow")}</Button>
        <Button variant="danger" disabled={busy} onClick={() => onDecide(false)}><X className="size-3.5" />{t("Refuse")}</Button>
      </div>
    </div>
  );
}

export function Mascot() {
  const t = useT();
  const [place, setPlace] = useState<Place>({ side: "right", y: 0.72, hidden: true });
  const [ready, setReady] = useState(false);
  const [open, setOpen] = useState(false);
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const [active, setActive] = useState<Active | null>(null);
  const [run, setRun] = useState<RobotRun | null>(null);
  const [text, setText] = useState("");
  const [appId, setAppId] = useState("");
  const [sites, setSites] = useState<App[] | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [bubble, setBubble] = useState("");
  const [refineOn, setRefineOn] = useState<boolean | null>(null);  // auto-prompting before a task
  const [coach, setCoach] = useState<string | null>(null);
  const press = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const shown = useRef<number | null>(null);
  const lastStatus = useRef<string>("");
  const log = useRef<HTMLDivElement>(null);

  const [view, setView] = useState({ w: 1280, h: 800 });
  useEffect(() => {
    setPlace(loadPlace());
    setReady(true);
    const size = () => setView({ w: window.innerWidth, h: window.innerHeight });
    size();
    window.addEventListener("resize", size);
    return () => window.removeEventListener("resize", size);
  }, []);
  // the page keeps a free strip on the side the robot is docked to: it never covers a card or a button
  useEffect(() => {
    const root = document.documentElement;
    if (ready && !place.hidden) root.dataset.mascot = place.side;
    else delete root.dataset.mascot;
    return () => { delete root.dataset.mascot; };
  }, [ready, place.side, place.hidden]);
  const save = useCallback((p: Place) => { setPlace(p); try { localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* private mode */ } }, []);

  // what the robot does: the task going on (it may wait for a click), and the question asked here, which may run
  // beside a task since it changes nothing. The panel follows the question asked here (until "New question"), else
  // the task; a task waiting for a click always gets its Allow / Refuse box on top.
  const runId = run?.id ?? null;
  const [waiting, setWaiting] = useState<RobotRun | null>(null);
  const runRef = useRef<RobotRun | null>(null);
  useEffect(() => { runRef.current = run; }, [run]);
  useEffect(() => {
    let alive = true;
    const look = async () => {
      const a = await api<Active | null>("/robot/active").catch(() => null);
      if (!alive) return;
      setActive(a);
      const mine = Number(sessionStorage.getItem(RUN_KEY)) || null;  // the last question asked here
      const cur = runRef.current;
      const keepMine = !!mine && ((a?.question && a.id === mine) || (cur?.id === mine && cur.autonomy === "read"));
      // a run that just ended stays on screen until another one starts
      const id = keepMine ? mine : a && !a.question ? a.id : shown.current ?? mine;
      shown.current = id;
      if (id) {
        const r = await api<RobotRun>(`/robot/runs/${id}`).catch(() => null);
        if (alive && r) setRun(r);
      }
      const task = a && !a.question && a.status === "waiting" && a.id !== id
        ? await api<RobotRun>(`/robot/runs/${a.id}`).catch(() => null) : null;
      if (alive) setWaiting(task && task.status === "waiting" ? task : null);
    };
    look();
    const every = setInterval(look, open || active ? 2500 : 6000);
    return () => { alive = false; clearInterval(every); };
  }, [open, active?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // a short word in a bubble when something happens while the panel is closed
  useEffect(() => {
    const status = run?.status ?? "";
    if (status && status !== lastStatus.current && lastStatus.current) {
      if (status === "waiting") setBubble(t("I need your go-ahead"));
      else if (status === "done") setBubble(t("Done! Click to read"));
      else if (status === "failed") setBubble(t("Something went wrong"));
    }
    lastStatus.current = status;
  }, [run?.status, t]);
  useEffect(() => { if (open) setBubble(""); }, [open]);
  useEffect(() => {
    if (!bubble || run?.status === "waiting") return;
    const id = setTimeout(() => setBubble(""), 9000);
    return () => clearTimeout(id);
  }, [bubble, run?.status]);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [run?.steps, open]);
  useEffect(() => {
    if (!open) return;
    if (sites === null) api<App[]>("/apps").then((a) => setSites(a.filter((x) => x.repo))).catch(() => setSites([]));
    if (refineOn === null) api<{ refine?: boolean }>("/robot").then((r) => setRefineOn(r.refine !== false)).catch(() => setRefineOn(false));
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open, sites]);

  const mineLive = !!run && run.autonomy === "read" && (run.status === "running" || run.status === "waiting");
  const taskBusy = !!active && !active.question;
  const finishedAt = run?.finished_at ?? 0;
  const recent = finishedAt && Date.now() / 1000 - finishedAt < 60;
  const spent = /monthly token cap/i.test(error) || /monthly token cap/i.test(run?.summary ?? "");
  const mood: Mood = active?.status === "waiting" ? "waiting" : active?.status === "running" || mineLive ? "working"
    : spent ? "sleep" : recent && run?.status === "done" ? "happy" : recent && run?.status === "failed" ? "sad" : "idle";

  const start = async (goal: string, question: boolean) => {
    if (goal.trim().length < 5) return;
    setBusy(question ? "ask" : "do"); setError("");
    try {
      const body: Record<string, string> = { goal: goal.trim(), app_id: appId };
      if (question) body.autonomy = "read";
      const r = await api<{ id: number }>("/robot/runs", { method: "POST", body });
      shown.current = r.id;
      if (question) sessionStorage.setItem(RUN_KEY, String(r.id));
      else sessionStorage.removeItem(RUN_KEY);
      if (!question) setActive({ id: r.id, status: "running" });
      setRun(await api<RobotRun>(`/robot/runs/${r.id}`));
      setText("");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally { setBusy(""); }
  };
  const act = async (what: "stop" | "decision", body?: object, target: number | null = runId) => {
    if (!target) return;
    setBusy(what); setError("");
    try {
      await api(`/robot/runs/${target}/${what}`, { method: "POST", body });
      const after = await api<RobotRun>(`/robot/runs/${target}`);
      if (target === runId) setRun(after);
      else setWaiting(after.status === "waiting" ? after : null);
    } catch (e) { setError(e instanceof ApiError ? e.detail : String(e)); } finally { setBusy(""); }
  };
  const fresh = () => { sessionStorage.removeItem(RUN_KEY); shown.current = null; setRun(null); setError(""); };

  // dragging: up and down along the edge, and across to the other side
  const down = (e: ReactPointerEvent<HTMLButtonElement>) => {
    press.current = { x: e.clientX, y: e.clientY, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const p = press.current;
    if (!p) return;
    if (!p.moved && Math.hypot(e.clientX - p.x, e.clientY - p.y) < 6) return;
    p.moved = true;
    setDrag({ x: e.clientX, y: e.clientY });
  };
  const up = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const p = press.current;
    press.current = null;
    if (p?.moved) {
      const h = window.innerHeight;
      save({ ...place, side: e.clientX < window.innerWidth / 2 ? "left" : "right", y: Math.min(0.92, Math.max(0.05, (e.clientY - SIZE / 2) / h)) });
      setDrag(null);
    } else {
      setOpen((o) => !o);
    }
  };

  if (!ready) return null;

  if (place.hidden) {
    return (
      <button type="button" onClick={() => save({ ...place, hidden: false })} aria-label={t("Show the robot")} title={t("Show the robot")}
        className={`mascot-dock fixed z-40 flex h-12 w-7 items-center justify-center overflow-hidden border border-zinc-200/80 bg-white/90 shadow-lg backdrop-blur transition hover:w-9 dark:border-white/10 dark:bg-zinc-900/90 ${place.side === "right" ? "right-0 rounded-l-xl" : "left-0 rounded-r-xl"}`}
        style={{ top: `${place.y * 100}%` }}>
        <span className={`size-2.5 rounded-full ${mood === "waiting" ? "animate-ping-slow bg-amber-400" : mood === "working" ? "animate-pulse bg-sky-400" : "bg-indigo-400"}`} />
      </button>
    );
  }

  const right = place.side === "right";
  const style = drag
    ? { left: drag.x - SIZE / 2, top: drag.y - SIZE / 2 }
    : { [right ? "right" : "left"]: 12, top: `${place.y * 100}%` };
  const live = run && (run.status === "running" || run.status === "waiting");
  const steps = run && Array.isArray(run.steps) ? (run.steps as RobotStep[])
    .filter((s) => (s.kind === "say" && s.text.trim() !== (run.summary || "").trim()) || s.kind === "tool" || s.kind === "ask")
    .map((s) => (s.kind === "say" ? { ...s, text: s.text.replace(/\n?RESULT:.*$/i, "").trim() } : s)).slice(-8) : [];
  // the panel opens beside the robot, inside the window
  const panelW = Math.min(PANEL_W, view.w - 24);
  const panelH = Math.min(576, view.h - 24);
  const panelTop = Math.min(Math.max(12, place.y * view.h + SIZE - panelH), view.h - panelH - 12);
  const panelSide = view.w >= panelW + SIZE + 36 ? SIZE + 24 : 12;

  return (
    <>
      {open && (
        <div role="dialog" aria-label={t("Robot")}
          className="fixed z-50 flex animate-pop flex-col overflow-hidden rounded-2xl border border-zinc-200/80 bg-white/95 shadow-2xl backdrop-blur-xl dark:border-white/10 dark:bg-zinc-900/95"
          style={{ width: panelW, maxHeight: panelH, top: panelTop, [right ? "right" : "left"]: panelSide }}>
          <div className="flex items-center gap-2 border-b border-zinc-200/70 px-3 py-2 dark:border-white/[0.06]">
            <MascotBot mood={mood} size={26} />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">{t("Robot")}</div>
              <div className="truncate text-[11px] text-zinc-500">{mood === "working" ? t("working…") : mood === "waiting" ? t("waiting for you") : mood === "sleep" ? t("monthly token cap reached") : t("ready")}</div>
            </div>
            <button type="button" onClick={() => save({ ...place, side: right ? "left" : "right" })} title={t("Move to the other side")} aria-label={t("Move to the other side")} className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-900/[0.05] dark:hover:bg-white/[0.07]"><ArrowLeftRight className="size-4" /></button>
            <button type="button" onClick={() => { setOpen(false); save({ ...place, hidden: true }); }} title={t("Hide the robot")} aria-label={t("Hide the robot")} className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-900/[0.05] dark:hover:bg-white/[0.07]"><Minus className="size-4" /></button>
            <button type="button" onClick={() => setOpen(false)} aria-label={t("Close")} className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-900/[0.05] dark:hover:bg-white/[0.07]"><X className="size-4" /></button>
          </div>

          <div ref={log} className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-3 py-3 text-sm">
            {waiting?.pending && <Approval run={waiting} busy={!!busy} withGoal onDecide={(approve) => act("decision", { approve }, waiting.id)} />}
            {coach !== null && (
              <PromptCoach compact request={coach} appId={appId} onCancel={() => setCoach(null)}
                onStart={(g) => { setCoach(null); start(g, false); }} />
            )}
            {coach === null && !run && (
              <>
                <p className="rounded-xl rounded-tl-sm bg-zinc-100 px-3 py-2 dark:bg-white/[0.06]">{t("Hi! Ask me anything about your server and your sites: I look and answer without changing anything. Or give me something to do.")}</p>
                <div className="flex flex-wrap gap-1.5">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} type="button" disabled={!!busy || mineLive} onClick={() => start(t(s), true)}
                      className="rounded-full border border-indigo-400/30 px-2.5 py-1 text-xs text-indigo-600 transition hover:bg-indigo-400/10 disabled:opacity-50 dark:text-indigo-300">{t(s)}</button>
                  ))}
                </div>
              </>
            )}
            {coach === null && run && (
              <>
                <p className="ml-8 whitespace-pre-wrap rounded-xl rounded-tr-sm bg-indigo-500 px-3 py-2 text-white">{run.goal}</p>
                {steps.map((s, i) => (
                  s.kind === "say"
                    ? <AiText key={i} className="text-xs text-zinc-600 dark:text-zinc-300">{s.text}</AiText>
                    : <p key={i} className="font-mono text-xs text-zinc-500">{`› ${s.text}`}</p>
                ))}
                {run.status === "waiting" && run.pending && <Approval run={run} busy={!!busy} onDecide={(approve) => act("decision", { approve })} />}
                {run.status === "running" && <p className="flex items-center gap-2 text-xs text-zinc-500"><Spinner />{t("Claude is working…")}</p>}
                {!live && answerOf(run) && (
                  <AiText className={`rounded-xl rounded-tl-sm px-3 py-2 ${run.status === "failed" ? "bg-rose-500/10 text-rose-700 dark:text-rose-300" : "bg-zinc-100 dark:bg-white/[0.06]"}`}>{answerOf(run)}</AiText>
                )}
                <div className="flex items-center justify-between gap-2 text-xs">
                  {live
                    ? <button type="button" disabled={!!busy} onClick={() => act("stop")} className="flex items-center gap-1 text-zinc-500 hover:text-rose-500"><Square className="size-3" />{t("Stop")}</button>
                    : <button type="button" onClick={fresh} className="text-indigo-600 hover:underline dark:text-indigo-300">{t("New question")}</button>}
                  <Link href="/advisor/" onClick={() => setOpen(false)} className="text-zinc-500 hover:underline">{t("Robot page")}</Link>
                </div>
              </>
            )}
            {error && <p className="rounded-lg bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-600 dark:text-rose-300">{error}</p>}
          </div>

          <form className="space-y-2 border-t border-zinc-200/70 p-2.5 dark:border-white/[0.06]" onSubmit={(e) => { e.preventDefault(); start(text, true); }}>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={4000} disabled={mineLive}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); start(text, true); } }}
              placeholder={mineLive ? t("Claude is working…") : t("Ask the robot…")}
              className="w-full resize-none rounded-lg border border-zinc-200 bg-transparent px-2.5 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/60 disabled:opacity-60 dark:border-white/10" />
            <div className="flex items-center gap-2">
              <select value={appId} onChange={(e) => setAppId(e.target.value)} aria-label={t("Site")}
                className="min-w-0 flex-1 rounded-lg border border-zinc-200 bg-transparent px-2 py-1 text-xs dark:border-white/10">
                <option value="">{t("Any site")}</option>
                {(sites ?? []).map((a) => <option key={a.id} value={a.id}>{a.domain || a.name}</option>)}
              </select>
              <button type="button" disabled={!!busy || taskBusy || mineLive || text.trim().length < 5} onClick={() => (refineOn ? setCoach(text.trim()) : start(text, false))} title={t("Let the robot change things (with your settings: it asks first, or acts alone)")}
                className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-900/[0.05] disabled:opacity-40 dark:text-zinc-300 dark:hover:bg-white/[0.07]">
                {busy === "do" ? <Spinner /> : <Wand2 className="size-3.5" />}{t("Do it")}
              </button>
              <Button type="submit" variant="primary" disabled={!!busy || mineLive || text.trim().length < 5}>{busy === "ask" ? <Spinner /> : <Send className="size-3.5" />}{t("Ask")}</Button>
            </div>
          </form>
        </div>
      )}

      <div className="mascot-dock fixed z-40 select-none" style={style}>
        {bubble && !open && (
          <div role="status" onClick={() => setOpen(true)}
            className={`absolute bottom-full cursor-pointer mb-1 w-max max-w-[12rem] animate-pop rounded-xl border px-2.5 py-1.5 text-xs shadow-lg ${right ? "right-0" : "left-0"} ${mood === "waiting" ? "border-amber-400/40 bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200" : "border-zinc-200 bg-white text-zinc-700 dark:border-white/10 dark:bg-zinc-900 dark:text-zinc-200"}`}>
            {bubble}
          </div>
        )}
        <button type="button" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => { press.current = null; setDrag(null); }}
          aria-label={open ? t("Close the robot") : t("Ask the robot")} aria-expanded={open}
          title={t("Click to ask the robot, drag to move it")}
          className={`block touch-none rounded-2xl outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 ${drag ? "cursor-grabbing" : "cursor-pointer"}`}>
          <MascotBot mood={mood} size={SIZE} wave={!!bubble && mood !== "waiting"} className="drop-shadow-[0_6px_14px_rgba(79,70,229,0.25)]" />
        </button>
      </div>
    </>
  );
}
