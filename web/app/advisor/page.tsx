"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, Sparkles, X } from "lucide-react";
import { AdvisorConnect } from "@/components/AdvisorConnect";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ErrorText, PageHeader, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo, type AdvisorState, type Suggestion } from "@/lib/api";
import { useT } from "@/lib/i18n";

const TONE = { critical: "red", warning: "amber", info: "sky" } as const;
const FILTERS = ["new", "done", "dismissed"] as const;

function Advisor() {
  const t = useT();
  const [state, setState] = useState<AdvisorState | null>(null);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("new");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [setup, setSetup] = useState(false);

  const load = useCallback(() => {
    api<AdvisorState>("/advisor").then(setState).catch((e: ApiError) => setError(e.detail));
  }, []);
  useEffect(load, [load]);

  async function analyse() {
    setBusy(true); setError(""); setNote("");
    try {
      const r = await api<AdvisorState & { added: number }>("/advisor/run", { method: "POST" });
      setState(r);
      setNote(r.added ? t("{n} new proposal(s)", { n: r.added }) : t("Nothing new: the server looks fine."));
      setFilter("new");
    } catch (e) {
      setError((e as ApiError).detail);
      load();
    } finally {
      setBusy(false);
    }
  }

  async function mark(s: Suggestion, status: Suggestion["status"]) {
    setState((cur) => cur && { ...cur, suggestions: cur.suggestions.map((x) => (x.id === s.id ? { ...x, status } : x)) });
    try {
      await api(`/advisor/suggestions/${s.id}`, { method: "PUT", body: { status } });
    } catch (e) {
      setError((e as ApiError).detail);
      load();
    }
  }

  if (!state) return <div className="flex justify-center py-24"><Spinner /></div>;

  const shown = state.suggestions.filter((s) => s.status === filter);
  const count = (f: string) => state.suggestions.filter((s) => s.status === f).length;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={t("Claude")}
        title={t("Advisor")}
        subtitle={t("Claude looks at your server and proposes improvements, on its own.")}
        actions={state.connected && (
          <Button variant="glow" onClick={analyse} disabled={busy}>
            {busy ? <Spinner /> : <Sparkles className="size-3.5" />}{busy ? t("Claude is thinking…") : t("Analyse now")}
          </Button>
        )}
      />
      {busy && <div className="progress-sweep" />}
      <ErrorText>{error}</ErrorText>
      {note && <p className="animate-pop rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">{note}</p>}

      {!state.connected ? (
        <Card title={t("Connect Claude")} className="ring-gradient">
          <div className="mb-4 flex items-center gap-3">
            <Sparkles className="sparkle size-8 text-indigo-400" />
            <p className="text-sm text-zinc-500">{t("Plug in a Claude Code token and Claude analyses the server every day: stability, security, memory, certificates. You accept or dismiss each proposal.")}</p>
          </div>
          <AdvisorConnect state={state} onChange={() => load()} />
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-lg border border-zinc-200/80 p-0.5 dark:border-white/[0.08]">
              {FILTERS.map((f) => (
                <button key={f} type="button" onClick={() => setFilter(f)} aria-pressed={filter === f}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition-all duration-200 ${filter === f ? "bg-zinc-900 text-white shadow dark:bg-white dark:text-zinc-950" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-white"}`}>
                  {f === "new" ? t("To review") : f === "done" ? t("Done") : t("Dismissed")} <span className="tabular-nums opacity-60">{count(f)}</span>
                </button>
              ))}
            </div>
            <button type="button" onClick={() => setSetup((o) => !o)} className="ml-auto flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-white">
              {t("Connection")}<ChevronDown className={`size-3.5 transition-transform duration-200 ${setup ? "rotate-180" : ""}`} />
            </button>
          </div>
          {setup && <Card title={t("Connection")} className="animate-pop"><AdvisorConnect state={state} onChange={() => load()} /></Card>}

          {shown.length === 0 ? (
            <Card><p className="py-6 text-center text-sm text-zinc-500">{filter === "new" ? t("No proposal waiting. Claude will tell you when it finds something.") : t("Nothing here yet.")}</p></Card>
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
                        <Button variant="primary" onClick={() => mark(s, "done")}><Check className="size-3.5" />{t("Done")}</Button>
                        <Button variant="ghost" onClick={() => mark(s, "dismissed")}><X className="size-3.5" />{t("Dismiss")}</Button>
                      </>
                    ) : (
                      <Button variant="ghost" onClick={() => mark(s, "new")}>{t("Back to review")}</Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

export default function Page() {
  return <Shell><Advisor /></Shell>;
}
