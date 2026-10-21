"use client";

import { useCallback, useEffect, useState } from "react";
import { StackFields, type StackValues } from "@/components/StackFields";
import { Button, Card, CopyCode, ErrorText, Input, Spinner } from "@/components/ui";
import { api, ApiError, type StackTemplate } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Row { label: string; value: string; copy?: boolean; secret?: boolean; link?: boolean }
interface StackInfo {
  template: StackTemplate;
  options: StackValues;
  ports: Record<string, number>;
  folder: string;
  rows: Row[];
  dns: { type: string; name: string; value: string }[];
  notes: { text: string; vars: Record<string, string> }[];
}

function Secret({ value }: { value: string }) {
  const t = useT();
  const [shown, setShown] = useState(false);
  return (
    <span className="flex items-center gap-2">
      <code className="mono truncate text-xs">{shown ? value : "•".repeat(16)}</code>
      <button className="text-xs text-zinc-500 hover:underline" onClick={() => setShown(!shown)}>{shown ? t("hide") : t("reveal")}</button>
      {shown && <button className="text-xs text-zinc-500 hover:underline" onClick={() => navigator.clipboard?.writeText(value)}>{t("Copy")}</button>}
    </span>
  );
}

function Action({ appId, action }: { appId: string; action: StackTemplate["actions"][number] }) {
  const t = useT();
  const [values, setValues] = useState<StackValues>({});
  const [busy, setBusy] = useState(false);
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");

  async function run() {
    setBusy(true);
    setError("");
    setOutput("");
    try {
      const r = await api<{ output: string }>(`/apps/${encodeURIComponent(appId)}/stack/actions/${action.id}`, { method: "POST", body: { values } });
      setOutput(r.output);
      setValues({});
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="rounded-lg border border-zinc-200/80 p-3 dark:border-white/[0.06]">
      <summary className="cursor-pointer text-sm font-medium">{t(action.label)}</summary>
      <div className="mt-3 space-y-3">
        <StackFields fields={action.fields} values={values} onChange={setValues} />
        <ErrorText>{error}</ErrorText>
        {output && <CopyCode text={output} />}
        <Button onClick={run} disabled={busy}>{busy ? <Spinner /> : null}{t(action.label)}</Button>
      </div>
    </details>
  );
}

/** A ready-made service (mode "stack"): how to use it, its DNS records, controls and settings. */
export function StackCard({ appId, onDeployed }: { appId: string; onDeployed: (releaseId: number) => void }) {
  const t = useT();
  const [info, setInfo] = useState<StackInfo | null>(null);
  const [values, setValues] = useState<StackValues>({});
  const [ports, setPorts] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [logs, setLogs] = useState<string | null>(null);

  const load = useCallback(() => {
    api<StackInfo>(`/apps/${encodeURIComponent(appId)}/stack`).then((r) => {
      setInfo(r);
      setValues(r.options);
      setPorts(r.ports);
    }).catch((e: ApiError) => setError(e.detail));
  }, [appId]);
  useEffect(load, [load]);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy("");
    }
  }

  const control = (op: string) => run(op, async () => { await api(`/apps/${encodeURIComponent(appId)}/stack/${op}`, { method: "POST" }); });
  const showLogs = () => run("logs", async () => { setLogs((await api<{ text: string }>(`/apps/${encodeURIComponent(appId)}/stack/logs`)).text); });
  const save = () => run("save", async () => {
    const r = await api<{ release_id: number }>(`/apps/${encodeURIComponent(appId)}/stack`, { method: "PUT", body: { options: values, ports } });
    onDeployed(r.release_id);
    load();
  });

  if (!info) return error ? <Card title={t("Service")}><ErrorText>{error}</ErrorText></Card> : null;
  const editablePorts = info.template.ports.filter((p) => !p.fixed);

  return (
    <>
      <Card title={t(info.template.name)}>
        <div className="space-y-4 text-sm">
          {info.rows.length > 0 && (
            <dl className="space-y-2">
              {info.rows.map((r) => (
                <div key={r.label} className="space-y-0.5">
                  <dt className="text-xs text-zinc-500">{t(r.label)}</dt>
                  <dd className="min-w-0">
                    {r.secret ? <Secret value={r.value} />
                      : r.link ? <a href={r.value} target="_blank" rel="noreferrer" className="break-all text-indigo-500 hover:underline">{r.value} ↗</a>
                      : r.copy ? <CopyCode text={r.value} /> : <span className="break-all">{r.value}</span>}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {info.notes.map((n) => <p key={n.text} className="text-xs text-zinc-500">{t(n.text, n.vars)}</p>)}
          <div className="flex flex-wrap gap-2 border-t border-zinc-200/80 pt-3 dark:border-white/[0.06]">
            {(["restart", "stop", "start"] as const).map((op) => (
              <Button key={op} disabled={!!busy} onClick={() => control(op)}>{busy === op ? <Spinner /> : null}{op === "restart" ? t("Restart") : op === "stop" ? t("Stop") : t("Start")}</Button>
            ))}
            <Button variant="ghost" disabled={!!busy} onClick={showLogs}>{busy === "logs" ? <Spinner /> : null}{t("Logs")}</Button>
          </div>
          <ErrorText>{error}</ErrorText>
          {logs !== null && <pre className="mono max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-black/40 p-3 text-[11px] text-zinc-300">{logs || t("No output yet.")}</pre>}
        </div>
      </Card>

      {info.dns.length > 0 && (
        <Card title={t("DNS records")}>
          <div className="space-y-3 text-sm">
            <p className="text-xs text-zinc-500">{t("Add them at your domain's DNS provider. Mail is refused or marked as spam without them.")}</p>
            {info.dns.map((r) => (
              <div key={`${r.type}-${r.name}`} className="space-y-1">
                <div className="flex gap-2 text-xs"><span className="mono font-semibold">{r.type}</span><span className="mono break-all text-zinc-500">{r.name}</span></div>
                <CopyCode text={r.value} />
              </div>
            ))}
          </div>
        </Card>
      )}

      {info.template.actions.map((a) => <Action key={a.id} appId={appId} action={a} />)}

      {(info.template.fields.length > 0 || editablePorts.length > 0) && (
        <details className="glass rounded-2xl p-5">
          <summary className="cursor-pointer text-sm font-semibold">{t("Settings")}</summary>
          <div className="mt-4 space-y-4">
            <StackFields fields={info.template.fields} values={values} onChange={setValues} />
            {editablePorts.map((p) => (
              <label key={p.name} className="flex items-center gap-3 text-sm">
                <span className="text-zinc-500">{t(p.label || p.name)}</span>
                <Input type="number" className="max-w-[8rem]" value={ports[p.name] ?? p.container}
                  onChange={(e) => setPorts({ ...ports, [p.name]: Number(e.target.value) })} />
                <span className="text-xs text-zinc-500">/{p.protocol}</span>
              </label>
            ))}
            <div className="flex items-center gap-3">
              <Button variant="primary" disabled={!!busy} onClick={save}>{busy === "save" ? <Spinner /> : null}{t("Save and redeploy")}</Button>
              <span className="text-xs text-zinc-500">{t("Data in {folder}/data is kept.", { folder: info.folder })}</span>
            </div>
          </div>
        </details>
      )}
    </>
  );
}
