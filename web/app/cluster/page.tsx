"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ConfirmDialog, CopyCode, EmptyState, ErrorText, Input, Label, Notice, PageHeader, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface ServerInfo {
  name: string;
  version: string;
  address: string | null;
  cpus: number | null;
  load: number[];
  memory: { total_mb: number | null; available_mb: number | null };
  disk: { total_gb: number; free_gb: number } | null;
  uptime_s: number;
  apps: { id: string; name: string; type: string; domain: string | null; replica_of: string | null; status: string | null }[];
}
interface Peer { id: number; name: string; url: string; address: string | null; info: ServerInfo | null; error: string | null; latency_ms: number | null }
interface Overview { self: ServerInfo; servers: Peer[]; tokens: { id: number; name: string; created_at: string; last_used: string | null }[] }

/** A share of a whole: the value in text, the bar for the eye. */
function Meter({ label, used, total, unit }: { label: string; used: number; total: number; unit: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs"><span className="text-zinc-500">{label}</span><span className="tabular-nums">{used.toLocaleString()} / {total.toLocaleString()} {unit} · {pct} %</span></div>
      <div className="h-1.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-white/[0.08]">
        <div className={`h-full rounded-full ${pct > 90 ? "bg-rose-500" : pct > 75 ? "bg-amber-500" : "bg-indigo-500"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function uptime(s: number, t: ReturnType<typeof useT>) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
  return d ? t("{d} d {h} h", { d, h }) : t("{h} h", { h });
}

function Metrics({ info }: { info: ServerInfo }) {
  const t = useT();
  const mem = info.memory;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-3 text-sm">
        <div><div className="text-xs text-zinc-500">CPU</div><div className="tabular-nums">{info.load[0]?.toFixed(2)} <span className="text-xs text-zinc-500">/ {info.cpus} {t("cores")}</span></div></div>
        <div><div className="text-xs text-zinc-500">{t("Up for")}</div><div>{uptime(info.uptime_s, t)}</div></div>
        <div><div className="text-xs text-zinc-500">Mas</div><div className="mono text-xs">{info.version}</div></div>
      </div>
      {mem.total_mb && mem.available_mb !== null && <Meter label={t("Memory")} used={mem.total_mb - (mem.available_mb ?? 0)} total={mem.total_mb} unit="MB" />}
      {info.disk && <Meter label={t("Disk")} used={Math.round((info.disk.total_gb - info.disk.free_gb) * 10) / 10} total={info.disk.total_gb} unit="GB" />}
      <div className="flex flex-wrap gap-1.5">
        {info.apps.map((a) => <Badge key={a.id} tone={a.replica_of ? "violet" : a.status === "failed" ? "red" : "zinc"}>{a.name}{a.replica_of ? ` · ${t("copy of {name}", { name: a.replica_of })}` : ""}</Badge>)}
        {info.apps.length === 0 && <span className="text-xs text-zinc-500">{t("No app yet.")}</span>}
      </div>
    </div>
  );
}

function ThisServer({ info, onSaved }: { info: ServerInfo; onSaved: () => void }) {
  const t = useT();
  const [name, setName] = useState(info.name);
  const [address, setAddress] = useState(info.address ?? "");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  async function save() {
    setError(""); setMsg("");
    try {
      await api("/cluster/self", { method: "PUT", body: { name, address } });
      setMsg(t("Saved."));
      onSaved();
    } catch (e) { setError((e as ApiError).detail); }
  }
  return (
    <Card title={t("This server")} actions={info.address ? <Badge tone="emerald">{info.address}</Badge> : <Badge tone="amber">{t("no private address")}</Badge>}>
      <div className="space-y-5">
        <Metrics info={info} />
        <div className="grid gap-3 sm:grid-cols-2">
          <Label title={t("Name in the cluster")}><Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} /></Label>
          <Label title={t("Private address")} hint={t("Of the network between your servers (VPC, WireGuard, Tailscale…): copies of apps listen on it. Never a public address.")}>
            <Input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="10.0.0.2" className="mono" />
          </Label>
        </div>
        <div className="flex items-center gap-3"><Button variant="primary" onClick={save}>{t("Save")}</Button><Notice>{msg}</Notice></div>
        <ErrorText>{error}</ErrorText>
      </div>
    </Card>
  );
}

function JoinTokens({ tokens, onChange }: { tokens: Overview["tokens"]; onChange: () => void }) {
  const t = useT();
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState("");
  async function create() {
    setError("");
    try {
      setToken((await api<{ token: string }>("/cluster/tokens", { method: "POST", body: { name: "cluster" } })).token);
      onChange();
    } catch (e) { setError((e as ApiError).detail); }
  }
  async function revoke(id: number) {
    try { await api(`/cluster/tokens/${id}`, { method: "DELETE" }); onChange(); } catch (e) { setError((e as ApiError).detail); }
  }
  return (
    <Card title={t("Let another Mas drive this server")}>
      <div className="space-y-3 text-sm">
        <p className="text-zinc-500">{t("Create a join token here, then paste it on the Servers page of the other Mas. Whoever holds it can deploy code on this server: treat it like a root password.")}</p>
        {token ? (
          <div className="space-y-1"><CopyCode text={token} /><p className="text-xs text-amber-600 dark:text-amber-400">{t("Shown only once: copy it now.")}</p></div>
        ) : <Button onClick={create}>{t("Create a join token")}</Button>}
        {tokens.length > 0 && (
          <ul className="divide-y divide-zinc-100 dark:divide-white/[0.06]">
            {tokens.map((tk) => (
              <li key={tk.id} className="flex items-center justify-between gap-2 py-2">
                <span className="text-xs text-zinc-500">{t("Token #{id}", { id: tk.id })} · {t("created {when}", { when: timeAgo(tk.created_at) })}{tk.last_used ? ` · ${t("used {when}", { when: timeAgo(tk.last_used) })}` : ` · ${t("never used")}`}</span>
                <Button variant="ghost" className="!px-2 !py-1 text-xs" onClick={() => revoke(tk.id)}>{t("Revoke")}</Button>
              </li>
            ))}
          </ul>
        )}
        <ErrorText>{error}</ErrorText>
      </div>
    </Card>
  );
}

function AddServer({ onAdded }: { onAdded: () => void }) {
  const t = useT();
  const [url, setUrl] = useState("https://");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await api("/cluster/servers", { method: "POST", body: { url, token } });
      setUrl("https://"); setToken("");
      onAdded();
    } catch (err) { setError((err as ApiError).detail); } finally { setBusy(false); }
  }
  return (
    <Card title={t("Add a server")}>
      <form onSubmit={submit} className="space-y-3">
        <Label title={t("Address of its Mas dashboard")}><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://mas.server-2.example.com" required /></Label>
        <Label title={t("Its join token")}><Input value={token} onChange={(e) => setToken(e.target.value)} placeholder="masc_…" className="mono" required /></Label>
        <ErrorText>{error}</ErrorText>
        <Button variant="primary" disabled={busy}>{busy ? <Spinner /> : null}{t("Add to the cluster")}</Button>
      </form>
    </Card>
  );
}

function Cluster() {
  const t = useT();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [leaving, setLeaving] = useState<Peer | null>(null);
  const [notice, setNotice] = useState("");
  const load = useCallback(() => {
    api<Overview>("/cluster").then(setData).catch((e: ApiError) => setError(e.detail));
  }, []);
  useEffect(() => {
    load();
    const timer = setInterval(load, 20_000);
    return () => clearInterval(timer);
  }, [load]);

  async function leave(p: Peer) {
    setLeaving(null);
    try {
      const r = await api<{ notes: string[] }>(`/cluster/servers/${p.id}`, { method: "DELETE" });
      setNotice(r.notes.join(" · ") || t("{name} left the cluster.", { name: p.name }));
      load();
    } catch (e) { setError((e as ApiError).detail); }
  }

  return (
    <>
      <PageHeader eyebrow={t("Cluster")} title={t("Servers")}
        subtitle={t("Your Mas servers side by side. Tick servers in the Load balancing card of an app: Mas runs a copy there and spreads the requests.")} />
      <div className="mb-4 space-y-3"><ErrorText>{error}</ErrorText><Notice>{notice}</Notice></div>
      {!data && !error && <div className="glass shimmer h-64 rounded-2xl" />}
      {data && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <ThisServer info={data.self} onSaved={load} />
            {data.servers.length === 0 ? (
              <EmptyState title={t("No other server yet")}>{t("Install Mas on another server, create a join token there, then add it here.")}</EmptyState>
            ) : data.servers.map((p) => (
              <Card key={p.id} title={p.info?.name ?? p.name} actions={
                <span className="flex items-center gap-2">
                  {p.error ? <Badge tone="red">{t("unreachable")}</Badge> : <Badge tone="emerald">{p.latency_ms} ms</Badge>}
                  {p.address && <Badge>{p.address}</Badge>}
                  <Button variant="ghost" className="!px-2 !py-1 text-xs" onClick={() => setLeaving(p)}>{t("Remove")}</Button>
                </span>
              }>
                {p.error ? <ErrorText>{p.error}</ErrorText> : p.info && <Metrics info={p.info} />}
                <p className="mt-3 text-xs text-zinc-500"><a href={p.url} target="_blank" rel="noreferrer" className="hover:underline">{p.url} ↗</a></p>
              </Card>
            ))}
          </div>
          <div className="space-y-6">
            <AddServer onAdded={load} />
            <JoinTokens tokens={data.tokens} onChange={load} />
            <p className="text-xs text-zinc-500">{t("Load balancing applies to Node.js, Next.js and Python apps: open an app, then its Load balancing card.")} <Link href="/apps/" className="text-indigo-500 hover:underline">{t("Apps")} →</Link></p>
          </div>
        </div>
      )}
      <ConfirmDialog open={leaving !== null} title={t("Remove {name} from the cluster?", { name: leaving?.name ?? "" })}
        message={t("The copies of your apps on that server are removed, then nginx stops sending it requests at the next deploy.")}
        confirmLabel={t("Remove")} danger onConfirm={() => leaving && leave(leaving)} onCancel={() => setLeaving(null)} />
    </>
  );
}

export default function ClusterPage() {
  return (
    <Shell>
      <Cluster />
    </Shell>
  );
}
