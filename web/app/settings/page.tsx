"use client";

import { UpdateCard } from "@/components/Update";
import { useEffect, useState, type FormEvent } from "react";
import { Avatar, userAvatarUrl, shownName } from "@/components/Avatar";
import { Shell, useSession } from "@/components/Shell";
import { Badge, Button, Card, ConfirmDialog, CopyCode, ErrorText, Input, Label, Notice, PageHeader, Select, Spinner, Textarea } from "@/components/ui";
import { api, ApiError, startViewAs, timeAgo, type ApiToken, type App, type CmsUser, type DnsStatus, type GitHubStatus, type ProviderInfo } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { AiAccountsList } from "@/components/AiAccounts";
import { SitePermsPicker, usePermSummary, type SiteAccess } from "@/components/SitePermsPicker";
import Link from "next/link";
import { GitLabCard } from "@/components/GitLab";
import { AppearanceCard, PasswordForm, ProfileCard, TwoFactor } from "@/components/AccountCards";
function GitHubCard() {
  const t = useT();
  const [status, setStatus] = useState<GitHubStatus | null>(null);
  const [org, setOrg] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () => api<GitHubStatus>("/github/status").then(setStatus).catch((e: ApiError) => setError(e.detail));
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    if (code && state) {
      // Back from GitHub after creating the App (manifest flow).
      window.history.replaceState(null, "", "/settings/");
      api<{ slug: string }>("/github/manifest/complete", { method: "POST", body: { code, state } })
        .then((r) => setMsg(t("GitHub App “{slug}” created. Last step: install it on your repositories.", { slug: r.slug })))
        .catch((e: ApiError) => setError(e.detail))
        .finally(load);
    } else {
      load();
    }
  }, []);

  async function create() {
    setError("");
    try {
      const r = await api<{ action: string; manifest: string }>("/github/manifest", { method: "POST", body: { org: org || null } });
      const form = document.createElement("form");
      form.method = "post";
      form.action = r.action;
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "manifest";
      input.value = r.manifest;
      form.appendChild(input);
      document.body.appendChild(form);
      form.submit();
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  return (
    <Card title="GitHub" actions={status?.configured ? <Badge tone="emerald">{t("connected")}</Badge> : <Badge>{t("not connected")}</Badge>}>
      <div className="max-w-xl space-y-3 text-sm">
        {msg && <p className="text-emerald-700 dark:text-emerald-400">{msg}</p>}
        <ErrorText>{error}</ErrorText>
        {status && !status.configured && (
          <>
            <p className="text-zinc-600 dark:text-zinc-400">
              {t("Mas deploys from GitHub through a private GitHub App (access to the repositories you choose, push webhooks). One click creates it on your account; GitHub sends its keys back to Mas.")}
            </p>
            <label className="block space-y-1">
              <span className="text-zinc-500">{t("Organization (optional, empty = your personal account)")}</span>
              <Input value={org} onChange={(e) => setOrg(e.target.value.trim())} placeholder="my-org" className="max-w-xs" />
            </label>
            <Button variant="primary" onClick={create}>{t("Create GitHub App")}</Button>
          </>
        )}
        {status?.configured && (
          <>
            <p>
              {t("App {slug} (id {id}).", { slug: status.slug ?? "", id: status.app_id ?? "" })}{" "}
              {status.installations.length
                ? <>{t("Installed on {accounts}.", { accounts: status.installations.map((i) => i.account).join(", ") })}</>
                : <span className="text-amber-700 dark:text-amber-400">{t("Not installed on any account yet.")}</span>}
            </p>
            {status.install_url && (
              <a href={status.install_url} target="_blank" rel="noreferrer" className="inline-block rounded-md border border-zinc-300 px-3 py-1.5 font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
                {t("Install / choose repositories")} ↗
              </a>
            )}
            <p className="text-xs text-zinc-500">{t("Webhook URL:")} <code>{status.webhook_url}</code></p>
            {status.error && <ErrorText>{status.error}</ErrorText>}
          </>
        )}
      </div>
    </Card>
  );
}

function DomainsCard() {
  const t = useT();
  const [status, setStatus] = useState<DnsStatus | null>(null);
  const [adding, setAdding] = useState<ProviderInfo | null>(null);
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [ipv4, setIpv4] = useState("");
  const [ipv6, setIpv6] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api<DnsStatus>("/dns/status").then((s) => {
    setStatus(s);
    setIpv4(s.ipv4 ?? s.suggested_ipv4 ?? "");
    setIpv6(s.ipv6 ?? "");
  }).catch((e: ApiError) => setError(e.detail));
  useEffect(() => { load(); }, []);

  function start(p: ProviderInfo) {
    setAdding(p);
    setError("");
    setMsg("");
    setCreds(Object.fromEntries(p.fields.filter((f) => f.options.length).map((f) => [f.name, f.options[0]])));
  }

  async function connect(e: FormEvent) {
    e.preventDefault();
    if (!adding) return;
    setBusy(true);
    setError("");
    try {
      const r = await api<{ account: string; zones: string[] }>("/dns/providers", { method: "POST", body: { type: adding.type, credentials: creds } });
      setMsg(t("{provider} connected ({account}): {n} domain(s).", { provider: adding.label, account: r.account, n: r.zones.length }));
      setAdding(null);
      setCreds({});
      load();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  async function saveIps(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/dns/settings", { method: "PUT", body: { ipv4: ipv4 || null, ipv6: ipv6 || null } });
      setMsg(t("Saved: new DNS records will point to this address."));
      load();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function disconnect(type: string) {
    await api(`/dns/providers/${type}`, { method: "DELETE" }).catch(() => {});
    load();
  }

  const connected = new Set(status?.providers.map((p) => p.type));
  const tokenUrl = adding ? adding.token_urls[creds.endpoint ?? "default"] ?? Object.values(adding.token_urls)[0] : null;

  return (
    <Card title={t("Domains")} actions={<Badge tone={status?.providers.length ? "emerald" : "zinc"}>{t("{n} registrar(s)", { n: status?.providers.length ?? 0 })}</Badge>}>
      <div className="space-y-6 text-sm">
        <Notice>{msg}</Notice>
        <ErrorText>{error}</ErrorText>

        <form onSubmit={saveIps} className="space-y-2">
          <div className="font-medium">{t("This server's public address")}</div>
          <p className="text-xs text-zinc-500">
            {t("New domains point here.")}{" "}
            {status && !status.ipv4 && status.suggested_ipv4 && <>{t("Detected from your existing domains:")} <code>{status.suggested_ipv4}</code>.</>}
          </p>
          <div className="flex flex-wrap gap-2">
            <Input value={ipv4} onChange={(e) => setIpv4(e.target.value.trim())} placeholder="IPv4" className="mono max-w-[13rem]" />
            <Input value={ipv6} onChange={(e) => setIpv6(e.target.value.trim())} placeholder={t("IPv6 (optional)")} className="mono max-w-[17rem]" />
            <Button type="submit">{t("Save")}</Button>
          </div>
        </form>

        {status && status.providers.length > 0 && (
          <div className="space-y-2">
            <div className="font-medium">{t("Connected")}</div>
            {status.providers.map((p) => (
              <div key={p.type} className="flex items-center justify-between rounded-xl border border-zinc-200 px-3 py-2 dark:border-white/[0.08]">
                <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)]" />{p.label} <span className="text-xs text-zinc-500">{p.account}</span></span>
                <Button variant="ghost" onClick={() => disconnect(p.type)}>{t("Disconnect")}</Button>
              </div>
            ))}
          </div>
        )}

        <div className="space-y-2">
          <div className="font-medium">{t("Connect a registrar")}</div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {status?.catalog.map((p) => (
              <button
                key={p.type}
                type="button"
                onClick={() => start(p)}
                className={`glass rounded-xl px-3 py-3 text-left transition hover:border-indigo-400/50 ${adding?.type === p.type ? "ring-2 ring-indigo-500/50" : ""}`}
              >
                <div className="font-medium">{p.label}</div>
                <div className="text-xs text-zinc-500">{connected.has(p.type) ? t("connected · replace keys") : t("API token")}</div>
              </button>
            ))}
          </div>
        </div>

        {adding && (
          <form onSubmit={connect} className="space-y-3 rounded-xl border border-indigo-400/30 bg-indigo-500/[0.04] p-4">
            <div className="font-medium">{adding.label}</div>
            <p className="text-xs text-zinc-500">
              {adding.help}{" "}
              {tokenUrl && <a href={tokenUrl} target="_blank" rel="noreferrer" className="font-medium text-indigo-500 hover:underline">{t("Open the token page")} ↗</a>}
            </p>
            {adding.fields.map((f) => (
              <Label key={f.name} title={f.label}>
                {f.options.length ? (
                  <Select value={creds[f.name] ?? ""} onChange={(e) => setCreds({ ...creds, [f.name]: e.target.value })}>
                    {f.options.map((o) => <option key={o}>{o}</option>)}
                  </Select>
                ) : (
                  <Input type={f.secret ? "password" : "text"} value={creds[f.name] ?? ""} onChange={(e) => setCreds({ ...creds, [f.name]: e.target.value.trim() })} required autoComplete="off" />
                )}
              </Label>
            ))}
            <div className="flex gap-2">
              <Button variant="glow" type="submit" disabled={busy}>{busy ? <Spinner /> : null}{t("Verify & connect")}</Button>
              <Button type="button" variant="ghost" onClick={() => setAdding(null)}>{t("Cancel")}</Button>
            </div>
            <p className="text-xs text-zinc-500">{t("Keys are checked with {provider}, then stored encrypted on this server. Mas never deletes DNS records.", { provider: adding.label })}</p>
          </form>
        )}
      </div>
    </Card>
  );
}

function SignupSettings() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [saved, setSaved] = useState<{ open: boolean; code: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ open: boolean; code: string }>("/signup/settings").then((r) => {
      setOpen(r.open);
      setCode(r.code);
      setSaved(r);
    }).catch((e: ApiError) => setError(e.detail));
  }, []);

  async function save(next: { open: boolean; code: string }) {
    setBusy(true);
    setError("");
    try {
      setSaved(await api<{ open: boolean; code: string }>("/signup/settings", { method: "PUT", body: next }));
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  if (!saved) return <ErrorText>{error}</ErrorText>;
  const signupUrl = typeof window !== "undefined" ? `${window.location.origin}/signup/` : "/signup/";
  return (
    <div className="space-y-3 rounded-xl border border-zinc-200/80 p-4 dark:border-white/[0.06]">
      <label className="flex items-center gap-2 text-sm font-medium">
        <input type="checkbox" checked={open} disabled={busy} onChange={(e) => { setOpen(e.target.checked); save({ open: e.target.checked, code }); }} />
        {t("Open sign-up")}
        <Badge tone={saved.open ? "emerald" : "zinc"}>{saved.open ? t("open") : t("closed")}</Badge>
      </label>
      <p className="text-xs text-zinc-500">
        {t("Clients create their own editor account at {url}. A new account sees nothing until you give it a site below.", { url: signupUrl })}
      </p>
      {open && (
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[14rem] flex-1">
            <Label title={t("Invitation code (optional)")} hint={t("If set, only people you gave the code to can sign up.")}>
              <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder={t("e.g. acme-2026")} />
            </Label>
          </div>
          <Button className="mb-6" disabled={busy || code === saved.code} onClick={() => save({ open, code })}>{busy ? <Spinner /> : null}{t("Save code")}</Button>
        </div>
      )}
      {open && <CopyCode text={signupUrl} />}
      <ErrorText>{error}</ErrorText>
    </div>
  );
}

interface Invite { id: number; apps: string[]; access: SiteAccess | null; role: string; note: string | null; created_by: string; created_at: string; expires_at: number }

function InviteLinks({ apps }: { apps: App[] }) {
  const t = useT();
  const summary = usePermSummary();
  const [invites, setInvites] = useState<Invite[]>([]);
  const [creating, setCreating] = useState(false);
  const [picked, setPicked] = useState<SiteAccess>({});
  const [note, setNote] = useState("");
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = () => { api<Invite[]>("/invites").then(setInvites).catch((e: ApiError) => setError(e.detail)); };
  useEffect(load, []);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  const create = () => run(async () => {
    const r = await api<{ path: string }>("/invites", { method: "POST", body: { access: picked, note, role: "editor" } });
    setLink(`${window.location.origin}${r.path}`);
    setCreating(false);
    setPicked({});
    setNote("");
  });
  const appName = (id: string) => apps.find((a) => a.id === id)?.name ?? id;

  return (
    <div className="space-y-3 rounded-xl border border-zinc-200/80 p-4 dark:border-white/[0.06]">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{t("Invitation links")}</span>
        {!creating && <Button onClick={() => { setCreating(true); setLink(null); }}>{t("New link")}</Button>}
      </div>
      <p className="text-xs text-zinc-500">{t("A link works once, for 7 days, even when sign-up is closed. The person picks a username and a password and lands directly on the sites you chose.")}</p>
      <p className="text-xs text-zinc-500">{t("For each site, choose what the person may do: a client who only follows the visits gets Statistics only and cannot change anything.")}</p>
      {creating && (
        <div className="space-y-2">
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("For whom? (e.g. Acme bakery)")} maxLength={100} />
          <SitePermsPicker sites={apps.map((a) => ({ id: a.id, name: a.name, domain: a.domain ?? a.repo }))} value={picked} onChange={setPicked} />
          <div className="flex gap-2">
            <Button variant="primary" disabled={busy} onClick={create}>{busy ? <Spinner /> : null}{t("Create the link")}</Button>
            <Button variant="ghost" onClick={() => setCreating(false)}>{t("Cancel")}</Button>
          </div>
        </div>
      )}
      {link && (
        <div className="space-y-1.5 text-xs text-zinc-500">
          <div>{t("Send this link to the person. It is shown only now:")}</div>
          <CopyCode text={link} />
        </div>
      )}
      {invites.length > 0 && (
        <ul className="space-y-1.5 text-sm">
          {invites.map((i) => (
            <li key={i.id} className="flex flex-wrap items-center gap-2">
              <span>{i.note || t("Link #{id}", { id: i.id })}</span>
              {i.access
                ? Object.entries(i.access).map(([a, p]) => <Badge key={a} tone="sky">{appName(a)} · {summary(p)}</Badge>)
                : i.apps.map((a) => <Badge key={a} tone="sky">{appName(a)}{i.role === "author" ? ` · ${t("Drafts only")}` : ""}</Badge>)}
              <span className="text-xs text-zinc-500">{t("expires {date}", { date: new Date(i.expires_at * 1000).toLocaleDateString() })}</span>
              <button className="ml-auto text-xs text-rose-500 hover:underline" disabled={busy}
                onClick={() => run(async () => { await api(`/invites/${i.id}`, { method: "DELETE" }); })}>{t("Revoke")}</button>
            </li>
          ))}
        </ul>
      )}
      <ErrorText>{error}</ErrorText>
    </div>
  );
}

function PeopleCard() {
  const t = useT();
  const [users, setUsers] = useState<CmsUser[]>([]);
  const [apps, setApps] = useState<App[]>([]);
  const [username, setUsername] = useState("");
  const [newRole, setNewRole] = useState("editor");
  const [secret, setSecret] = useState<{ username: string; password: string } | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [picked, setPicked] = useState<SiteAccess>({});
  const summary = usePermSummary();
  const [removing, setRemoving] = useState<CmsUser | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = () => {
    api<CmsUser[]>("/users").then(setUsers).catch((e: ApiError) => setError(e.detail));
    api<App[]>("/apps").then(setApps).catch(() => {});
  };
  useEffect(load, []);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError("");
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy("");
    }
  }

  const create = (e: FormEvent) => {
    e.preventDefault();
    run("create", async () => {
      const r = await api<CmsUser & { password: string }>("/users", { method: "POST", body: { username, role: newRole } });
      setSecret({ username: r.username, password: r.password });
      setUsername("");
      if (newRole !== "viewer") { setEditing(r.id); setPicked({}); }
    });
  };
  const reset = (u: CmsUser) => run(`reset-${u.id}`, async () => {
    const r = await api<{ password: string }>(`/users/${u.id}/password`, { method: "POST" });
    setSecret({ username: u.username, password: r.password });
  });
  const saveApps = (u: CmsUser) => run(`apps-${u.id}`, async () => {
    const access: SiteAccess = Object.fromEntries(Object.keys(u.access ?? {}).map((a) => [a, []]));
    await api(`/users/${u.id}/access`, { method: "PUT", body: { access: { ...access, ...picked } } });
    setEditing(null);
  });

  const editors = users.filter((u) => u.role === "editor" || u.role === "author");
  const viewers = users.filter((u) => u.role === "viewer");
  const admins = users.filter((u) => u.role === "admin");
  const appName = (id: string) => apps.find((a) => a.id === id)?.name ?? id;
  const linked = apps.filter((a) => a.installation_id || a.source === "gitlab");

  return (
    <Card title={t("People")} actions={<Badge tone="violet">{t("{n} editor(s)", { n: editors.length })}</Badge>}>
      <div className="space-y-5">
        <p className="text-sm text-zinc-500">
          {t("Editors are client accounts: they only see the CMS of the sites you give them and can change the content the site declares in mas.cms.json, nothing else.")}
        </p>
        <SignupSettings />
        <InviteLinks apps={linked} />
        <ErrorText>{error}</ErrorText>
        {secret && (
          <div className="space-y-2 rounded-xl border border-emerald-400/30 bg-emerald-400/5 p-4 text-sm">
            <div>{t("Password for {name}, shown once. Send it with the dashboard address; they can change it in Account.", { name: secret.username })}</div>
            <CopyCode text={secret.password} />
            <button className="text-xs text-zinc-500 hover:underline" onClick={() => setSecret(null)}>{t("Done")}</button>
          </div>
        )}
        <ul className="divide-y divide-zinc-200/80 dark:divide-white/[0.06]">
          {editors.map((u) => (
            <li key={u.id} className="space-y-3 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Avatar name={shownName(u)} src={userAvatarUrl(u)} size={28} />
                <span className="font-medium">{shownName(u)}</span>
                {shownName(u) !== u.username && <span className="text-xs text-zinc-500">@{u.username}</span>}
                <Badge tone={u.totp_enabled ? "emerald" : "zinc"}>{u.totp_enabled ? "2FA" : t("no 2FA")}</Badge>
                <span className="text-xs text-zinc-500">{t("since {when}", { when: timeAgo(u.created_at) })}</span>
                <div className="ml-auto flex flex-wrap gap-1">
                  <Button onClick={() => { setEditing(editing === u.id ? null : u.id); setPicked(u.access ?? {}); }}>{t("Sites")}</Button>
                  <Button variant="ghost" onClick={() => startViewAs(u)}>{t("View as this person")}</Button>
                  <Button variant="ghost" disabled={!!busy} onClick={() => reset(u)}>{busy === `reset-${u.id}` ? <Spinner /> : null}{t("Reset password")}</Button>
                  {u.totp_enabled && (
                    <Button variant="ghost" disabled={!!busy} onClick={() => run(`totp-${u.id}`, async () => { await api(`/users/${u.id}/totp/reset`, { method: "POST" }); })}>{t("Reset 2FA")}</Button>
                  )}
                  <Button variant="ghost" onClick={() => setRemoving(u)}>{t("Delete")}</Button>
                </div>
              </div>
              {editing === u.id ? (
                <div className="space-y-2 rounded-xl border border-zinc-200/80 p-3 dark:border-white/[0.06]">
                  <SitePermsPicker sites={linked.map((a) => ({ id: a.id, name: a.name, domain: a.domain ?? a.repo }))} value={picked} onChange={setPicked} />
                  <Button variant="primary" disabled={!!busy} onClick={() => saveApps(u)}>{busy === `apps-${u.id}` ? <Spinner /> : null}{t("Save access")}</Button>
                </div>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {u.apps.map((a) => <Badge key={a} tone="sky">{appName(a)} · {summary(u.access?.[a] ?? [])}</Badge>)}
                  {u.apps.length === 0 && <span className="text-xs text-zinc-500">{t("No site yet.")}</span>}
                </div>
              )}
            </li>
          ))}
          {editors.length === 0 && <li className="py-3 text-sm text-zinc-500">{t("No editor yet.")}</li>}
        </ul>
        {viewers.length > 0 && (
          <div className="space-y-2">
            <p className="text-sm font-medium">{t("Read-only accounts")}</p>
            <ul className="divide-y divide-zinc-200/80 dark:divide-white/[0.06]">
              {viewers.map((u) => (
                <li key={u.id} className="flex flex-wrap items-center gap-2 py-2">
                  <Avatar name={shownName(u)} src={userAvatarUrl(u)} size={28} />
                  <span className="font-medium">{shownName(u)}</span>
                  {shownName(u) !== u.username && <span className="text-xs text-zinc-500">@{u.username}</span>}
                  <Badge tone={u.totp_enabled ? "emerald" : "zinc"}>{u.totp_enabled ? "2FA" : t("no 2FA")}</Badge>
                  <div className="ml-auto flex flex-wrap gap-1">
                    <Button variant="ghost" disabled={!!busy} onClick={() => reset(u)}>{t("Reset password")}</Button>
                    {u.totp_enabled && <Button variant="ghost" disabled={!!busy} onClick={() => run(`totp-${u.id}`, async () => { await api(`/users/${u.id}/totp/reset`, { method: "POST" }); })}>{t("Reset 2FA")}</Button>}
                    <Button variant="ghost" onClick={() => setRemoving(u)}>{t("Delete")}</Button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
        <form onSubmit={create} className="flex flex-wrap items-end gap-2">
          <div className="min-w-[14rem] flex-1">
            <Label title={t("New editor")} hint={t("Lowercase login name, for example the client company.")}>
              <Input value={username} onChange={(e) => setUsername(e.target.value.toLowerCase())} placeholder="acme" />
            </Label>
          </div>
          <div className="mb-6">
            <Select value={newRole} onChange={(e) => setNewRole(e.target.value)} aria-label={t("Role")}>
              <option value="editor">{t("Editor (CMS of their sites)")}</option>
              <option value="viewer">{t("Read-only (the whole dashboard, no change, no secret)")}</option>
            </Select>
          </div>
          <Button variant="primary" className="mb-6" disabled={!username || busy === "create"}>{busy === "create" ? <Spinner /> : null}{t("Create")}</Button>
        </form>
        <p className="text-xs text-zinc-500">{t("Administrators ({names}) are managed with mas user on the server.", { names: admins.map((a) => a.username).join(", ") })}</p>
      </div>
      <ConfirmDialog
        open={!!removing}
        title={t("Delete {name}?", { name: removing?.username ?? "" })}
        message={t("The account and its access are removed. The content it published stays in the repositories.")}
        confirmLabel={t("Delete")}
        danger
        onConfirm={() => { const u = removing!; setRemoving(null); run(`delete-${u.id}`, async () => { await api(`/users/${u.id}`, { method: "DELETE" }); }); }}
        onCancel={() => setRemoving(null)}
      />
    </Card>
  );
}

function AdvisorCard() {
  const t = useT();
  return (
    <Card title={t("AI accounts")} actions={<Link href="/advisor/" className="text-xs text-indigo-500 hover:underline">{t("Open the advisor")}</Link>}>
      <p className="mb-4 text-sm text-zinc-500">{t("Connect Claude Code tokens, Codex or other providers: an AI proposes improvements for your sites (GDPR, SEO, style) and can make them and deploy by itself. Several accounts: when one reaches its limit the next one takes over.")}</p>
      <AiAccountsList />
    </Card>
  );
}

function ClaudeCard({ totp }: { totp: boolean }) {
  const t = useT();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("claude");
  const [scope, setScope] = useState<"read" | "write" | "create">("write");
  const [repos, setRepos] = useState<string | null>(null);
  const [days, setDays] = useState(90);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const endpoint = typeof window === "undefined" ? "/mcp" : `${window.location.origin}/mcp`;

  const load = () => {
    api<{ enabled: boolean; allowed_repos: string[] }>("/mcp").then((r) => {
      setEnabled(r.enabled);
      setRepos((current) => current ?? r.allowed_repos.join("\n"));
    }).catch((e: ApiError) => setError(e.detail));
    api<ApiToken[]>("/tokens").then(setTokens).catch(() => {});
  };
  useEffect(load, []);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError("");
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy("");
    }
  }

  const toggle = () => run("toggle", async () => {
    await api("/mcp", { method: "PUT", body: { enabled: !enabled } });
  });

  const connect = (e: FormEvent) => {
    e.preventDefault();
    run("create", async () => {
      const r = await api<{ token: string }>("/tokens", {
        method: "POST", body: { name, scope, days, password, code: totp ? code : undefined },
      });
      if (!enabled) await api("/mcp", { method: "PUT", body: { enabled: true } });
      setCreated(r.token);
      setOpen(false); setPassword(""); setCode("");
    });
  };

  const command = (token: string) =>
    `claude mcp add --transport http mas ${endpoint} --header "Authorization: Bearer ${token}"`;
  const expiry = (k: ApiToken) => k.expires_at
    ? t("expires {date}", { date: new Date(k.expires_at * 1000).toLocaleDateString() })
    : t("never expires");

  return (
    <Card title={t("Claude")} actions={
      enabled === null ? <Spinner /> : (
        <div className="flex items-center gap-2">
          <Badge tone={enabled ? "emerald" : "zinc"}>{enabled ? t("MCP on") : t("MCP off")}</Badge>
          <Button variant="ghost" disabled={!!busy} onClick={toggle}>{busy === "toggle" ? <Spinner /> : null}{enabled ? t("Turn off") : t("Turn on")}</Button>
        </div>
      )}>
      <div className="space-y-4 text-sm">
        <p className="text-zinc-500">
          {t("Let Claude Code (or any MCP client) look after this server: it reads services and logs, restarts, deploys and rolls back, through Mas. Every action goes into the history under the token's name. Accounts, environment variables and credentials stay out of reach.")}
        </p>
        <ErrorText>{error}</ErrorText>
        {created && (
          <div className="space-y-2 rounded-xl border border-emerald-400/30 bg-emerald-400/5 p-4">
            <div>{t("Token shown once. Run this command on your computer to add the server to Claude Code:")}</div>
            <CopyCode text={command(created)} />
            <div className="text-xs text-zinc-500">{t("Then ask Claude, for example: what is broken on the server?")}</div>
            <button className="text-xs text-zinc-500 hover:underline" onClick={() => setCreated(null)}>{t("Done")}</button>
          </div>
        )}
        {!open ? (
          <Button variant="primary" onClick={() => { setOpen(true); setCreated(null); }}>{t("Connect Claude")}</Button>
        ) : (
          <form onSubmit={connect} className="max-w-md space-y-3 rounded-xl border border-zinc-200/80 p-4 dark:border-white/[0.06]">
            <Label title={t("Name")} hint={t("Shown in the history, for example claude-laptop.")}>
              <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required />
            </Label>
            <Label title={t("Access")}>
              <Select value={scope} onChange={(e) => setScope(e.target.value as "read" | "write" | "create")}>
                <option value="write">{t("Read and act (restart, deploy, roll back)")}</option>
                <option value="create">{t("Read, act and create apps (allowed repositories)")}</option>
                <option value="read">{t("Read only (status, logs, health)")}</option>
              </Select>
            </Label>
            <Label title={t("Expires")}>
              <Select value={days} onChange={(e) => setDays(Number(e.target.value))}>
                <option value={30}>{t("in 30 days")}</option>
                <option value={90}>{t("in 90 days")}</option>
                <option value={365}>{t("in a year")}</option>
                <option value={0}>{t("never")}</option>
              </Select>
            </Label>
            <Input type="password" placeholder={t("Your password")} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            {totp && <Input inputMode="numeric" placeholder={t("Two-factor code")} value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" required />}
            <div className="flex gap-2">
              <Button variant="primary" type="submit" disabled={!!busy}>{busy === "create" ? <Spinner /> : null}{t("Create the token")}</Button>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t("Cancel")}</Button>
            </div>
          </form>
        )}
        {enabled && <p className="text-xs text-zinc-500">{t("Endpoint")}: <span className="mono">{endpoint}</span></p>}
        <div className="max-w-md space-y-2">
          <Label title={t("Repositories agents may create apps from")} hint={t("One per line: Owner/Repo, or Owner/* for all of an account. Empty: no creation. Variables only a human may fill (secrets) keep the app waiting.")}>
            <Textarea rows={3} value={repos ?? ""} onChange={(e) => setRepos(e.target.value)} placeholder="SyanCode/Indiepolis" />
          </Label>
          <Button disabled={!!busy || repos === null} onClick={() => run("repos", async () => {
            const r = await api<{ allowed_repos: string[] }>("/mcp", { method: "PUT", body: { allowed_repos: (repos ?? "").split(/\s+/).filter(Boolean) } });
            setRepos(r.allowed_repos.join("\n"));
          })}>{busy === "repos" ? <Spinner /> : null}{t("Save")}</Button>
        </div>
        <ul className="divide-y divide-zinc-200/80 dark:divide-white/[0.06]">
          {tokens.map((k) => (
            <li key={k.id} className="flex flex-wrap items-center gap-2 py-2.5">
              <span className="font-medium">{k.name}</span>
              <Badge tone={k.scope === "create" ? "violet" : k.scope === "write" ? "amber" : "sky"}>
                {k.scope === "create" ? t("creates apps") : k.scope === "write" ? t("read and act") : t("read only")}
              </Badge>
              <span className="text-xs text-zinc-500">
                {k.last_used ? t("used {when}", { when: timeAgo(k.last_used) }) : t("never used")} · {expiry(k)}
              </span>
              <Button variant="ghost" className="ml-auto" disabled={!!busy} onClick={() => setRevoking(k)}>{t("Revoke")}</Button>
            </li>
          ))}
          {tokens.length === 0 && <li className="py-2.5 text-zinc-500">{t("No token yet.")}</li>}
        </ul>
      </div>
      <ConfirmDialog
        open={!!revoking}
        title={t("Revoke {name}?", { name: revoking?.name ?? "" })}
        message={t("Whatever uses this token loses access right away.")}
        confirmLabel={t("Revoke")}
        danger
        onConfirm={() => { const k = revoking!; setRevoking(null); run(`revoke-${k.id}`, async () => { await api(`/tokens/${k.id}`, { method: "DELETE" }); }); }}
        onCancel={() => setRevoking(null)}
      />
    </Card>
  );
}

interface Remote { enabled: boolean; endpoint: string; region: string; bucket: string; prefix: string; access_key: string; has_secret: boolean }

function RemoteCopy() {
  const t = useT();
  const [r, setR] = useState<Remote | null>(null);
  const [secret, setSecret] = useState("");
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { api<Remote>("/self-backup/remote").then(setR).catch(() => {}); }, []);
  if (!r) return null;
  const set = (patch: Partial<Remote>) => setR({ ...r, ...patch });

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(""); setMsg("");
    try {
      setR(await api<Remote>("/self-backup/remote", { method: "PUT", body: { ...r, secret_key: secret || undefined } }));
      setSecret("");
      setMsg(t("Saved."));
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }
  async function test() {
    setError(""); setMsg("");
    try {
      await api("/self-backup/remote/test", { method: "POST" });
      setMsg(t("Test file written to the storage."));
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <div className="space-y-2 border-t border-zinc-200/80 pt-3 dark:border-white/[0.06]">
      <button className="flex items-center gap-2 font-medium" onClick={() => setOpen(!open)}>
        {t("Copy outside the server (S3)")} {r.enabled ? <Badge tone="emerald">{t("on")}</Badge> : <Badge tone="zinc">{t("off")}</Badge>}
      </button>
      {open && (
        <form onSubmit={save} className="space-y-2">
          <p className="text-zinc-600 dark:text-zinc-400">{t("Each backup is also sent to a storage compatible with S3 (AWS, Scaleway, OVH, Backblaze, MinIO…). The secret key is kept encrypted and never shown again.")}</p>
          <label className="flex items-center gap-2"><input type="checkbox" checked={r.enabled} onChange={(e) => set({ enabled: e.target.checked })} />{t("Send the backups")}</label>
          <Input placeholder="https://s3.fr-par.scw.cloud" value={r.endpoint} onChange={(e) => set({ endpoint: e.target.value })} />
          <div className="flex gap-2">
            <Input placeholder={t("Region (fr-par)")} value={r.region} onChange={(e) => set({ region: e.target.value })} />
            <Input placeholder={t("Bucket")} value={r.bucket} onChange={(e) => set({ bucket: e.target.value })} />
          </div>
          <Input placeholder={t("Folder (mas/)")} value={r.prefix} onChange={(e) => set({ prefix: e.target.value })} />
          <Input placeholder={t("Access key")} value={r.access_key} onChange={(e) => set({ access_key: e.target.value })} autoComplete="off" />
          <Input type="password" placeholder={r.has_secret ? t("Secret key set (type a new one to replace it)") : t("Secret key")} value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" />
          <ErrorText>{error}</ErrorText>
          {msg && <p className="text-emerald-700 dark:text-emerald-400">{msg}</p>}
          <div className="flex gap-2"><Button variant="primary" type="submit">{t("Save")}</Button><Button type="button" onClick={test}>{t("Test the storage")}</Button></div>
        </form>
      )}
    </div>
  );
}

function SelfBackupCard() {
  const t = useT();
  const [auto, setAuto] = useState(true);
  const [dir, setDir] = useState("");
  const [backups, setBackups] = useState<{ name: string; size: number; modified: number }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const apply = (r: { auto: boolean; directory: string; backups: { name: string; size: number; modified: number }[] }) => {
    setAuto(r.auto); setDir(r.directory); setBackups(r.backups);
  };
  useEffect(() => { api<Parameters<typeof apply>[0]>("/self-backup").then(apply).catch(() => {}); }, []);

  async function call(method: "PUT" | "POST", body?: object) {
    setBusy(true); setError("");
    try {
      apply(await api<Parameters<typeof apply>[0]>("/self-backup", { method, body }));
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={t("Backup of Mas")}>
      <div className="max-w-md space-y-3 text-sm">
        <p className="text-zinc-600 dark:text-zinc-400">{t("The database, the secret key and the configuration of Mas, in one private archive on this server ({dir}). It holds the keys that decrypt your secrets: copy it somewhere else too (sudo mas backup create -o FOLDER).", { dir })}</p>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={auto} disabled={busy} onChange={(e) => call("PUT", { auto: e.target.checked })} />
          {t("Back up every night (the last 7 are kept)")}
        </label>
        <ErrorText>{error}</ErrorText>
        <Button variant="primary" disabled={busy} onClick={() => call("POST")}>{busy ? <Spinner /> : null}{t("Back up now")}</Button>
        {backups.length > 0 && (
          <ul className="space-y-1 text-xs text-zinc-500">
            {backups.slice(0, 7).map((b) => <li key={b.name}>{b.name} · {Math.max(1, Math.round(b.size / 1024))} KiB</li>)}
          </ul>
        )}
        <RemoteCopy />
      </div>
    </Card>
  );
}

function HostAlertsCard() {
  const t = useT();
  const [enabled, setEnabled] = useState(false);
  const [emails, setEmails] = useState("");
  const [webhook, setWebhook] = useState("");
  const [hasWebhook, setHasWebhook] = useState(false);
  const [percent, setPercent] = useState(90);
  const [services, setServices] = useState(true);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ enabled: boolean; emails: string[]; disk_percent: number; services: boolean; has_webhook: boolean }>("/host-alerts").then((r) => {
      setServices(r.services); setEnabled(r.enabled); setEmails(r.emails.join(", ")); setPercent(r.disk_percent); setHasWebhook(r.has_webhook);
    }).catch(() => {});
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(""); setMsg("");
    try {
      const r = await api<{ has_webhook: boolean }>("/host-alerts", {
        method: "PUT",
        body: { enabled, emails: emails.split(/[,\s]+/).filter(Boolean), disk_percent: percent, services, webhook: webhook || undefined },
      });
      setHasWebhook(r.has_webhook); setWebhook("");
      setMsg(t("Saved."));
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function test() {
    setError(""); setMsg("");
    try {
      const r = await api<{ sent: boolean }>("/host-alerts/test", { method: "POST" });
      setMsg(r.sent ? t("Test sent.") : t("Nobody could be reached: check the mail server and the webhook."));
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <Card title={t("Server alerts")} actions={enabled ? <Badge tone="emerald">{t("on")}</Badge> : <Badge tone="zinc">{t("off")}</Badge>}>
      <form onSubmit={save} className="max-w-md space-y-3 text-sm">
        <p className="text-zinc-600 dark:text-zinc-400">{t("Get told once when the disk is nearly full, a certificate expires or nginx is broken, and once when it is fixed. Uses the mail server of the notifications.")}</p>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          {t("Send server alerts")}
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={services} onChange={(e) => setServices(e.target.checked)} />
          {t("Also tell me when a service that was running stops")}
        </label>
        <Input placeholder={t("E-mail addresses, separated by commas")} value={emails} onChange={(e) => setEmails(e.target.value)} />
        <Input type="url" placeholder={hasWebhook ? t("Webhook set (type a new address to replace it)") : t("Webhook https:// address (Slack, Discord…)")} value={webhook} onChange={(e) => setWebhook(e.target.value)} />
        <Label title={t("Disk alert from {n}% full", { n: percent })}>
          <input type="range" min={50} max={99} value={percent} onChange={(e) => setPercent(Number(e.target.value))} className="w-full" />
        </Label>
        <ErrorText>{error}</ErrorText>
        {msg && <p className="text-emerald-700 dark:text-emerald-400">{msg}</p>}
        <div className="flex gap-2"><Button variant="primary" type="submit">{t("Save")}</Button><Button type="button" onClick={test}>{t("Send a test")}</Button></div>
      </form>
    </Card>
  );
}

function Settings() {
  const t = useT();
  const session = useSession();
  if (session && session.role !== "admin") {
    return (
      <div className="space-y-4">
        <PageHeader eyebrow={t("Account")} title={t("Your account")} subtitle={t("Password and two-factor authentication.")} />
        <ProfileCard />
        <AppearanceCard />
        <TwoFactor enabled={!!session?.totp_enabled} />
        <PasswordForm />
        <p className="text-xs text-zinc-500">{t("Signed in as {name}", { name: session?.username ?? "" })}</p>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <PageHeader eyebrow={t("Configuration")} title={t("Settings")} subtitle={t("Domains, GitHub, people and account security.")} />
      <ProfileCard />
      <AppearanceCard />
      <UpdateCard />
      <DomainsCard />
      <GitHubCard />
      <GitLabCard />
      <PeopleCard />
      <AdvisorCard />
      <HostAlertsCard />
      <SelfBackupCard />
      <ClaudeCard totp={!!session?.totp_enabled} />
      <TwoFactor enabled={!!session?.totp_enabled} />
      <PasswordForm />
      <p className="text-xs text-zinc-500">Mas {session?.version} · {t("signed in as {name}", { name: session?.username ?? "" })}</p>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <Shell>
      <Settings />
    </Shell>
  );
}
