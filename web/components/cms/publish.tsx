"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, ExternalLink, Loader2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError, type CmsSite, type ReleaseStatus } from "@/lib/api";
import { useT } from "@/lib/i18n";

export type Saved = { unchanged: boolean; instant?: boolean; commit?: string | null }; // instant: online without a rebuild
type Phase = "saving" | "waiting" | ReleaseStatus;
type State = { phase: Phase; after: number; startedAt: number } | null;

/**
 * Runs a change (a commit) and follows the deployment it triggers.
 * `run` returns true when the change went through; the banner shows the progress.
 */
export function usePublisher(site: CmsSite | null) {
  const t = useT();
  const [state, setState] = useState<State>(null);
  const [error, setError] = useState("");
  const appId = site?.id ?? "";

  useEffect(() => {
    if (!state || !["waiting", "queued", "building"].includes(state.phase)) return;
    const timer = setInterval(async () => {
      try {
        const s = await api<CmsSite>(`/cms/sites/${encodeURIComponent(appId)}/status`);
        const last = s.last_release;
        if (last && last.id > state.after) setState({ ...state, phase: last.status });
        else if (Date.now() - state.startedAt > 120_000) setState({ ...state, phase: "live" }); // no deployment seen
      } catch {}
    }, 3000);
    return () => clearInterval(timer);
  }, [state, appId]);

  const run = useCallback(async (action: () => Promise<Saved>, doneMessage?: string): Promise<boolean> => {
    setError("");
    const after = site?.last_release?.id ?? 0;
    setState({ phase: "saving", after, startedAt: Date.now() });
    try {
      const r = await action();
      if (r.unchanged) {
        setState(null);
        toast(t("Nothing changed."));
      } else {
        setState({ phase: r.instant ? "live" : "waiting", after, startedAt: Date.now() });
        toast.success(doneMessage ?? t("Saved."));
      }
      return true;
    } catch (e) {
      setState(null);
      const detail = e instanceof ApiError ? e.detail : (e as Error).message;
      setError(detail);
      toast.error(detail);
      return false;
    }
  }, [site, t]);

  return { run, busy: state?.phase === "saving", state, error, clearError: () => setError("") };
}

export function PublishBanner({ state, site }: { state: State; site: CmsSite | null }) {
  const t = useT();
  if (!state || state.phase === "saving") return null;
  const failed = state.phase === "failed";
  const live = state.phase === "live";
  return (
    <div className="mb-4 flex items-center gap-3 rounded-md border bg-card px-4 py-3 text-sm">
      {failed ? <XCircle className="size-4 text-destructive" /> : live ? <CheckCircle2 className="size-4 text-emerald-500" /> : <Loader2 className="size-4 animate-spin text-muted-foreground" />}
      <span>
        {failed ? t("Saved, but the site could not be rebuilt. Your administrator can see why in Mas.")
          : live ? t("Your changes are online.") : t("Saved. Publishing your changes…")}
      </span>
      {live && site?.url && (
        <a href={site.url} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 text-primary hover:underline">
          {t("View")}<ExternalLink className="size-3.5" />
        </a>
      )}
    </div>
  );
}
