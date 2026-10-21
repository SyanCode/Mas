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
  role?: "primary" | "agent";
  cpus: number | null;
  load: number[];
  memory: { total_mb: number | null; available_mb: number | null };
  disk: { total_gb: number; free_gb: number } | null;
  uptime_s: number;
  apps: { id: string; name: string; type: string; domain: string | null; replica_of: string | null; status: string | null }[];
}
interface Peer { id: number; name: string; url: string; address: string | null; info: ServerInfo | null; error: string | null; latency_ms: number | null; down_for_s?: number | null; state_sent_at?: number | null }
interface Overview { self: ServerInfo; servers: Peer[]; tokens: { id: number; name: string; created_at: string; last_used: string | null }[] }

interface Traffic {
  at: number;
  apps: { id: string; name: string; domain: string; backends: { address: string; server: "self" | number | null; configured: boolean; requests_minute: number; requests_15min: number; errors_15min: number; avg_ms: number | null }[] }[];
}

/** Apps on the left, servers on the right: one link per app and server, as thick as its share of the
 *  requests of the last minute, moving while requests flow, red when that server answers errors. */
function ClusterMap({ data, traffic }: { data: Overview; traffic: Traffic | null }) {
  const t = useT();
  const servers = [
    { key: "self", name: data.self.name, address: data.self.address, up: true, load: data.self.load[0], cpus: data.self.cpus },
    ...data.servers.map((p) => ({ key: String(p.id), name: p.info?.name ?? p.name, address: p.address, up: !p.error, load: p.info?.load[0] ?? null, cpus: p.info?.cpus ?? null })),
  ];
  const external = Array.from(new Set((traffic?.apps ?? []).flatMap((a) => a.backends.filter((b) => b.server === null).map((b) => b.address.split(":")[0]))));
  const right = [...servers, ...external.map((h) => ({ key: `ext:${h}`, name: t("outside Mas"), address: h, up: true, load: null, cpus: null }))];
  const apps = traffic?.apps ?? [];
  const W = 1160, rowH = 64, top = 24;
  const H = Math.max(apps.length, right.length, 1) * rowH + top * 2;
  const leftY = (i: number) => top + (i + 0.5) * ((H - 2 * top) / Math.max(apps.length, 1));
  const rightY = (i: number) => top + (i + 0.5) * ((H - 2 * top) / Math.max(right.length, 1));
  const keyOf = (b: Traffic["apps"][number]["backends"][number]) => (b.server === null ? `ext:${b.address.split(":")[0]}` : String(b.server));
  const links = apps.flatMap((a, i) => {
    const per = new Map<string, { minute: number; errors: number; quarter: number; ms: number[]; addrs: string[] }>();
    for (const b of a.backends) {
      const k = keyOf(b);
      const cur = per.get(k) ?? { minute: 0, errors: 0, quarter: 0, ms: [], addrs: [] };
      cur.minute += b.requests_minute; cur.errors += b.errors_15min; cur.quarter += b.requests_15min;
      if (b.avg_ms !== null) cur.ms.push(b.avg_ms);
      cur.addrs.push(b.address);
      per.set(k, cur);
    }
    const total = Array.from(per.values()).reduce((n, v) => n + v.minute, 0);
    return Array.from(per.entries()).map(([k, v]) => ({ app: a, i, j: right.findIndex((r) => r.key === k), share: total ? v.minute / total : 0, ...v }))
      .filter((l) => l.j >= 0);
  });
  // links arriving at the same server: their labels are stacked instead of drawn on each other
  const arriving = (l: (typeof links)[number]) => links.filter((o) => o.j === l.j);
  if (!apps.length) return <p className="text-sm text-zinc-500">{t("No app behind nginx yet: the map shows the Node.js, Next.js and Python apps that have a domain.")}</p>;
  return (
    <div className="space-y-3">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={t("Cluster map")}>
        <style>{`:root { --mas-map-halo: #fff } .dark { --mas-map-halo: #0a0b10 } @keyframes mas-flow { to { stroke-dashoffset: -24; } } .mas-flow { stroke-dasharray: 6 6; animation: mas-flow linear infinite; }`}</style>
        {links.map((l) => {
          const x1 = 230, y1 = leftY(l.i), x2 = W - 230, y2 = rightY(l.j);
          const width = 1.5 + 6 * l.share;
          const bad = l.quarter > 0 && l.errors / l.quarter > 0.05;
          const mx = (x1 + x2) / 2;
          const d = `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
          // the label sits on the curve near the server (t = 0.8), where the links of one server spread out
          const tt = 0.8, u = 1 - tt;
          const lx = u ** 3 * x1 + 3 * u * u * tt * mx + 3 * u * tt * tt * mx + tt ** 3 * x2;
          const ly = u ** 3 * y1 + 3 * u * u * tt * y1 + 3 * u * tt * tt * y2 + tt ** 3 * y2;
          const label = `${l.app.name} → ${right[l.j].name} : ${l.minute} ${t("req/min")} (${Math.round(l.share * 100)} %), ${l.errors} ${t("errors in 15 min")}${l.ms.length ? `, ${Math.round(l.ms.reduce((a, b) => a + b, 0) / l.ms.length)} ms` : ""} · ${l.addrs.join(", ")}`;
          return (
            <g key={`${l.app.id}-${l.j}`}>
              <title>{label}</title>
              <path d={d} fill="none" strokeWidth={width + 8} stroke="transparent" />
              <path d={d} fill="none" strokeWidth={width} strokeLinecap="round" className={bad ? "stroke-rose-500/40" : "stroke-indigo-500/25"} />
              {l.minute > 0 && <path d={d} fill="none" strokeWidth={Math.max(1.5, width * 0.5)} strokeLinecap="round" className={`mas-flow ${bad ? "stroke-rose-400" : "stroke-indigo-300"}`} style={{ animationDuration: `${Math.max(0.4, 3 - Math.log10(l.minute + 1))}s` }} />}
              <text x={lx} y={ly - 7 + (arriving(l).indexOf(l) - (arriving(l).length - 1) / 2) * 15} textAnchor="middle" className={`text-[11px] tabular-nums ${bad ? "fill-rose-500" : "fill-zinc-500"}`}
                style={{ paintOrder: "stroke", stroke: "var(--mas-map-halo, #0a0b10)", strokeWidth: 4 }}>{l.minute ? `${Math.round(l.share * 100)} % · ${l.minute}/min` : bad ? t("errors") : "0/min"}</text>
            </g>
          );
        })}
        {apps.map((a, i) => (
          <g key={a.id} transform={`translate(10 ${leftY(i) - 22})`}>
            <rect width="220" height="44" rx="10" className="fill-white stroke-zinc-200 dark:fill-zinc-900 dark:stroke-white/10" />
            <text x="14" y="19" className="fill-zinc-900 text-[13px] font-medium dark:fill-zinc-100">{a.name}</text>
            <text x="14" y="34" className="fill-zinc-500 text-[11px]">{a.domain}</text>
          </g>
        ))}
        {right.map((s, j) => (
          <g key={s.key} transform={`translate(${W - 230} ${rightY(j) - 22})`}>
            <rect width="220" height="44" rx="10" className={s.up ? "fill-white stroke-zinc-200 dark:fill-zinc-900 dark:stroke-white/10" : "fill-rose-500/10 stroke-rose-500/50"} />
            <circle cx="16" cy="22" r="4" className={s.up ? "fill-emerald-500" : "fill-rose-500"} />
            <text x="28" y="19" className="fill-zinc-900 text-[13px] font-medium dark:fill-zinc-100">{s.name}{s.key === "self" ? ` (${t("this server")})` : ""}</text>
            <text x="28" y="34" className="fill-zinc-500 text-[11px]">{s.address ?? "—"}{s.load !== null ? ` · CPU ${s.load.toFixed(2)}/${s.cpus}` : ""}{!s.up ? ` · ${t("unreachable")}` : ""}</text>
          </g>
        ))}
      </svg>
      <p className="text-xs text-zinc-500">{t("Requests of the last minute, per app and server, from the logs of nginx on this server; refreshed every 5 s. Hover a link for its errors and response time.")}</p>
    </div>
  );
}

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
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await api("/cluster/servers", { method: "POST", body: { url, token } });
      setUrl(""); setToken("");
      onAdded();
    } catch (err) { setError((err as ApiError).detail); } finally { setBusy(false); }
  }
  return (
    <Card title={t("Add a server")}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-zinc-500">{t("On the other server, run once:")}</p>
        <CopyCode text="sudo mas agent init --address 10.0.0.2" />
        <p className="text-xs text-zinc-500">{t("It becomes an agent (no dashboard, almost no CPU or memory, listening on its private address only) and prints its address and join token. A Mas with its own dashboard can join too: use its https:// address and a token from its Servers page.")}</p>
        <Label title={t("Its address")}><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://10.0.0.2:7070" required /></Label>
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
  const [traffic, setTraffic] = useState<Traffic | null>(null);
  const load = useCallback(() => {
    api<Overview>("/cluster").then(setData).catch((e: ApiError) => setError(e.detail));
  }, []);
  useEffect(() => {
    const read = () => api<Traffic>("/cluster/traffic").then(setTraffic).catch(() => {});
    read();
    const timer = setInterval(read, 5_000);
    return () => clearInterval(timer);
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
          <div className="lg:col-span-3">
            <Card title={t("Cluster map")} actions={<Badge tone="emerald">{t("live")}</Badge>}>
              <ClusterMap data={data} traffic={traffic} />
            </Card>
          </div>
          <div className="space-y-6 lg:col-span-2">
            <ThisServer info={data.self} onSaved={load} />
            {data.servers.length === 0 ? (
              <EmptyState title={t("No other server yet")}>{t("Install Mas on another server, create a join token there, then add it here.")}</EmptyState>
            ) : data.servers.map((p) => (
              <Card key={p.id} title={p.info?.name ?? p.name} actions={
                <span className="flex items-center gap-2">
                  {p.error ? <Badge tone="red">{t("unreachable")}</Badge> : <Badge tone="emerald">{p.latency_ms} ms</Badge>}
                  {p.info?.role === "agent" && <Badge tone="violet">{t("agent")}</Badge>}
                  {p.state_sent_at && <span title={t("It has the state of this server: if this one is lost, run on it: sudo mas agent promote")}><Badge tone="sky">{t("can take over")}</Badge></span>}
                  {p.address && <Badge>{p.address}</Badge>}
                  <Button variant="ghost" className="!px-2 !py-1 text-xs" onClick={() => setLeaving(p)}>{t("Remove")}</Button>
                </span>
              }>
                {p.error ? (
                  <div className="space-y-2">
                    <ErrorText>{p.error}</ErrorText>
                    {p.down_for_s != null && <p className="text-xs text-zinc-500">{p.down_for_s >= 120 ? t("Silent for {n} min: its copies were moved to the other servers.", { n: Math.round(p.down_for_s / 60) }) : t("Silent for {n} s: after 2 minutes its copies move to another server.", { n: p.down_for_s })}</p>}
                  </div>
                ) : p.info && <Metrics info={p.info} />}
                <p className="mt-3 text-xs text-zinc-500"><a href={p.url} target="_blank" rel="noreferrer" className="hover:underline">{p.url} ↗</a></p>
              </Card>
            ))}
          </div>
          <div className="space-y-6">
            <AddServer onAdded={load} />
            <JoinTokens tokens={data.tokens} onChange={load} />
            <p className="text-xs text-zinc-500">{t("Load balancing applies to Node.js, Next.js and Python apps: open an app, then its Load balancing card.")} <Link href="/apps/" className="text-indigo-500 hover:underline">{t("Apps")} →</Link></p>
            <Card title={t("If this server is lost")}>
              <div className="space-y-2 text-sm text-zinc-500">
                <p>{t("This server sends its state (apps, servers, GitHub App, accounts) to every server of the cluster, encrypted. On one of them, run:")}</p>
                <CopyCode text="sudo mas agent promote" />
                <p>{t("It becomes the primary: the copies it runs become the apps, the others are built there, and it drives the remaining servers. Then point the domains to it. If this server comes back, the others refuse its orders.")}</p>
              </div>
            </Card>
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
