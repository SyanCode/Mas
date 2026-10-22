"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Button, ErrorText, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

interface Item { key: string; kind: string; name: string; target: string; project?: string | null; status: string; detail: string }
interface State { running: boolean; started_at: number | null; finished_at: number | null; items: Item[]; plan?: Item[] }

const KINDS: Record<string, string> = {
  container: msg("container"), unit: msg("system service"), pm2: "PM2", app: msg("Mas app"), nginx: "nginx",
};
const STATUS: Record<string, { label: string; tone: "zinc" | "sky" | "emerald" | "amber" | "red" }> = {
  pending: { label: msg("waiting"), tone: "zinc" }, running: { label: msg("restarting"), tone: "sky" },
  identical: { label: msg("identical"), tone: "emerald" }, changed: { label: msg("changed"), tone: "amber" },
  failed: { label: msg("failed"), tone: "red" }, skipped: { label: msg("skipped"), tone: "zinc" },
};

// the details of a service restarted identical (the others come as they are, in English)
msg("same container, image, variables, volumes and ports"); msg("same container, image, variables, volumes and ports; health check passed");
msg("same unit file, command and environment"); msg("pm2 restart (same process definition)"); msg("configuration tested, then reloaded");

/** Restart every running service at once, exactly as it was (nothing rebuilt or recreated). */
export function RestartAll() {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<State | null>(null);
  const [skip, setSkip] = useState<string[]>([]);
  const [started, setStarted] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => api<State>("/restart-all").then(setState).catch((e) => setError((e as ApiError).detail)), []);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    load();
  }, [open, load]);
  useEffect(() => {  // follow the progress
    if (!open || !state?.running) return;
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, [open, state?.running, load]);

  async function go() {
    setError("");
    try {
      setState(await api<State>("/restart-all", { method: "POST", body: { skip } }));
      setStarted(true);
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  const showing = state?.running || started ? state?.items ?? [] : state?.plan ?? [];
  const done = started && state && !state.running;
  const bad = (state?.items ?? []).filter((i) => i.status === "failed" || i.status === "changed").length;
  return (
    <>
      <Button onClick={() => { setStarted(false); setSkip([]); setOpen(true); }}>{t("Restart everything")}</Button>
      <dialog ref={ref} onCancel={() => setOpen(false)}
        className="panel m-auto w-[min(94vw,40rem)] rounded-2xl p-0 text-zinc-900 backdrop:bg-black/60 backdrop:backdrop-blur-sm dark:text-zinc-100">
        <div className="space-y-4 p-6">
          <h3 className="text-base font-semibold">{t("Restart every service")}</h3>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            {t("Each running service restarts as it is: containers keep their image, variables, volumes and ports (nothing is recreated), units and PM2 apps their configuration. What is stopped stays stopped. Mas compares the configuration before and after each restart.")}
          </p>
          {!state && !error && <div className="glass shimmer h-32 rounded-xl" />}
          <ul className="max-h-[50vh] divide-y divide-zinc-100 overflow-y-auto text-sm dark:divide-white/[0.06]">
            {showing.map((i) => {
              const s = STATUS[i.status] ?? STATUS.pending;
              const planning = !started && !state?.running;
              return (
                <li key={i.key} className="flex items-start justify-between gap-3 py-2">
                  <label className="flex min-w-0 items-start gap-2">
                    {planning && <input type="checkbox" className="mt-1" checked={!skip.includes(i.key)}
                      onChange={(e) => setSkip(e.target.checked ? skip.filter((k) => k !== i.key) : [...skip, i.key])} />}
                    <span className="min-w-0">
                      <span className="font-medium">{i.name}</span>
                      <span className="ml-2 text-xs text-zinc-500">{t(KINDS[i.kind] ?? i.kind)}{i.project ? ` · ${i.project}` : ""}</span>
                      {i.detail && <span className="block text-xs text-zinc-500">{t(i.detail)}</span>}
                    </span>
                  </label>
                  {!planning && <Badge tone={s.tone}>{i.status === "running" ? <Spinner /> : null}{t(s.label)}</Badge>}
                </li>
              );
            })}
            {state && showing.length === 0 && <li className="py-2 text-zinc-500">{t("Nothing is running.")}</li>}
          </ul>
          {done && <p className={`text-sm ${bad ? "text-amber-600" : "text-emerald-600"}`}>
            {bad ? t("Done: {n} service(s) to check.", { n: bad }) : t("Done: every service restarted identical.")}</p>}
          <ErrorText>{error}</ErrorText>
          <div className="flex justify-end gap-2 pt-1">
            <Button onClick={() => setOpen(false)}>{state?.running ? t("Hide") : t("Close")}</Button>
            {!started && !state?.running && (
              <Button variant="primary" disabled={!state || showing.length === skip.length} onClick={go}>
                {t("Restart {n} service(s)", { n: showing.length - skip.length })}
              </Button>
            )}
          </div>
        </div>
      </dialog>
    </>
  );
}
