"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowUpCircle, CheckCircle2, Loader2, RefreshCw } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/cms/ui/dialog";
import { Badge, Button, Card } from "@/components/ui";
import { api, timeAgo, type ApiError, type UpdateProgress, type UpdateStatus } from "@/lib/api";
import { useT } from "@/lib/i18n";

const CHECK_EVERY = 30 * 60_000;

/** What the official repository has that this server does not (looked at once an hour on the server). */
export function useUpdate(enabled = true) {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const load = useCallback(async (force = false) => {
    setChecking(true);
    try { setStatus(await api<UpdateStatus>(`/update${force ? "?force=1" : ""}`)); } catch { /* the banner is optional */ } finally { setChecking(false); }
  }, []);
  useEffect(() => {
    if (!enabled) return;
    load();
    const timer = setInterval(() => load(), CHECK_EVERY);
    return () => clearInterval(timer);
  }, [enabled, load]);
  return { status, checking, reload: load };
}

type Phase = "confirm" | "running" | "done" | "failed";

/** Confirm, run mas self-update (the dashboard restarts meanwhile), and reload on the new version. */
export function UpdateDialog({ open, status, onClose }: { open: boolean; status: UpdateStatus; onClose: () => void }) {
  const t = useT();
  const [phase, setPhase] = useState<Phase>("confirm");
  const [log, setLog] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { if (open) { setPhase("confirm"); setLog(""); setError(""); } }, [open]);

  useEffect(() => {
    if (phase !== "running") return;
    let live = true;
    const tick = async () => {
      try {
        const p = await api<UpdateProgress>("/update/progress");
        if (!live) return;
        setLog(p.log);
        if (p.state === "done") {
          setPhase("done");
          setTimeout(() => window.location.reload(), 1500);
        } else if (p.state === "failed") setPhase("failed");
      } catch { /* Mas is restarting: ask again */ }
    };
    const timer = setInterval(tick, 2000);
    return () => { live = false; clearInterval(timer); };
  }, [phase]);

  const start = async () => {
    setError("");
    try { await api("/update", { method: "POST" }); setPhase("running"); } catch (e) { setError((e as ApiError).detail); }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && phase !== "running") onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{phase === "done" ? t("Mas is up to date") : phase === "failed" ? t("The update failed") : phase === "running" ? t("Updating Mas…") : t("Update Mas")}</DialogTitle>
          <DialogDescription>
            {phase === "confirm" && (status.unknown
              ? t("The official history changed: Mas will move to the latest version.")
              : t("{n} change(s) since this version.", { n: status.behind }))}
            {phase === "running" && t("The dashboard restarts during the update and may be unavailable for about a minute. Your apps keep running.")}
            {phase === "done" && t("Reloading the dashboard…")}
            {phase === "failed" && t("Nothing was lost: the previous version is still installed unless the log below says otherwise.")}
          </DialogDescription>
        </DialogHeader>

        {phase === "confirm" && (
          <>
            {status.commits.length > 0 && (
              <ul className="max-h-56 space-y-1.5 overflow-y-auto rounded-lg bg-muted/60 p-3 text-[13px]">
                {status.commits.map((c, i) => (
                  <li key={`${c.sha}${i}`} className="flex gap-2"><code className="shrink-0 text-xs text-muted-foreground">{c.sha}</code><span>{c.message}</span></li>
                ))}
              </ul>
            )}
            <p className="text-xs text-muted-foreground">{t("The update fetches the source, reinstalls, rebuilds the dashboard and restarts Mas. It stops if the source has local changes.")}</p>
            {error && <p className="text-sm text-red-500">{error}</p>}
          </>
        )}
        {phase !== "confirm" && (
          <pre className="max-h-64 min-h-24 overflow-auto rounded-lg bg-zinc-950 p-3 font-mono text-[11.5px] leading-relaxed text-zinc-300">{log || t("Starting…")}</pre>
        )}
        {phase === "running" && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />{t("In progress…")}</p>}
        {phase === "done" && <p className="flex items-center gap-2 text-sm text-emerald-600"><CheckCircle2 className="size-4" />{t("Updated.")}</p>}

        <DialogFooter>
          {phase === "confirm" && <><Button onClick={onClose}>{t("Cancel")}</Button><Button variant="primary" onClick={start}>{t("Update now")}</Button></>}
          {phase === "failed" && <Button onClick={onClose}>{t("Close")}</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** A slim line above the page when a newer version exists. */
export function UpdateBanner() {
  const t = useT();
  const { status } = useUpdate();
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  if (!status || status.behind === 0 || dismissed) return null;
  return (
    <>
      <div role="status" className="mb-6 flex flex-wrap items-center gap-3 rounded-xl border border-indigo-500/25 bg-indigo-500/[0.07] px-4 py-2.5 text-[13px]">
        <ArrowUpCircle className="size-4 shrink-0 text-indigo-500" />
        <span className="min-w-0 flex-1">
          <span className="font-medium">{status.latest_version && status.latest_version !== status.version ? t("Mas {v} is available", { v: status.latest_version }) : t("A new version of Mas is available")}</span>
          <span className="ml-2 text-zinc-500">{status.unknown ? t("history changed") : t("{n} change(s)", { n: status.behind })}</span>
        </span>
        <Button variant="primary" onClick={() => setOpen(true)} disabled={status.busy}>{t("Update")}</Button>
        <button type="button" onClick={() => setDismissed(true)} className="text-xs text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200">{t("Later")}</button>
      </div>
      <UpdateDialog open={open} status={status} onClose={() => setOpen(false)} />
    </>
  );
}

/** Settings → Updates: the version, a button to look now, and the update itself. */
export function UpdateCard() {
  const t = useT();
  const { status, checking, reload } = useUpdate();
  const [open, setOpen] = useState(false);
  const behind = status?.behind ?? 0;
  return (
    <Card title={t("Updates")} actions={status ? (behind > 0 ? <Badge tone="amber">{t("update available")}</Badge> : <Badge tone="emerald">{t("up to date")}</Badge>) : undefined}>
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-[14rem] flex-1 basis-56 text-sm">
          <div>Mas <span className="font-medium">v{status?.version ?? "…"}</span>{status?.sha && <code className="ml-2 text-xs text-zinc-500">{status.branch}@{status.sha}</code>}</div>
          <p className="mt-1 text-xs text-zinc-500">
            {status?.error ? status.error
              : behind > 0 ? (status?.unknown ? t("The official history changed: update to follow it.") : t("{n} change(s) are available.", { n: behind }))
              : status?.checked_at ? t("Checked {when}.", { when: timeAgo(new Date(status.checked_at * 1000).toISOString()) }) : t("Looking…")}
            {status?.restart_pending && ` ${t("A newer version is installed: restart Mas to run it.")}`}
          </p>
        </div>
        <Button onClick={() => reload(true)} disabled={checking}>{checking ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}{t("Check now")}</Button>
        {behind > 0 && status && <Button variant="primary" onClick={() => setOpen(true)} disabled={status.busy}>{t("Update")}</Button>}
      </div>
      {status && <UpdateDialog open={open} status={status} onClose={() => setOpen(false)} />}
    </Card>
  );
}
