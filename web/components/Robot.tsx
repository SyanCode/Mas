"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, Check, CircleSlash, Hammer, Play, ShieldCheck, Square, Wrench, X } from "lucide-react";
import { Badge, Button, Card, ErrorText, Input, Label, Select, Spinner, Textarea } from "@/components/ui";
import { api, ApiError, timeAgo, type App, type RobotRun, type RobotState, type RobotStep, type RobotUsage } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";
import { PromptCoach } from "@/components/PromptCoach";

const PRESET_LABEL: Record<string, string> = {
  polish: msg("Not vibecoded"), launch: msg("Ready to launch"),
  compliance: msg("GDPR compliance"), seo: msg("SEO"), style: msg("Style and mobile"), maintenance: msg("Maintenance round"),
};
const PRESET_HINT: Record<string, string> = {
  polish: msg("Placeholders, template leftovers, generic copy, emoji icons, dead links, favicon and 404 page."),
  launch: msg("Domain, HTTPS, SEO, compliance and finish: the whole launch checklist, fixed or explained."),
  compliance: msg("Cookie banner, legal pages, consent, accessibility: audit, fix, audit again."),
  seo: msg("robots.txt, sitemap, titles, descriptions, headings, image texts."),
  style: msg("Spacing, typography, contrast, responsive layout, tasteful animations."),
  maintenance: msg("Audit compliance and SEO, fix what is safe, report the rest."),
};
function kilo(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

/** Tokens of a run (or of the month) and the share of the input read from the prompt cache. */
function Usage({ u }: { u?: RobotUsage | null }) {
  const t = useT();
  if (!u) return null;
  const read = u.input + u.cache_write + u.cache_read;
  const total = read + u.output;
  if (!total) return null;
  const cached = read ? Math.round((u.cache_read / read) * 100) : 0;
  const detail = t("{a} input, {b} written to the cache, {c} read from the cache, {d} output", { a: u.input, b: u.cache_write, c: u.cache_read, d: u.output });
  return (
    <span className="text-xs tabular-nums text-zinc-500" title={detail}>
      {t("{n} tokens", { n: kilo(total) })} · {t("{p}% from cache", { p: cached })}
    </span>
  );
}

/** Tokens of the month (robot, advisor, questions) and the cap that stops every AI use when it is spent. */
function Budget({ state, onSave }: { state: RobotState; onSave: (cap: number) => void }) {
  const t = useT();
  const b = state.budget;
  const [cap, setCap] = useState("");
  useEffect(() => { setCap(b?.cap ? String(Math.round(b.cap / 1000)) : ""); }, [b?.cap]);
  if (!b) return null;
  const share = b.cap ? Math.min(100, Math.round((b.weighted / b.cap) * 100)) : 0;
  const sources: Record<string, string> = { robot: t("robot"), advisor: t("advisor"), other: t("other") };
  return (
    <Card title={t("Monthly token budget")}>
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span><span className="text-lg font-semibold tabular-nums">{kilo(b.weighted)}</span> <span className="text-zinc-500">{b.cap ? t("of {n} tokens this month", { n: kilo(b.cap) }) : t("tokens this month, no cap")}</span></span>
          <span className="text-xs text-zinc-500">{Object.entries(b.by_source).map(([k, u]) => `${sources[k] ?? k} ${kilo(u.input + u.cache_write + u.output + Math.floor(u.cache_read / 10))}`).join(" · ")}</span>
        </div>
        {b.cap > 0 && (
          <div className="h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-white/10" role="progressbar" aria-valuenow={share} aria-valuemin={0} aria-valuemax={100}>
            <div className={`h-full rounded-full ${share >= 100 ? "bg-rose-500" : share >= 80 ? "bg-amber-500" : "bg-indigo-500"}`} style={{ width: `${share}%` }} />
          </div>
        )}
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); onSave(Math.max(0, Math.round(Number(cap || 0) * 1000))); }}>
          <Label title={t("Cap (thousands of tokens a month)")} hint={t("When it is spent, the robot, the autopilot, the advisor and the questions stop until next month. Cache reads count for a tenth. Empty: no cap.")}>
            <Input type="number" min={0} step={100} value={cap} onChange={(e) => setCap(e.target.value)} placeholder={t("no cap")} className="w-40" />
          </Label>
          <Button type="submit">{t("Save")}</Button>
        </form>
      </div>
    </Card>
  );
}

const TONE = { running: "sky", waiting: "amber", done: "emerald", failed: "red", stopped: "zinc" } as const;

function Step({ s }: { s: RobotStep }) {
  const t = useT();
  const base = "animate-fade-up flex gap-2.5 text-sm";
  if (s.kind === "say") return <li className={base}><Bot className="mt-0.5 size-4 shrink-0 text-indigo-400" /><p className="min-w-0 whitespace-pre-wrap break-words">{s.text}</p></li>;
  if (s.kind === "tool") return <li className={`${base} text-zinc-500`}><Wrench className="mt-0.5 size-4 shrink-0" /><span className="mono min-w-0 break-words text-xs">{s.text}</span></li>;
  if (s.kind === "result") return <li className={`${base} pl-6 text-xs text-zinc-500`}><span className="mono min-w-0 break-words">{s.text}</span></li>;
  if (s.kind === "ask") return <li className={`${base} text-amber-600 dark:text-amber-300`}><ShieldCheck className="mt-0.5 size-4 shrink-0" /><span className="min-w-0 break-words">{s.text}</span></li>;
  if (s.kind === "approved") return <li className={`${base} text-emerald-600 dark:text-emerald-400`}><Check className="mt-0.5 size-4 shrink-0" />{t("Approved")}</li>;
  if (s.kind === "denied") return <li className={`${base} text-rose-500`}><CircleSlash className="mt-0.5 size-4 shrink-0" />{t("Refused")}</li>;
  return <li className={`${base} border-t border-zinc-200/80 pt-3 font-medium dark:border-white/[0.06]`}><Hammer className="mt-0.5 size-4 shrink-0" /><p className="min-w-0 whitespace-pre-wrap break-words">{s.text}</p></li>;
}

/** The robot: a goal in a few words, then Claude (or another model) works on the site, deploys and reports, live. */
export function RobotPanel({ apps, goal, onGoalUsed }: { apps: App[]; goal?: { text: string; app?: string } | null; onGoalUsed?: () => void }) {
  const t = useT();
  const [state, setState] = useState<RobotState | null>(null);
  const [text, setText] = useState("");
  const [coach, setCoach] = useState<string | null>(null);  // a request being turned into a precise goal
  const [appId, setAppId] = useState("");
  const [autonomy, setAutonomy] = useState<"ask" | "auto">("ask");
  const [run, setRun] = useState<RobotRun | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const log = useRef<HTMLUListElement>(null);
  const sites = apps.filter((a) => a.repo && a.installation_id);

  const load = useCallback(() => {
    api<RobotState>("/robot").then((r) => { setState(r); setAutonomy((cur) => (state ? cur : r.autonomy)); }).catch((e: ApiError) => setError(e.detail));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(load, [load]);
  useEffect(() => { if (state?.active && !run) api<RobotRun>(`/robot/runs/${state.active}`).then(setRun).catch(() => {}); }, [state, run]);
  useEffect(() => { if (goal) { setText(goal.text); setAppId(goal.app ?? ""); onGoalUsed?.(); } }, [goal, onGoalUsed]);

  const live = run && (run.status === "running" || run.status === "waiting");
  useEffect(() => {  // follow the run
    if (!live) return;
    const id = setInterval(() => api<RobotRun>(`/robot/runs/${run!.id}`).then((r) => { setRun(r); if (r.status !== "running" && r.status !== "waiting") load(); }).catch(() => {}), 1500);
    return () => clearInterval(id);
  }, [live, run, load]);
  useEffect(() => { log.current?.lastElementChild?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [run?.steps]);

  async function start(goalText: string) {
    setBusy("start"); setError("");
    try {
      const { id } = await api<{ id: number }>("/robot/runs", { method: "POST", body: { goal: goalText, app_id: appId, autonomy } });
      setRun(await api<RobotRun>(`/robot/runs/${id}`));
      setText("");
    } catch (e) { setError((e as ApiError).detail); } finally { setBusy(""); }
  }
  async function act(path: string, body?: object) {
    setBusy(path); setError("");
    try { await api(`/robot/runs/${run!.id}/${path}`, { method: "POST", body }); setRun(await api<RobotRun>(`/robot/runs/${run!.id}`)); } catch (e) { setError((e as ApiError).detail); } finally { setBusy(""); }
  }
  async function save(body: { schedule?: string; autonomy?: string; monthly_cap?: number; refine?: boolean }) {
    try { await api("/robot", { method: "PUT", body }); load(); } catch (e) { setError((e as ApiError).detail); }
  }

  const steps = (run && Array.isArray(run.steps) ? run.steps : []) as RobotStep[];

  return (
    <div className="space-y-4">
      <ErrorText>{error}</ErrorText>
      {live && <div className="progress-sweep" />}
      {run && (
        <Card title={<span className="flex items-center gap-2"><Bot className={`size-4 ${live ? "animate-float text-indigo-400" : ""}`} />{t("Robot")} <Badge tone={TONE[run.status]}>{run.status === "running" ? t("working") : run.status === "waiting" ? t("waiting for you") : run.status === "done" ? t("done") : run.status === "failed" ? t("failed") : t("stopped")}</Badge></span>}
          actions={live ? <Button variant="ghost" disabled={!!busy} onClick={() => act("stop")}><Square className="size-3.5" />{t("Stop")}</Button> : <Button variant="ghost" onClick={() => setRun(null)}>{t("Close")}</Button>}>
          <p className="mb-3 flex flex-wrap items-baseline justify-between gap-2 text-sm text-zinc-500"><span>{run.goal}</span><Usage u={run.usage} /></p>
          <ul ref={log} className="max-h-[28rem] space-y-2.5 overflow-y-auto pr-1">{steps.map((s, i) => <Step key={i} s={s} />)}{live && run.status === "running" && <li className="flex items-center gap-2 text-xs text-zinc-500"><Spinner />{t("Claude is working…")}</li>}</ul>
          {run.pending && (
            <div className="animate-pop mt-4 space-y-3 rounded-xl border border-amber-400/40 bg-amber-400/10 p-4">
              <div className="text-sm font-medium">{t("The robot asks your permission")}</div>
              <p className="mono break-words text-xs">{run.pending.detail}</p>
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" disabled={!!busy} onClick={() => act("decision", { approve: true })}><Check className="size-3.5" />{t("Allow")}</Button>
                <Button variant="danger" disabled={!!busy} onClick={() => act("decision", { approve: false })}><X className="size-3.5" />{t("Refuse")}</Button>
              </div>
            </div>
          )}
        </Card>
      )}

      {!live && coach !== null && (
        <Card title={t("Preparing the goal")}>
          <PromptCoach request={coach} appId={appId} onCancel={() => setCoach(null)} onStart={(g) => { setCoach(null); start(g); }} />
        </Card>
      )}

      {!live && coach === null && (
        <Card title={t("What should the robot do?")}>
          <div className="space-y-4 text-sm">
            <div className="stagger grid gap-2 sm:grid-cols-2">
              {(state?.presets ?? ["polish", "launch", "compliance", "seo", "style", "maintenance"]).map((p) => (
                <button key={p} type="button" disabled={!!busy || !state} onClick={() => start(p)}
                  className="glass lift rounded-xl p-3 text-left disabled:opacity-50">
                  <div className="flex items-center gap-2 font-medium"><Play className="size-3.5 text-indigo-400" />{t(PRESET_LABEL[p] ?? p)}</div>
                  <div className="mt-1 text-xs text-zinc-500">{t(PRESET_HINT[p] ?? "")}</div>
                </button>
              ))}
            </div>
            {state?.skills && state.skills.length > 0 && (
              <div className="text-xs text-zinc-500">
                <span className="mr-1.5">{t("Skills it loads when the goal needs them:")}</span>
                {state.skills.map((k) => <span key={k.name} title={k.description} className="mr-1.5 inline-block"><Badge tone="zinc">{k.name}</Badge></span>)}
              </div>
            )}
            <Label title={t("Or describe it")}>
              <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder={t("e.g. Add a FAQ section to the home page and make the buttons rounder")} maxLength={4000} />
            </Label>
            <div className="grid gap-3 sm:grid-cols-2">
              <Label title={t("Site")}>
                <Select value={appId} onChange={(e) => setAppId(e.target.value)}>
                  <option value="">{t("Any site")}</option>
                  {sites.map((a) => <option key={a.id} value={a.id}>{a.name}{a.domain ? ` · ${a.domain}` : ""}</option>)}
                </Select>
              </Label>
              <Label title={t("Autonomy")}>
                <Select value={autonomy} onChange={(e) => setAutonomy(e.target.value as "ask" | "auto")}>
                  <option value="ask">{t("Ask me before each change")}</option>
                  <option value="auto">{t("Fully automatic: change, deploy, check")}</option>
                </Select>
              </Label>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="glow" disabled={!!busy || text.trim().length < 5 || !state} onClick={() => (state?.refine ? setCoach(text.trim()) : start(text))}>
                {busy === "start" ? <Spinner /> : <Play className="size-3.5" />}{t("Start")}
              </Button>
              {state && (
                <label className="flex items-center gap-2 text-xs text-zinc-500">
                  <input type="checkbox" checked={state.refine !== false} onChange={(e) => save({ refine: e.target.checked })} />
                  {t("Improve my request first: Claude asks for missing details and writes a precise goal")}
                </label>
              )}
            </div>
            <p className="text-xs text-zinc-500">{autonomy === "auto" ? t("In automatic mode the robot commits and deploys alone. Mas still checks nginx, health-checks the new release and rolls back by itself; every commit is in your repository history.") : t("The robot reads and audits freely, but every commit, deployment and fix waits for your click.")}</p>
          </div>
        </Card>
      )}

      {state && !live && (
        <Budget state={state} onSave={(cap) => save({ monthly_cap: cap })} />
      )}

      {state && !live && (
        <Card title={t("Automatic maintenance")}>
          <div className="grid max-w-xl gap-3 text-sm sm:grid-cols-2">
            <Label title={t("Every")} hint={t("The robot audits compliance and SEO of your sites and fixes what is safe.")}>
              <Select value={state.schedule} onChange={(e) => save({ schedule: e.target.value })}>
                <option value="off">{t("never")}</option>
                <option value="daily">{t("every day")}</option>
                <option value="weekly">{t("every week")}</option>
              </Select>
            </Label>
            <Label title={t("Autonomy")}>
              <Select value={state.autonomy} onChange={(e) => save({ autonomy: e.target.value })}>
                <option value="ask">{t("Ask me before each change")}</option>
                <option value="auto">{t("Fully automatic: change, deploy, check")}</option>
              </Select>
            </Label>
          </div>
        </Card>
      )}

      {state && state.runs.length > 0 && !live && (
        <Card title={t("Previous runs")} padded={false}
          actions={state.usage_30d && state.usage_30d.calls ? <span className="flex items-center gap-1.5 text-xs text-zinc-500">{t("Last 30 days:")} <Usage u={state.usage_30d} /></span> : undefined}>
          <ul className="divide-y divide-zinc-200/80 dark:divide-white/[0.06]">
            {state.runs.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => api<RobotRun>(`/robot/runs/${r.id}`).then(setRun)} className="flex w-full flex-wrap items-center gap-2 px-4 py-3 text-left text-sm transition hover:bg-zinc-900/[0.03] dark:hover:bg-white/[0.04]">
                  <Badge tone={TONE[r.status]}>{r.status}</Badge>
                  <span className="min-w-0 flex-1 truncate">{r.goal}</span>
                  <Usage u={r.usage} />
                  <span className="text-xs text-zinc-500">{timeAgo(new Date(r.created_at * 1000).toISOString())}</span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
