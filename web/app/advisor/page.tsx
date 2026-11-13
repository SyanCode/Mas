"use client";

import { useCallback, useEffect, useState } from "react";
import { Bot, Check, Lightbulb, Sparkles, UserRoundCog, X } from "lucide-react";
import { AiAccountsList } from "@/components/AiAccounts";
import { RobotPanel } from "@/components/Robot";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ErrorText, PageHeader, Select, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo, type AdvisorState, type App, type Suggestion } from "@/lib/api";
import { useT } from "@/lib/i18n";

const TONE = { critical: "red", warning: "amber", info: "sky" } as const;
const FILTERS = ["new", "done", "dismissed"] as const;
type Tab = "robot" | "proposals" | "accounts";

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

  const shown = state.suggestions.filter((s) => s.status === filter);
  const count = (f: string) => state.suggestions.filter((s) => s.status === f).length;
  return (
    <div className="space-y-4">
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
              <div className="mt-3 flex flex-wrap gap-2">
                {s.status === "new" ? (
                  <>
                    <Button variant="glow" onClick={() => onRobot(s)}><Bot className="size-3.5" />{t("Let the robot do it")}</Button>
                    <Button variant="secondary" onClick={() => mark(s, "done")}><Check className="size-3.5" />{t("Done")}</Button>
                    <Button variant="ghost" onClick={() => mark(s, "dismissed")}><X className="size-3.5" />{t("Dismiss")}</Button>
                  </>
                ) : <Button variant="ghost" onClick={() => mark(s, "new")}>{t("Back to review")}</Button>}
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
