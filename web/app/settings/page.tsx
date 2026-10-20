"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Shell, useSession } from "@/components/Shell";
import { Badge, Button, Card, ConfirmDialog, CopyCode, ErrorText, Input, Label, Notice, PageHeader, Select, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo, type App, type CmsUser, type DnsStatus, type GitHubStatus, type ProviderInfo } from "@/lib/api";
import { useT } from "@/lib/i18n";

function PasswordForm() {
  const t = useT();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg("");
    setError("");
    if (next !== confirm) return setError(t("The two passwords differ."));
    try {
      await api("/account/password", { method: "POST", body: { current, new: next } });
      setCurrent(""); setNext(""); setConfirm("");
      setMsg(t("Password changed. Other sessions were signed out."));
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <Card title={t("Password")}>
      <form onSubmit={submit} className="max-w-sm space-y-3 text-sm">
        <Input type="password" placeholder={t("Current password")} value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
        <Input type="password" placeholder={t("New password (10+ characters)")} value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={10} required />
        <Input type="password" placeholder={t("Confirm new password")} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
        <ErrorText>{error}</ErrorText>
        {msg && <p className="text-emerald-700 dark:text-emerald-400">{msg}</p>}
        <Button variant="primary" type="submit">{t("Change password")}</Button>
      </form>
    </Card>
  );
}

interface Setup { secret: string; uri: string; qr_svg: string }

function TwoFactor({ enabled }: { enabled: boolean }) {
  const t = useT();
  const [step, setStep] = useState<"idle" | "password" | "scan" | "disable">("idle");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<Setup | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  function reset() {
    setStep("idle"); setPassword(""); setCode(""); setSetup(null); setError("");
  }

  async function start(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      setSetup(await api<Setup>("/account/totp/setup", { method: "POST", body: { password, code: enabled ? code : undefined } }));
      setCode("");
      setStep("scan");
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function enable(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/account/totp/enable", { method: "POST", body: { code } });
      reset();
      setDone(t("Two-factor authentication is on. You will be asked for a code at each sign-in."));
      setTimeout(() => window.location.reload(), 1500);
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function disable(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/account/totp/disable", { method: "POST", body: { password, code } });
      reset();
      window.location.reload();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  const codeInput = (
    <Input
      value={code}
      onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
      inputMode="numeric"
      autoComplete="one-time-code"
      placeholder={t("6-digit code")}
      className="mono tracking-[0.3em]"
      required
    />
  );

  return (
    <Card title={t("Two-factor authentication")} actions={enabled ? <Badge tone="emerald">{t("on")}</Badge> : <Badge tone="amber">{t("off")}</Badge>}>
      <div className="max-w-md space-y-3 text-sm">
        {done && <p className="text-emerald-700 dark:text-emerald-400">{done}</p>}

        {step === "idle" && (
          <>
            <p className="text-zinc-600 dark:text-zinc-400">
              {enabled
                ? t("Sign-in requires a code from your authenticator app. Lost your phone? Run `sudo mas user 2fa-reset` on the server.")
                : t("Protect the dashboard with a 6-digit code from Google Authenticator, Aegis, 1Password, Bitwarden…")}
            </p>
            {enabled ? (
              <div className="flex gap-2">
                <Button onClick={() => setStep("password")}>{t("Replace authenticator")}</Button>
                <Button variant="danger" onClick={() => setStep("disable")}>{t("Disable")}</Button>
              </div>
            ) : (
              <Button variant="primary" onClick={() => setStep("password")}>{t("Set up 2FA")}</Button>
            )}
          </>
        )}

        {step === "password" && (
          <form onSubmit={start} className="space-y-3">
            <p>{enabled ? t("Confirm with your password and the current code of your authenticator.") : t("Confirm your password to continue.")}</p>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus required />
            {enabled && codeInput}
            <ErrorText>{error}</ErrorText>
            <div className="flex gap-2"><Button variant="primary" type="submit">{t("Continue")}</Button><Button type="button" onClick={reset}>{t("Cancel")}</Button></div>
          </form>
        )}

        {step === "scan" && setup && (
          <form onSubmit={enable} className="space-y-3">
            <p>1. {t("Scan this QR code with your authenticator app.")}</p>
            <div className="inline-block rounded-md bg-white p-2" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
            <p className="text-xs text-zinc-500">
              {t("Or type this key:")} <code className="select-all break-all">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
            </p>
            <p>2. {t("Enter the code it shows.")}</p>
            {codeInput}
            <ErrorText>{error}</ErrorText>
            <div className="flex gap-2"><Button variant="primary" type="submit">{t("Enable")}</Button><Button type="button" onClick={reset}>{t("Cancel")}</Button></div>
          </form>
        )}

        {step === "disable" && (
          <form onSubmit={disable} className="space-y-3">
            <p>{t("Confirm with your password and a current code.")}</p>
            <Input type="password" placeholder={t("Password")} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            {codeInput}
            <ErrorText>{error}</ErrorText>
            <div className="flex gap-2"><Button variant="danger" type="submit">{t("Disable 2FA")}</Button><Button type="button" onClick={reset}>{t("Cancel")}</Button></div>
          </form>
        )}
      </div>
    </Card>
  );
}

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

interface Invite { id: number; apps: string[]; note: string | null; created_by: string; created_at: string; expires_at: number }

function InviteLinks({ apps }: { apps: App[] }) {
  const t = useT();
  const [invites, setInvites] = useState<Invite[]>([]);
  const [creating, setCreating] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
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
    const r = await api<{ path: string }>("/invites", { method: "POST", body: { apps: picked, note } });
    setLink(`${window.location.origin}${r.path}`);
    setCreating(false);
    setPicked([]);
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
      {creating && (
        <div className="space-y-2">
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("For whom? (e.g. Acme bakery)")} maxLength={100} />
          {apps.map((a) => (
            <label key={a.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={picked.includes(a.id)}
                onChange={(e) => setPicked(e.target.checked ? [...picked, a.id] : picked.filter((p) => p !== a.id))} />
              {a.name} <span className="text-xs text-zinc-500">{a.domain ?? a.repo}</span>
            </label>
          ))}
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
              {i.apps.map((a) => <Badge key={a} tone="sky">{appName(a)}</Badge>)}
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
  const [secret, setSecret] = useState<{ username: string; password: string } | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
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
      const r = await api<CmsUser & { password: string }>("/users", { method: "POST", body: { username } });
      setSecret({ username: r.username, password: r.password });
      setUsername("");
      setEditing(r.id);
      setPicked([]);
    });
  };
  const reset = (u: CmsUser) => run(`reset-${u.id}`, async () => {
    const r = await api<{ password: string }>(`/users/${u.id}/password`, { method: "POST" });
    setSecret({ username: u.username, password: r.password });
  });
  const saveApps = (u: CmsUser) => run(`apps-${u.id}`, async () => {
    await api(`/users/${u.id}/apps`, { method: "PUT", body: { apps: picked } });
    setEditing(null);
  });

  const editors = users.filter((u) => u.role === "editor");
  const admins = users.filter((u) => u.role === "admin");
  const appName = (id: string) => apps.find((a) => a.id === id)?.name ?? id;
  const linked = apps.filter((a) => a.installation_id);

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
                <span className="font-medium">{u.username}</span>
                <Badge tone={u.totp_enabled ? "emerald" : "zinc"}>{u.totp_enabled ? "2FA" : t("no 2FA")}</Badge>
                <span className="text-xs text-zinc-500">{t("since {when}", { when: timeAgo(u.created_at) })}</span>
                <div className="ml-auto flex flex-wrap gap-1">
                  <Button onClick={() => { setEditing(editing === u.id ? null : u.id); setPicked(u.apps); }}>{t("Sites")}</Button>
                  <Button variant="ghost" disabled={!!busy} onClick={() => reset(u)}>{busy === `reset-${u.id}` ? <Spinner /> : null}{t("Reset password")}</Button>
                  {u.totp_enabled && (
                    <Button variant="ghost" disabled={!!busy} onClick={() => run(`totp-${u.id}`, async () => { await api(`/users/${u.id}/totp/reset`, { method: "POST" }); })}>{t("Reset 2FA")}</Button>
                  )}
                  <Button variant="ghost" onClick={() => setRemoving(u)}>{t("Delete")}</Button>
                </div>
              </div>
              {editing === u.id ? (
                <div className="space-y-2 rounded-xl border border-zinc-200/80 p-3 dark:border-white/[0.06]">
                  {linked.map((a) => (
                    <label key={a.id} className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={picked.includes(a.id)}
                        onChange={(e) => setPicked(e.target.checked ? [...picked, a.id] : picked.filter((p) => p !== a.id))} />
                      {a.name} <span className="text-xs text-zinc-500">{a.domain ?? a.repo}</span>
                    </label>
                  ))}
                  {linked.length === 0 && <p className="text-sm text-zinc-500">{t("No app linked to GitHub yet.")}</p>}
                  <Button variant="primary" disabled={!!busy} onClick={() => saveApps(u)}>{busy === `apps-${u.id}` ? <Spinner /> : null}{t("Save access")}</Button>
                </div>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {u.apps.map((a) => <Badge key={a} tone="sky">{appName(a)}</Badge>)}
                  {u.apps.length === 0 && <span className="text-xs text-zinc-500">{t("No site yet.")}</span>}
                </div>
              )}
            </li>
          ))}
          {editors.length === 0 && <li className="py-3 text-sm text-zinc-500">{t("No editor yet.")}</li>}
        </ul>
        <form onSubmit={create} className="flex flex-wrap items-end gap-2">
          <div className="min-w-[14rem] flex-1">
            <Label title={t("New editor")} hint={t("Lowercase login name, for example the client company.")}>
              <Input value={username} onChange={(e) => setUsername(e.target.value.toLowerCase())} placeholder="acme" />
            </Label>
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

function Settings() {
  const t = useT();
  const session = useSession();
  if (session?.role === "editor") {
    return (
      <div className="space-y-4">
        <PageHeader eyebrow={t("Account")} title={t("Your account")} subtitle={t("Password and two-factor authentication.")} />
        <TwoFactor enabled={!!session?.totp_enabled} />
        <PasswordForm />
        <p className="text-xs text-zinc-500">{t("Signed in as {name}", { name: session?.username ?? "" })}</p>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <PageHeader eyebrow={t("Configuration")} title={t("Settings")} subtitle={t("Domains, GitHub, people and account security.")} />
      <DomainsCard />
      <GitHubCard />
      <PeopleCard />
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
