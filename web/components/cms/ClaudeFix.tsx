"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bot, Check, CircleSlash, Loader2, ShieldCheck, Square, Wrench, X } from "lucide-react";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { api, type ApiError, type RobotRun, type RobotStep } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useCms } from "@/components/cms/CmsLayout";

/** "Fix with Claude" on a CMS page: the robot works on this site (audit, edit, deploy, audit again), live. */
export function ClaudeFix({ preset, title, description, onDone }: { preset: "compliance" | "seo"; title: string; description: string; onDone?: () => void }) {
  const t = useT();
  const { site, session } = useCms();
  const [connected, setConnected] = useState<boolean | null>(null);
  const [autonomy, setAutonomy] = useState<"ask" | "auto">("ask");
  const [run, setRun] = useState<RobotRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const log = useRef<HTMLUListElement>(null);
  const admin = session.role === "admin";

  useEffect(() => {
    if (!admin) return;
    api<{ connected: boolean }>("/advisor").then((r) => setConnected(r.connected)).catch(() => setConnected(false));
  }, [admin]);

  const live = !!run && (run.status === "running" || run.status === "waiting");
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => api<RobotRun>(`/robot/runs/${run!.id}`).then((r) => {
      setRun(r);
      if (r.status !== "running" && r.status !== "waiting") onDone?.();
    }).catch(() => {}), 1500);
    return () => clearInterval(id);
  }, [live, run, onDone]);
  useEffect(() => { log.current?.lastElementChild?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [run?.steps]);

  if (!admin || !site || connected === null) return null;

  async function post(path: string, body?: object) {
    setBusy(true); setError("");
    try { await api(path, { method: "POST", body }); if (run) setRun(await api<RobotRun>(`/robot/runs/${run.id}`)); } catch (e) { setError((e as ApiError).detail); } finally { setBusy(false); }
  }
  async function start() {
    setBusy(true); setError("");
    try {
      const { id } = await api<{ id: number }>("/robot/runs", { method: "POST", body: { goal: preset, app_id: site!.id, autonomy } });
      setRun(await api<RobotRun>(`/robot/runs/${id}`));
    } catch (e) { setError((e as ApiError).detail); } finally { setBusy(false); }
  }

  const steps = (run && Array.isArray(run.steps) ? run.steps : []) as RobotStep[];
  const icon = (s: RobotStep) => s.kind === "say" ? <Bot className="mt-0.5 size-4 shrink-0 text-brand" /> : s.kind === "tool" || s.kind === "result" ? <Wrench className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
    : s.kind === "ask" ? <ShieldCheck className="mt-0.5 size-4 shrink-0 text-amber-500" /> : s.kind === "approved" ? <Check className="mt-0.5 size-4 shrink-0 text-emerald-500" /> : s.kind === "denied" ? <CircleSlash className="mt-0.5 size-4 shrink-0 text-red-500" /> : <Check className="mt-0.5 size-4 shrink-0" />;

  return (
    <Card className="mb-6 animate-in fade-in slide-in-from-bottom-1 border-brand/30">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Bot className={`size-4 text-brand ${live ? "animate-pulse" : ""}`} />{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!connected ? (
          <p className="text-sm text-muted-foreground">{t("Connect an AI account first.")} <Link href="/advisor/" className="text-brand underline">{t("Open the advisor")}</Link></p>
        ) : (
          <>
            {!live && (
              <div className="flex flex-wrap items-center gap-3">
                <select value={autonomy} onChange={(e) => setAutonomy(e.target.value as "ask" | "auto")} aria-label={t("Autonomy")}
                  className="h-9 min-w-0 max-w-full rounded-md border bg-background px-2 text-sm">
                  <option value="ask">{t("Ask me before each change")}</option>
                  <option value="auto">{t("Fully automatic: change, deploy, check")}</option>
                </select>
                <Button onClick={start} disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <Bot />}{run ? t("Run again") : t("Fix with Claude")}</Button>
              </div>
            )}
            {live && <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground"><span className="flex items-center gap-2"><Loader2 className="size-4 animate-spin" />{run!.status === "waiting" ? t("waiting for you") : t("Claude is working…")}</span><Button variant="ghost" size="sm" onClick={() => post(`/robot/runs/${run!.id}/stop`)} disabled={busy}><Square />{t("Stop")}</Button></div>}
            {steps.length > 0 && (
              <ul ref={log} className="max-h-80 space-y-2 overflow-y-auto pr-1">
                {steps.map((s, i) => (
                  <li key={i} className={`flex animate-in fade-in gap-2.5 text-sm ${s.kind === "result" ? "pl-6 text-xs text-muted-foreground" : ""} ${s.kind === "end" ? "border-t pt-3 font-medium" : ""}`}>
                    {icon(s)}<span className="min-w-0 whitespace-pre-wrap break-words">{s.text}</span>
                  </li>
                ))}
              </ul>
            )}
            {run?.pending && (
              <div className="space-y-3 rounded-xl border border-amber-400/40 bg-amber-400/10 p-4">
                <div className="text-sm font-medium">{t("The robot asks your permission")}</div>
                <p className="break-words font-mono text-xs">{run.pending.detail}</p>
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => post(`/robot/runs/${run.id}/decision`, { approve: true })} disabled={busy}><Check />{t("Allow")}</Button>
                  <Button variant="outline" onClick={() => post(`/robot/runs/${run.id}/decision`, { approve: false })} disabled={busy}><X />{t("Refuse")}</Button>
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
