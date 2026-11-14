"use client";

import { useCallback, useEffect, useState } from "react";
import { Bot, Check, Lightbulb, Sparkles, UserRoundCog, X } from "lucide-react";
import { AiAccountsList } from "@/components/AiAccounts";
import { RobotPanel } from "@/components/Robot";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ErrorText, PageHeader, Select, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo, type AdvisorState, type App, type Autopilot, type Suggestion } from "@/lib/api";
import { useT } from "@/lib/i18n";

const TONE = { critical: "red", warning: "amber", info: "sky" } as const;
const FILTERS = ["new", "done", "dismissed"] as const;
const OPEN = ["new", "working", "failed"];  // what still waits for somebody
type Tab = "robot" | "proposals" | "accounts";

function AutopilotCard({ state, onChange }: { state: AdvisorState; onChange: (s: AdvisorState) => void }) {
  const t = useT();
  const ap = state.autopilot;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  async function applyAll() {
    setBusy(true); setError(""); setNote("");
    try {
      const r = await api<AdvisorState & { added: number; queued: number; note: string }>("/advisor/apply-all", { method: "POST" });
      onChange(r);
      setNote(r.queued ? t("{n} proposal(s) queued: the robot applies them one after the other.", { n: r.queued }) : t("Nothing waits for the robot."));
    } catch (e) { setError((e as ApiError).detail); } finally { setBusy(false); }
  }
  async function stop() {
    setBusy(true); setError(""); setNote("");
    try {
      const r = await api<{ autopilot: Autopilot }>("/advisor/autopilot/stop", { method: "POST" });
      onChange({ ...state, autopilot: r.autopilot });
      setNote(t("Stopped. Nothing else starts until you click again."));
    } catch (e) { setError((e as ApiError).detail); } finally { setBusy(false); }
  }
  async function standing(enabled: boolean) {
    setError("");
    try { onChange({ ...state, autopilot: await api<Autopilot>("/advisor/autopilot", { method: "PUT", body: { enabled } }) }); } catch (e) { setError((e as ApiError).detail); }
  }

  const working = ap.current;
  const queued = ap.batch || ap.enabled;
  const waiting = !working && ap.left > 0 && queued;  // idle, but something is queued: say why
  return (
    <Card title={t("Apply the proposals")} actions={working ? <Badge tone="violet">{t("working")}</Badge> : ap.enabled ? <Badge tone="emerald">{t("automatic")}</Badge> : undefined}>
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="glow" onClick={applyAll} disabled={busy || !!working || (ap.batch && ap.left > 0 && !ap.paused && !ap.limited_until && !ap.next_try)}>
            {busy ? <Spinner /> : <Bot className="size-3.5" />}{t("Apply everything")}{ap.left > 0 ? ` (${ap.left})` : ""}
          </Button>
          {(working || ap.batch || ap.enabled || ap.blocked) && <Button variant="secondary" onClick={stop} disabled={busy}><X className="size-3.5" />{t("Stop")}</Button>}
        </div>
        {working && (
          <div className="rounded-lg border border-violet-400/30 bg-violet-500/10 px-3 py-2 text-violet-800 dark:text-violet-200">
            <div className="flex items-center gap-2 font-medium"><Spinner />{t("Working on: {title}", { title: working.title })}</div>
            <div className="mt-1 text-xs opacity-80">
              {working.since ? t("started at {time}", { time: clock(working.since) }) : ""}{working.last_step ? ` · ${working.last_step}` : ""}
            </div>
          </div>
        )}
        {waiting && !ap.paused && (
          <p className="text-zinc-600 dark:text-zinc-400">
            {ap.limited_until ? t("Every AI account is at its limit: waiting for the reset ({time}).", { time: clock(ap.limited_until) })
              : ap.blocked?.reason === "robot_busy" ? t("The robot is still busy with an earlier run (#{id}): press Stop to free it.", { id: ap.blocked.detail })
              : ap.blocked?.reason === "ai_disabled" ? t("Every AI account is switched off ({detail}): fix it in the AI accounts tab.", { detail: ap.blocked.detail })
              : ap.blocked?.reason === "no_account" ? t("Connect an AI account first (AI accounts tab).")
              : ap.blocked?.reason === "cap" ? t("The daily cap ({n}) is reached: it carries on tomorrow.", { n: ap.blocked.detail })
              : ap.next_try ? t("A proposal failed: next try at {time}.", { time: clock(ap.next_try) })
              : t("Starting the next proposal…")}
          </p>
        )}
        {ap.error && <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-rose-700 dark:text-rose-300">{t("Something went wrong in the autopilot: {error}", { error: ap.error })}</p>}
        {ap.paused && <p className="rounded-lg border border-amber-400/30 bg-amber-500/10 px-3 py-2 text-amber-800 dark:text-amber-300">{ap.paused}</p>}
        <p className="text-zinc-600 dark:text-zinc-400">
          {t("One click: if nothing waits Claude analyses first, then the robot makes every change (on your sites, or on the server with Mas's own fixes), deploys it and closes the proposal with its report. What the robot cannot do, it says so and leaves it to you. Mas still checks nginx and the health of each release and goes back on its own if it fails. It goes as far as your AI accounts' usage limit allows, and carries on by itself when the limit resets.")}
        </p>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={ap.enabled} onChange={(e) => standing(e.target.checked)} />
          {t("Also apply every new proposal as soon as it comes, without a click")}
        </label>
        {note && <p className="text-emerald-700 dark:text-emerald-400">{note}</p>}
        <ErrorText>{error}</ErrorText>
      </div>
    </Card>
  );
}

function Proposals({ state, setState, onRobot }: { state: AdvisorState; setState: (s: AdvisorState) => void; onRobot: (s: Suggestion) => void }) {
  const t = useT();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("new");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  async function analyse() {
    setBusy(true); setError(""); setNote("");
    try {
      const r = await api<AdvisorState & { added: number }>("/advisor/run", { method: "POST" });
      setState(r);
      setNote(r.added ? t("{n} new proposal(s)", { n: r.added }) : t("Nothing new: the server looks fine."));
      setFilter("new");
    } catch (e) { setError((e as ApiError).detail); } finally { setBusy(false); }
  }
  async function mark(s: Suggestion, status: Suggestion["status"]) {
    setState({ ...state, suggestions: state.suggestions.map((x) => (x.id === s.id ? { ...x, status } : x)) });
    try { await api(`/advisor/suggestions/${s.id}`, { method: "PUT", body: { status } }); } catch (e) { setError((e as ApiError).detail); }
  }
  async function interval(value: string) {
    try { setState({ ...state, ...(await api<AdvisorState>("/advisor", { method: "PUT", body: { interval: value } })) }); } catch (e) { setError((e as ApiError).detail); }
  }

  const matches = (s: Suggestion, f: string) => (f === "new" ? OPEN.includes(s.status) : s.status === f);
  const shown = state.suggestions.filter((s) => matches(s, filter));
  const count = (f: string) => state.suggestions.filter((s) => matches(s, f)).length;
  return (
    <div className="space-y-4">
      <AutopilotCard state={state} onChange={setState} />
      <ErrorText>{error}</ErrorText>
      {busy && <div className="progress-sweep" />}
      {note && <p className="animate-pop rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">{note}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-zinc-200/80 p-0.5 dark:border-white/[0.08]">
          {FILTERS.map((f) => (
            <button key={f} type="button" onClick={() => setFilter(f)} aria-pressed={filter === f}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-all duration-200 ${filter === f ? "bg-zinc-900 text-white shadow dark:bg-white dark:text-zinc-950" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-white"}`}>
              {f === "new" ? t("To review") : f === "done" ? t("Done") : t("Dismissed")} <span className="tabular-nums opacity-60">{count(f)}</span>
            </button>
          ))}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="w-44"><Select value={state.interval} onChange={(e) => interval(e.target.value)} aria-label={t("Analyse")}>
            <option value="daily">{t("every day")}</option><option value="weekly">{t("every week")}</option><option value="off">{t("only when I ask")}</option>
          </Select></div>
          <Button variant="glow" onClick={analyse} disabled={busy}>{busy ? <Spinner /> : <Sparkles className="size-3.5" />}{busy ? t("Claude is thinking…") : t("Analyse now")}</Button>
        </div>
      </div>
      {state.error && <ErrorText>{state.error}</ErrorText>}
      {shown.length === 0 ? (
        <Card><p className="py-6 text-center text-sm text-zinc-500">{filter === "new" ? t("No proposal waiting. The AI will tell you when it finds something.") : t("Nothing here yet.")}</p></Card>
      ) : (
        <ul className="stagger space-y-3">
          {shown.map((s) => (
            <li key={s.id} className="glass lift rounded-2xl p-4 sm:p-5">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={TONE[s.severity]}>{s.severity === "critical" ? t("Critical") : s.severity === "warning" ? t("Warning") : t("Tip")}</Badge>
                {s.target && <Badge>{s.target}</Badge>}
                <span className="ml-auto text-xs text-zinc-500">{timeAgo(new Date(s.created_at * 1000).toISOString())}</span>
              </div>
              <h3 className="mt-2 text-[15px] font-semibold tracking-tight">{s.title}</h3>
              <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{s.detail}</p>
              {s.status === "working" && <p className="mt-2 flex items-center gap-2 text-sm text-indigo-600 dark:text-indigo-300"><Spinner />{t("The robot is working on it…")}</p>}
              {s.status === "failed" && <p className="mt-2 text-sm text-rose-600 dark:text-rose-400">{t("The robot could not do it: it needs you.")}</p>}
              {s.result && s.status !== "working" && <p className="mt-2 whitespace-pre-line rounded-lg bg-zinc-900/[0.04] px-3 py-2 text-xs text-zinc-600 dark:bg-white/[0.05] dark:text-zinc-400">{s.result}</p>}
              <div className="mt-3 flex flex-wrap gap-2">
                {s.status === "new" || s.status === "failed" ? (
                  <>
                    <Button variant="glow" onClick={() => onRobot(s)}><Bot className="size-3.5" />{t("Let the robot do it")}</Button>
                    <Button variant="secondary" onClick={() => mark(s, "done")}><Check className="size-3.5" />{t("Done")}</Button>
                    <Button variant="ghost" onClick={() => mark(s, "dismissed")}><X className="size-3.5" />{t("Dismiss")}</Button>
                  </>
                ) : s.status === "working" ? null : <Button variant="ghost" onClick={() => mark(s, "new")}>{t("Back to review")}</Button>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Advisor() {
  const t = useT();
  const [tab, setTab] = useState<Tab>("robot");
  const [state, setState] = useState<AdvisorState | null>(null);
  const [apps, setApps] = useState<App[]>([]);
  const [goal, setGoal] = useState<{ text: string; app?: string } | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<AdvisorState>("/advisor").then((s) => { setState(s); setTab((cur) => (cur === "robot" && !s.connected ? "accounts" : cur)); }).catch((e: ApiError) => setError(e.detail));
    api<App[]>("/apps").then(setApps).catch(() => {});
  }, []);
  useEffect(load, [load]);
  const busyQueue = !!state && (state.autopilot.batch || state.autopilot.enabled || state.autopilot.working !== null);
  useEffect(() => {  // the statuses follow the robot while it works through the queue
    if (!busyQueue) return;
    const id = setInterval(() => { api<AdvisorState>("/advisor").then(setState).catch(() => {}); }, 5000);
    return () => clearInterval(id);
  }, [busyQueue]);

  if (!state) return <div className="flex justify-center py-24"><Spinner /></div>;
  const tabs: { id: Tab; label: string; icon: typeof Bot; count?: number }[] = [
    { id: "robot", label: t("Robot"), icon: Bot },
    { id: "proposals", label: t("Proposals"), icon: Lightbulb, count: state.new },
    { id: "accounts", label: t("AI accounts"), icon: UserRoundCog },
  ];
  const toRobot = (s: Suggestion) => {
    const app = apps.find((a) => s.target && (a.name === s.target || a.id === s.target || a.domain === s.target));
    setGoal({ text: `${s.title}. ${s.detail}`, app: app?.id });
    setTab("robot");
  };

  return (
    <div className="space-y-6">
      <PageHeader eyebrow={t("Claude and friends")} title={t("Advisor")} subtitle={t("An AI looks at your sites and server, proposes improvements and, if you let it, makes them and deploys by itself.")} />
      <ErrorText>{error}</ErrorText>
      <div className="no-scrollbar -mx-1 flex gap-1 overflow-x-auto px-1">
        {tabs.map(({ id, label, icon: Icon, count }) => (
          <button key={id} type="button" onClick={() => setTab(id)} aria-pressed={tab === id}
            className={`flex shrink-0 items-center gap-2 rounded-xl px-3.5 py-2 text-sm font-medium transition-all duration-200 ${tab === id ? "bg-zinc-900 text-white shadow-lg dark:bg-white dark:text-zinc-950" : "text-zinc-500 hover:bg-zinc-900/[0.05] hover:text-zinc-900 dark:hover:bg-white/[0.06] dark:hover:text-white"}`}>
            <Icon className="size-4" />{label}
            {!!count && <span className="rounded-full bg-indigo-500 px-1.5 text-[10px] font-semibold tabular-nums text-white">{count}</span>}
          </button>
        ))}
      </div>
      <div key={tab} className="page-in">
        {tab === "robot" && (state.connected ? <RobotPanel apps={apps} goal={goal} onGoalUsed={() => setGoal(null)} /> : <Card><p className="text-sm text-zinc-500">{t("Connect an AI account first.")}</p></Card>)}
        {tab === "proposals" && (state.connected ? <Proposals state={state} setState={setState} onRobot={toRobot} /> : <Card><p className="text-sm text-zinc-500">{t("Connect an AI account first.")}</p></Card>)}
        {tab === "accounts" && <Card title={t("AI accounts")} className="ring-gradient"><AiAccountsList onChange={(a) => setState((cur) => cur && (cur.connected === (a.length > 0) ? cur : { ...cur, connected: a.length > 0 }))} /></Card>}
      </div>
    </div>
  );
}

export default function Page() {
  return <Shell><Advisor /></Shell>;
}
